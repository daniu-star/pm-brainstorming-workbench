"use strict";

/**
 * v2 API 客户端的单元测试（mock fetch，无网络）。
 *
 * 覆盖：
 *  - 错误体映射（{error:{code,message,retryable,request_id,details}}）与旧 detail 回退
 *  - ApiError.code/status/retryable/details/requestId 字段
 *  - 写方法自动生成 Idempotency-Key（且与 body.request_id 一致）
 *  - sendCommand 的 body 形状（request_id/action/expected_session_version/payload）
 *  - GET 查询串（version / product_version / before_seq）
 *  - 网络异常 → NETWORK_ERROR 且 retryable
 *  - isEndpointUnavailable 判定（404 回落 legacy 的判定依据）
 */

const { test, beforeEach, afterEach } = require("node:test");
const assert = require("node:assert/strict");

const api = require("../node_modules/.cache/test-build/lib/api.js");
const {
  ApiError,
  isApiError,
  statusMessage,
  errorMessage,
  isEndpointUnavailable,
  errorFromResponse,
  newRequestId,
  apiV2,
  createSession,
  getSession,
  getCanvas,
  getProductState,
  listMessages,
  sendCommand,
  cancelRun,
  retryFailed,
  deleteSession,
  runEventsUrl,
  CODE_MESSAGES,
} = api;

const realFetch = globalThis.fetch;
let calls = [];

function jsonResponse(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
}

function installFetch(handler) {
  globalThis.fetch = async (url, init) => {
    const call = { url: String(url), init: init || {} };
    calls.push(call);
    return handler(call);
  };
}

beforeEach(() => {
  calls = [];
});

afterEach(() => {
  globalThis.fetch = realFetch;
});

// --------------------------------------------------------------------------- //
// errorFromResponse / ApiError
// --------------------------------------------------------------------------- //

test("errorFromResponse 解析标准错误体", () => {
  const error = errorFromResponse(409, {
    error: {
      code: "VERSION_CONFLICT",
      message: "session version mismatch",
      retryable: false,
      request_id: "req-9",
      details: { expected: 3, actual: 4 },
    },
  });
  assert.ok(error instanceof ApiError);
  assert.equal(error.code, "VERSION_CONFLICT");
  assert.equal(error.status, 409);
  assert.equal(error.retryable, false);
  assert.equal(error.requestId, "req-9");
  assert.deepEqual(error.details, { expected: 3, actual: 4 });
});

test("errorFromResponse 回退旧后端的 detail 字符串（保留 .payload.detail 兼容）", () => {
  const error = errorFromResponse(400, { detail: "输入过长" });
  assert.equal(error.message, "输入过长");
  assert.equal(error.status, 400);
  assert.equal(error.payload.detail, "输入过长");
});

test("errorFromResponse 对未知 5xx 标记 retryable", () => {
  const error = errorFromResponse(503, null);
  assert.equal(error.code, "INTERNAL");
  assert.equal(error.retryable, true);
  assert.equal(error.status, 503);
});

test("statusFallbackCode 映射：401/403/404/410/422/429", () => {
  assert.equal(errorFromResponse(401, null).code, "UNAUTHENTICATED");
  assert.equal(errorFromResponse(403, null).code, "FORBIDDEN");
  assert.equal(errorFromResponse(404, null).code, "NOT_FOUND");
  assert.equal(errorFromResponse(410, null).code, "EVENT_CURSOR_EXPIRED");
  assert.equal(errorFromResponse(422, null).code, "INVALID_REQUEST");
  assert.equal(errorFromResponse(429, null).code, "QUEUE_LIMIT_EXCEEDED");
});

test("statusMessage 中文映射保持不变（既有页面依赖）", () => {
  assert.equal(statusMessage(401), "请先登录");
  assert.equal(statusMessage(403), "没有权限");
  assert.equal(statusMessage(404), "资源不存在");
  assert.equal(statusMessage(500), "服务暂不可用，请稍后重试");
  assert.equal(statusMessage(418), "请求失败（418）");
});

test("errorMessage 优先使用中文错误码文案", () => {
  const conflict = new ApiError("VERSION_CONFLICT", "raw message", 409);
  assert.equal(errorMessage(conflict), CODE_MESSAGES.VERSION_CONFLICT);
  assert.match(errorMessage(conflict), /内容已在别处更新/);
});

test("errorMessage 对网络错误给出中文提示", () => {
  const network = new ApiError("NETWORK_ERROR", "Failed to fetch", 0, true);
  assert.equal(errorMessage(network), "网络连接失败，请检查网络后重试");
});

test("isApiError 正确区分 ApiError 与普通 Error", () => {
  assert.equal(isApiError(new ApiError("NOT_FOUND", "x", 404)), true);
  assert.equal(isApiError(new Error("x")), false);
});

test("isEndpointUnavailable 只认端点确实不存在（404/405/501）与网络错误", () => {
  assert.equal(isEndpointUnavailable(new ApiError("NOT_FOUND", "x", 404)), true);
  assert.equal(isEndpointUnavailable(new ApiError("INTERNAL", "x", 501)), true);
  assert.equal(isEndpointUnavailable(new TypeError("Failed to fetch")), true);
  assert.equal(isEndpointUnavailable(new ApiError("VERSION_CONFLICT", "x", 409)), false);
  // 410/502/503 是「暂时不可用」而非「端点缺失」：不得据此永久降级到 legacy
  assert.equal(isEndpointUnavailable(new ApiError("EVENT_CURSOR_EXPIRED", "x", 410)), false);
  assert.equal(isEndpointUnavailable(new ApiError("INTERNAL", "x", 502)), false);
  assert.equal(isEndpointUnavailable(new ApiError("FEATURE_NOT_READY", "x", 503)), false);
});

test("newRequestId 返回非空且唯一", () => {
  const a = newRequestId();
  const b = newRequestId();
  assert.equal(typeof a, "string");
  assert.ok(a.length >= 8);
  assert.notEqual(a, b);
});

// --------------------------------------------------------------------------- //
// 写方法：Idempotency-Key 与 body 形状
// --------------------------------------------------------------------------- //

test("createSession 发送 problem_statement 并携带 Idempotency-Key", async () => {
  installFetch(() => jsonResponse(201, { id: "s1", session_version: 1 }));

  await createSession("做一个面向独立开发者的记账工具");

  assert.equal(calls.length, 1);
  const { url, init } = calls[0];
  assert.match(url, /\/api\/v2\/sessions$/);
  assert.equal(init.method, "POST");
  assert.equal(init.credentials, "include");
  const body = JSON.parse(init.body);
  // 创建会话的请求体是后端 StrictModel（extra=forbid），只能带 problem_statement；
  // 幂等通过 Idempotency-Key 头传递。
  assert.deepEqual(Object.keys(body), ["problem_statement"]);
  assert.equal(body.problem_statement, "做一个面向独立开发者的记账工具");
  assert.equal(typeof init.headers["Idempotency-Key"], "string");
  assert.ok(init.headers["Idempotency-Key"].length >= 8);
});

test("sendCommand 的 body 包含 request_id/action/expected_session_version/payload", async () => {
  installFetch(() =>
    jsonResponse(202, {
      run_id: "r1",
      session_id: "s1",
      action: "confirm_brief",
      status: "queued",
      session_version: 4,
      base_product_version: 2,
      events_url: "/api/v2/runs/r1/events",
      reused: false,
      audit_id: null,
    }),
  );

  await sendCommand(
    "s1",
    "confirm_brief",
    { expected_product_version: 2 },
    { expectedSessionVersion: 3 },
  );

  const { url, init } = calls[0];
  assert.match(url, /\/api\/v2\/sessions\/s1\/commands$/);
  assert.equal(init.method, "POST");
  const body = JSON.parse(init.body);
  assert.equal(body.action, "confirm_brief");
  assert.equal(body.expected_session_version, 3);
  assert.deepEqual(body.payload, { expected_product_version: 2 });
  assert.equal(typeof body.request_id, "string");
  assert.equal(init.headers["Idempotency-Key"], body.request_id);
});

test("sendCommand 允许显式传入 requestId（重试时保持幂等）", async () => {
  installFetch(() => jsonResponse(202, { run_id: "r1", reused: true }));

  await sendCommand("s1", "clarify", { content: "hi" }, { requestId: "fixed-request-id-1" });

  const body = JSON.parse(calls[0].init.body);
  assert.equal(body.request_id, "fixed-request-id-1");
  assert.equal(calls[0].init.headers["Idempotency-Key"], "fixed-request-id-1");
});

test("sendCommand 未给 expectedSessionVersion 时显式发送 null（后端不接受缺省推算）", async () => {
  installFetch(() => jsonResponse(202, { run_id: "r1" }));
  await sendCommand("s1", "clarify", { content: "x" });
  const body = JSON.parse(calls[0].init.body);
  assert.equal(body.expected_session_version, null);
});

test("deleteSession 发送 DELETE + Idempotency-Key + expected_session_version", async () => {
  installFetch(() => jsonResponse(200, { session_id: "s1", deleted_at: "2026-01-01T00:00:00Z" }));

  await deleteSession("s1", { expectedSessionVersion: 7 });

  const { url, init } = calls[0];
  assert.match(url, /\/api\/v2\/sessions\/s1$/);
  assert.equal(init.method, "DELETE");
  const body = JSON.parse(init.body);
  assert.equal(body.expected_session_version, 7);
  assert.ok(init.headers["Idempotency-Key"]);
});

test("cancelRun / retryFailed 都是写方法且带幂等键", async () => {
  installFetch((call) =>
    call.url.includes("/retry-failed")
      ? jsonResponse(202, { run_id: "r2" })
      : jsonResponse(200, { id: "r1", status: "cancel_requested" }),
  );

  await cancelRun("r1");
  await retryFailed("r1", { expectedSessionVersion: 5 });

  assert.match(calls[0].url, /\/api\/v2\/runs\/r1\/cancel$/);
  assert.match(calls[1].url, /\/api\/v2\/runs\/r1\/retry-failed$/);
  for (const call of calls) {
    assert.equal(call.init.method, "POST");
    assert.ok(call.init.headers["Idempotency-Key"]);
  }
});

// --------------------------------------------------------------------------- //
// 读方法：查询串与不注入幂等键
// --------------------------------------------------------------------------- //

test("getCanvas 传 product_version 查询参数，且不带 Idempotency-Key", async () => {
  installFetch(() =>
    jsonResponse(200, {
      session_id: "s1",
      product_version: 3,
      schema_version: 1,
      root: null,
      branches: [],
    }),
  );

  await getCanvas("s1", 3);

  const { url, init } = calls[0];
  assert.match(url, /\/api\/v2\/sessions\/s1\/canvas\?product_version=3/);
  assert.equal(init.method, "GET");
  assert.equal(init.headers["Idempotency-Key"], undefined);
  assert.equal(init.cache, "no-store");
});

test("getCanvas 不传版本时省略查询串", async () => {
  installFetch(() => jsonResponse(200, { branches: [] }));
  await getCanvas("s1");
  assert.match(calls[0].url, /\/canvas$/);
});

test("getProductState 传 version 查询参数", async () => {
  installFetch(() => jsonResponse(200, { session_id: "s1", version: 2 }));
  await getProductState("s1", 2);
  assert.match(calls[0].url, /\/product-state\?version=2/);
});

test("listMessages 正确拼接 before_seq/limit/stage，并跳过空值", async () => {
  installFetch(() => jsonResponse(200, { items: [], next_before_seq: null }));
  await listMessages("s1", { beforeSeq: 10, limit: 50, stage: "clarify", auditId: null });
  const { url } = calls[0];
  assert.match(url, /before_seq=10/);
  assert.match(url, /limit=50/);
  assert.match(url, /stage=clarify/);
  assert.doesNotMatch(url, /audit_id/);
});

test("getSession 走 GET 且解析 JSON", async () => {
  installFetch(() => jsonResponse(200, { id: "s1", phase: "clarifying", session_version: 2 }));
  const session = await getSession("s1");
  assert.equal(session.id, "s1");
  assert.equal(session.session_version, 2);
});

test("204 响应返回 undefined 而不抛解析错误", async () => {
  installFetch(() => ({
    ok: true,
    status: 204,
    json: async () => {
      throw new Error("no body");
    },
    text: async () => "",
  }));
  const result = await getSession("s1");
  assert.equal(result, undefined);
});

// --------------------------------------------------------------------------- //
// 网络/协议错误
// --------------------------------------------------------------------------- //

test("网络异常被包装为 NETWORK_ERROR 且 retryable", async () => {
  globalThis.fetch = async () => {
    throw new TypeError("Failed to fetch");
  };

  await assert.rejects(
    () => getSession("s1"),
    (error) => {
      assert.ok(error instanceof ApiError);
      assert.equal(error.code, "NETWORK_ERROR");
      assert.equal(error.status, 0);
      assert.equal(error.retryable, true);
      return true;
    },
  );
});

test("错误响应被包装为 ApiError 并带上服务端 code", async () => {
  installFetch(() =>
    jsonResponse(409, {
      error: { code: "VERSION_CONFLICT", message: "stale", retryable: false, request_id: "req-1", details: null },
    }),
  );

  await assert.rejects(
    () => sendCommand("s1", "confirm_brief", { expected_product_version: 1 }),
    (error) => {
      assert.ok(error instanceof ApiError);
      assert.equal(error.code, "VERSION_CONFLICT");
      assert.equal(error.status, 409);
      assert.equal(error.requestId, "req-1");
      return true;
    },
  );
});

test("runEventsUrl 拼接 after_seq 查询参数", () => {
  assert.match(runEventsUrl("r1"), /\/api\/v2\/runs\/r1\/events$/);
  assert.match(runEventsUrl("r1", 12), /after_seq=12$/);
  assert.doesNotMatch(runEventsUrl("r1", 0), /after_seq/);
});

test("apiV2 显式 headers 可覆盖默认值", async () => {
  installFetch(() => jsonResponse(200, { id: "s1" }));
  await apiV2("/api/v2/sessions/s1", { headers: { "X-Trace": "abc" } });
  assert.equal(calls[0].init.headers["X-Trace"], "abc");
});
