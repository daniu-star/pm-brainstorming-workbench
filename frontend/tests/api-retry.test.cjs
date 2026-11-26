"use strict";

/**
 * 第 41 批热修：瞬时网络故障自动重试（2026-11-26 线上故障回归）。
 *
 * 线上现象：HF Space 前置代理约 5%~10% 的请求会在 ~21 秒后直接断开（curl 000，
 * 直连 Space 同样复现），浏览器 fetch 抛 TypeError: Failed to fetch，页面显示
 * 「读取模型配置失败：网络连接失败」或英文「Failed to fetch」，用户完全进不去。
 *
 * 这里锁定的行为：
 *  - GET 遇到 TypeError / 503 会自动重试（第 42 批起最多 4 次，首跳 12s 超时）；
 *  - 写请求默认不重试，但带幂等语义的命令（sendCommand）显式开启重试，且
 *    三次尝试复用同一个 Idempotency-Key（后端按 request_id 去重）；
 *  - 重试耗尽后抛出 NETWORK_ERROR，文案中文且带「已自动重试」说明；
 *  - 外部 signal 取消时立刻停止，不再继续重试。
 */

const { test, afterEach } = require("node:test");
const assert = require("node:assert/strict");

const api = require("../node_modules/.cache/test-build/lib/api.js");
const { apiV2, sendCommand, errorMessage, isEndpointUnavailable, isTransientFailure, ApiError } = api;

const realFetch = globalThis.fetch;
let calls = [];

function jsonResponse(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => "application/json" },
    async json() {
      return body;
    },
    async text() {
      return JSON.stringify(body);
    },
  };
}

function installFetch(handler) {
  calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init });
    return handler(calls.length, url, init);
  };
}

afterEach(() => {
  globalThis.fetch = realFetch;
  calls = [];
});

test("GET 连续两次传输层失败后成功：自动重试到第 3 次并返回结果", async () => {
  installFetch((n) => {
    if (n < 3) throw new TypeError("Failed to fetch");
    return jsonResponse(200, { id: "s1", session_version: 1 });
  });

  const view = await apiV2("/api/v2/sessions/s1");

  assert.equal(view.id, "s1");
  assert.equal(calls.length, 3);
});

test("GET 先 503 再 200：网关抖动会被重试吸收", async () => {
  installFetch((n) => {
    if (n === 1) return jsonResponse(503, { error: { code: "INTERNAL", message: "gateway" } });
    return jsonResponse(200, { id: "s2" });
  });

  const view = await apiV2("/api/v2/sessions/s2");

  assert.equal(view.id, "s2");
  assert.equal(calls.length, 2);
});

test("写请求默认不重试：一次 TypeError 即抛 NETWORK_ERROR", async () => {
  installFetch(() => {
    throw new TypeError("Failed to fetch");
  });

  await assert.rejects(
    () => apiV2("/api/v2/sessions", { method: "POST", body: { problem_statement: "x" } }),
    (error) => {
      assert.ok(error instanceof ApiError);
      assert.equal(error.code, "NETWORK_ERROR");
      assert.match(errorMessage(error), /网络连接失败/);
      return true;
    },
  );
  assert.equal(calls.length, 1);
});

test("sendCommand（带幂等键）重试时复用同一个 Idempotency-Key", async () => {
  installFetch((n) => {
    if (n < 3) throw new TypeError("Load failed");
    return jsonResponse(202, { run_id: "r1", session_version: 2 });
  });

  const receipt = await sendCommand("s1", "clarify", { target: "coach" });

  assert.equal(receipt.run_id, "r1");
  assert.equal(calls.length, 3);
  const keys = calls.map((call) => call.init.headers["Idempotency-Key"]);
  assert.equal(new Set(keys).size, 1, "三次尝试必须复用同一个幂等键，否则后端会建出多个 Run");
  assert.ok(keys[0].length >= 8);
});

test("重试耗尽后的 TypeError 归一为中文网络错误，不再透出英文 Failed to fetch", async () => {
  installFetch(() => {
    throw new TypeError("Failed to fetch");
  });

  let caught = null;
  try {
    await apiV2("/api/v2/quota");
  } catch (error) {
    caught = error;
  }

  assert.ok(caught instanceof ApiError);
  assert.equal(caught.code, "NETWORK_ERROR");
  assert.equal(caught.retryable, true);
  assert.equal(errorMessage(caught), api.NETWORK_ERROR_MESSAGE);
  assert.doesNotMatch(errorMessage(caught), /Failed to fetch/i);
  assert.equal(calls.length, api.TRANSIENT_ATTEMPTS, "GET 应尝试满 TRANSIENT_ATTEMPTS 次才放弃");
});

test("外部 signal 取消后不再继续重试", async () => {
  const controller = new AbortController();
  installFetch(() => {
    controller.abort();
    throw new TypeError("Failed to fetch");
  });

  await assert.rejects(() => apiV2("/api/v2/quota", { signal: controller.signal }));
  assert.equal(calls.length, 1);
});

test("isTransientFailure / isEndpointUnavailable 的判定边界", () => {
  assert.equal(isTransientFailure(new ApiError("NETWORK_ERROR", "x", 0, true)), true);
  assert.equal(isTransientFailure(new ApiError("TIMEOUT", "x", 0, true)), true);
  assert.equal(isTransientFailure(new ApiError("INTERNAL", "x", 502)), true);
  assert.equal(isTransientFailure(new ApiError("INTERNAL", "x", 503)), true);
  assert.equal(isTransientFailure(new ApiError("VERSION_CONFLICT", "x", 409)), false);
  assert.equal(isTransientFailure(new ApiError("NOT_FOUND", "x", 404)), false);
  // 网络抖动不等于端点不存在：不能因此把会话永久降级到 legacy
  assert.equal(isEndpointUnavailable(new TypeError("Failed to fetch")), false);
  assert.equal(isEndpointUnavailable(new ApiError("INTERNAL", "x", 503)), false);
  assert.equal(isEndpointUnavailable(new ApiError("NOT_FOUND", "x", 404)), true);
});

test("重试期间通过 subscribeRetryNotices 广播进度，恢复后广播 null", async () => {
  const notices = [];
  const unsubscribe = api.subscribeRetryNotices((notice) => notices.push(notice));
  try {
    installFetch((n) => {
      if (n < 4) throw new TypeError("Failed to fetch");
      return jsonResponse(200, { ok: true });
    });

    const result = await apiV2("/api/v2/quota");

    assert.deepEqual(result, { ok: true });
    assert.equal(calls.length, 4);
    const attempts = notices.filter(Boolean).map((notice) => notice.attempt);
    assert.deepEqual(attempts, [1, 2, 3], "每次重试前都要通知页面，用户才看得到「正在重试 2/4」");
    assert.ok(notices.some((notice) => notice && notice.path === "/api/v2/quota"));
    assert.equal(notices[notices.length - 1], null, "恢复后必须广播 null，让横幅自动收起");
  } finally {
    unsubscribe();
  }
});

test("重试全部失败时也会广播 null，避免横幅卡在「正在重试」", async () => {
  const notices = [];
  const unsubscribe = api.subscribeRetryNotices((notice) => notices.push(notice));
  try {
    installFetch(() => {
      throw new TypeError("Failed to fetch");
    });
    await assert.rejects(() => apiV2("/api/v2/quota"));
    assert.equal(notices[notices.length - 1], null);
  } finally {
    unsubscribe();
  }
});

test("未重试的写请求使用「不会自动重放」文案，不谎称已重试", async () => {
  installFetch(() => {
    throw new TypeError("Failed to fetch");
  });

  let caught = null;
  try {
    await apiV2("/api/v2/llm-config", { method: "PUT", body: { api_key: "sk-x" } });
  } catch (error) {
    caught = error;
  }

  assert.ok(caught instanceof ApiError);
  assert.equal(caught.code, "NETWORK_ERROR");
  assert.equal(errorMessage(caught), api.NETWORK_ERROR_UNRETRIED_MESSAGE);
  assert.doesNotMatch(errorMessage(caught), /已自动重试/);
  assert.equal(calls.length, 1);
});

test("首跳超时早于网关 21 秒断点", () => {
  assert.ok(api.FIRST_ATTEMPT_TIMEOUT_MS <= 12_000);
  assert.ok(api.TRANSIENT_ATTEMPTS >= 4);
});
