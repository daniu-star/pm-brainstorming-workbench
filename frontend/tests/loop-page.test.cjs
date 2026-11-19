"use strict";

/**
 * Loop 工作室页面链路测试（node --test，无 RTL / 无 jsdom）。
 *
 * 页面本身只做渲染，所有分支判断都在 `lib/loopView.ts`（loop-view.test.cjs 覆盖）里。
 * 这里覆盖「页面能拿到正确数据」的三段真实链路：
 *  1. `lib/api.ts` 的 4 个 GET 与 startLoop / decideLoop 命令封装（fetch 桩）；
 *  2. store 的 hydrateLoopById / startLoop / decideLoop（fetch 桩，断言请求体）；
 *  3. 失败路径：404 → loopErrors.notFound → 页面回到 StartLoopBar；
 *     409 LOOP_VERSION_CONFLICT → 抛回 ApiError，页面据此刷新快照 + toast。
 */

const path = require("node:path");
const Module = require("node:module");
const { test } = require("node:test");
const assert = require("node:assert/strict");

const BUILD_DIR = path.join(__dirname, "..", "node_modules", ".cache", "test-build");
const STORE_PATH = path.join(BUILD_DIR, "store", "sessionStore.js");

// 编译产物里的 `@/lib/x` 无法被 Node 直接解析，这里映射回 test-build 目录。
const originalResolve = Module._resolveFilename;
Module._resolveFilename = function (request, parent, isMain, options) {
  if (request.startsWith("@/")) {
    return originalResolve.call(
      this,
      path.join(BUILD_DIR, request.slice(2)),
      parent,
      isMain,
      options
    );
  }
  return originalResolve.call(this, request, parent, isMain, options);
};

const { useSessionStore } = require(STORE_PATH);
const api = require(path.join(BUILD_DIR, "lib", "api.js"));
const loopView = require(path.join(BUILD_DIR, "lib", "loopView.js"));

const PRISTINE_STATE = useSessionStore.getState();
const realFetch = globalThis.fetch;

const SESSION_VIEW = {
  id: "s1",
  owner_email: "me@example.com",
  phase: "brainstorming",
  session_version: 3,
  current_product_version: 2,
  current_audit_id: null,
  current_loop_id: null,
  active_run_id: null,
  problem_statement: "做一个协作白板",
  brief_status: "draft",
  created_at: "",
  updated_at: "",
  deleted_at: null,
  message_count: 0,
};

const LOOP_SNAPSHOT = {
  loop_id: "loop_1",
  session_id: "s1",
  owner_email: "me@example.com",
  status: "awaiting_decision",
  scope: "mvp",
  current_round: 1,
  max_rounds: 3,
  target_score: 85,
  loop_version: 3,
  focus: "只做单人流程",
  rounds: [
    {
      id: "round_1",
      ordinal: 1,
      status: "done",
      score: 78,
      verdict: "iterate",
      stages: [
        { stage: "ideate", status: "done", summary: "", error_code: null, updated_at: "" },
        { stage: "evaluate", status: "done", summary: "", error_code: null, updated_at: "" },
      ],
      artifacts: [{ id: "a1", kind: "mvp_spec", title: "MVP 规格", size_bytes: 10, created_at: "" }],
      started_at: "",
      finished_at: "",
    },
  ],
  mvp_spec: null,
  validation: null,
  prototype: null,
  test_report: null,
  scorecard: null,
  latest_run_id: "r1",
  created_at: "",
  updated_at: "",
};

function runReceipt(overrides = {}) {
  return {
    run_id: "r1",
    session_id: "s1",
    action: "start_loop",
    status: "queued",
    session_version: 4,
    base_product_version: 2,
    events_url: "/api/v2/runs/r1/events",
    reused: false,
    audit_id: null,
    loop_id: "loop_1",
    ...overrides,
  };
}

function errorBody(code, message, status, details = null) {
  return {
    status,
    body: {
      error: { code, message, retryable: false, request_id: "req_1", details },
    },
  };
}

function jsonResponse(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
    headers: { get: () => "application/json" },
  };
}

/** 桩 fetch：记录请求，按 handler 的返回值构造响应；handler 可返回 {status, body}。 */
function installFetch(handler) {
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    const target = String(url);
    const body = init.body ? JSON.parse(init.body) : null;
    calls.push({ url: target, method: init.method ?? "GET", body });
    const result = handler(target, body, init);
    return jsonResponse(result.status ?? 200, result.body ?? result);
  };
  return calls;
}

function prime(overrides = {}) {
  useSessionStore.setState({ ...PRISTINE_STATE }, true);
  useSessionStore.setState({
    sessionId: "s1",
    currentSessionId: "s1",
    legacyMode: false,
    phase: "interview",
    v2Phase: "brainstorming",
    sessionView: { ...SESSION_VIEW },
    productVersion: 2,
    isStreaming: false,
    // 避免测试里真的打开 SSE：只记录订阅到的 Run
    subscribeRun: () => {},
    ...overrides,
  });
}

function waitFor(predicate, timeoutMs = 1000) {
  const startedAt = Date.now();
  return new Promise((resolve, reject) => {
    const tick = () => {
      if (predicate()) return resolve();
      if (Date.now() - startedAt > timeoutMs) {
        return reject(new Error("waitFor 超时：期望的异步写入没有发生"));
      }
      setTimeout(tick, 5);
    };
    tick();
  });
}

function restoreFetch() {
  globalThis.fetch = realFetch;
}

// --------------------------------------------------------------------------- //
// 1. api.ts：4 个 GET + 两个命令封装
// --------------------------------------------------------------------------- //

test("api.startLoop 走 POST /sessions/{id}/commands，action=start_loop 且 payload 完整", async () => {
  let calls = installFetch(() => ({ status: 202, body: runReceipt() }));
  try {
    const receipt = await api.startLoop("s1", {
      scope: "mvp",
      max_rounds: 3,
      target_score: 85,
      focus: "只做登录",
      expected_product_version: 2,
    });
    assert.equal(receipt.loop_id, "loop_1");
    assert.equal(calls.length, 1);
    assert.ok(calls[0].url.endsWith("/api/v2/sessions/s1/commands"));
    assert.equal(calls[0].method, "POST");
    assert.equal(calls[0].body.action, "start_loop");
    assert.equal(calls[0].body.expected_session_version, null);
    assert.deepEqual(calls[0].body.payload, {
      scope: "mvp",
      max_rounds: 3,
      target_score: 85,
      focus: "只做登录",
      expected_product_version: 2,
    });
    assert.ok(typeof calls[0].body.request_id === "string" && calls[0].body.request_id.length > 0);
  } finally {
    restoreFetch();
  }
});

test("api.decideLoop 走 loop_decision，expected_loop_version 原样透传", async () => {
  const calls = installFetch(() => ({ status: 202, body: runReceipt({ action: "loop_decision" }) }));
  try {
    await api.decideLoop("s1", {
      loop_id: "loop_1",
      expected_loop_version: 3,
      decision: "ship",
      note: "可以交付",
    });
    assert.equal(calls[0].body.action, "loop_decision");
    assert.deepEqual(calls[0].body.payload, {
      loop_id: "loop_1",
      expected_loop_version: 3,
      decision: "ship",
      note: "可以交付",
    });
  } finally {
    restoreFetch();
  }
});

test("api 的 listLoops / getLoop / getLoopRound / getLoopArtifact 请求正确路径", async () => {
  const calls = installFetch((url) => {
    if (url.includes("/sessions/s1/loops")) return { body: { items: [LOOP_SNAPSHOT], next_cursor: null } };
    if (url.includes("/rounds/2")) return { body: LOOP_SNAPSHOT.rounds[0] };
    if (url.includes("/artifacts/a1")) return { body: { artifact: LOOP_SNAPSHOT.rounds[0].artifacts[0], payload: {} } };
    if (url.includes("/api/v2/loops/loop_1")) return { body: LOOP_SNAPSHOT };
    return errorBody("NOT_FOUND", "not found", 404);
  });
  try {
    const page = await api.listLoops("s1", { limit: 5 });
    assert.equal(page.items.length, 1);
    assert.ok(calls[0].url.includes("/api/v2/sessions/s1/loops"));
    assert.ok(calls[0].url.includes("limit=5"));

    const snapshot = await api.getLoop("loop_1");
    assert.equal(snapshot.loop_id, "loop_1");

    const round = await api.getLoopRound("loop_1", 2);
    assert.equal(round.ordinal, 1);

    const artifact = await api.getLoopArtifact("loop_1", "a1");
    assert.equal(artifact.artifact.kind, "mvp_spec");
    assert.ok(calls.some((call) => call.url.includes("/loops/loop_1/rounds/2")));
    assert.ok(calls.some((call) => call.url.includes("/loops/loop_1/artifacts/a1")));
  } finally {
    restoreFetch();
  }
});

// --------------------------------------------------------------------------- //
// 2. store：hydrateLoopById / startLoop / decideLoop
// --------------------------------------------------------------------------- //

test("store.hydrateLoopById 归一化快照并写入 loops / loopId，loopLoading 归位", async () => {
  prime();
  installFetch((url) => {
    if (url.includes("/api/v2/loops/loop_1")) return { body: LOOP_SNAPSHOT };
    return errorBody("NOT_FOUND", "not found", 404);
  });
  try {
    const ok = await useSessionStore.getState().hydrateLoopById("loop_1");
    assert.equal(ok, true);
    const state = useSessionStore.getState();
    assert.equal(state.loopId, "loop_1");
    assert.equal(state.loopLoading, false);
    assert.equal(state.loopErrors.loop_1, null);
    assert.equal(state.loops.loop_1.status, "awaiting_decision");
    assert.equal(state.loops.loop_1.current_round, 1);
    assert.equal(state.loops.loop_1.rounds.length, 1);
    // 页面据此进入「闭环主体」而不是启动表单
    const entry = loopView.resolveLoopEntryState({
      sessionLoading: false,
      sessionPhase: "brainstorming",
      legacyMode: false,
      loop: state.loops.loop_1,
      loopLoading: state.loopLoading,
      loopError: state.loopErrors.loop_1,
    });
    assert.equal(entry.showLoop, true);
    assert.equal(entry.showStartPanel, false);
  } finally {
    restoreFetch();
  }
});

test("store.startLoop 用当前产品版本（不是 0）并带上归一化后的表单参数", async () => {
  // productVersion=0 但会话快照有 current_product_version=2：必须取 2
  prime({ productVersion: 0 });
  const calls = installFetch((url) => {
    if (url.includes("/api/v2/sessions/s1/commands")) return { status: 202, body: runReceipt() };
    if (url.includes("/api/v2/loops/loop_1")) return { body: LOOP_SNAPSHOT };
    return errorBody("NOT_FOUND", "not found", 404);
  });
  try {
    const receipt = await useSessionStore.getState().startLoop({
      maxRounds: 9,
      targetScore: 10,
      focus: "  只做登录  ",
    });
    assert.equal(receipt.loop_id, "loop_1");
    const command = calls.find((call) => call.url.includes("/commands"));
    assert.ok(command);
    assert.equal(command.body.action, "start_loop");
    assert.equal(command.body.payload.expected_product_version, 2);
    assert.equal(command.body.payload.max_rounds, 5);
    assert.equal(command.body.payload.target_score, 60);
    assert.equal(command.body.payload.focus, "只做登录");
    // 回执里的 loop_id 会立刻拉一次快照，页面无需等 Run 结束
    await waitFor(() => Boolean(useSessionStore.getState().loops.loop_1));
    assert.equal(useSessionStore.getState().loops.loop_1.loop_id, "loop_1");
  } finally {
    restoreFetch();
  }
});

test("store.decideLoop 发送 loop_id / expected_loop_version / decision / note", async () => {
  prime({ loops: { loop_1: LOOP_SNAPSHOT }, loopId: "loop_1" });
  const calls = installFetch((url) => {
    if (url.includes("/api/v2/sessions/s1/commands")) {
      return { status: 202, body: runReceipt({ action: "loop_decision" }) };
    }
    if (url.includes("/api/v2/loops/loop_1")) return { body: LOOP_SNAPSHOT };
    return errorBody("NOT_FOUND", "not found", 404);
  });
  try {
    await useSessionStore.getState().decideLoop({
      loopId: "loop_1",
      expectedLoopVersion: 3,
      decision: "ship",
      note: "  可以交付  ",
    });
    const command = calls.find((call) => call.url.includes("/commands"));
    assert.ok(command);
    assert.equal(command.body.action, "loop_decision");
    assert.deepEqual(command.body.payload, {
      loop_id: "loop_1",
      expected_loop_version: 3,
      decision: "ship",
      note: "可以交付",
    });
  } finally {
    restoreFetch();
  }
});

// --------------------------------------------------------------------------- //
// 3. 失败路径：404 回到启动表单 / 409 版本冲突抛回页面
// --------------------------------------------------------------------------- //

test("hydrateLoopById 404：记录 notFound，页面回退到 StartLoopBar", async () => {
  prime();
  installFetch(() => errorBody("NOT_FOUND", "闭环不存在", 404));
  try {
    const ok = await useSessionStore.getState().hydrateLoopById("loop_missing");
    assert.equal(ok, false);
    const state = useSessionStore.getState();
    assert.equal(state.loopLoading, false);
    assert.equal(state.loopErrors.loop_missing.notFound, true);
    const entry = loopView.resolveLoopEntryState({
      sessionLoading: false,
      sessionPhase: "brainstorming",
      legacyMode: false,
      loop: null,
      loopLoading: state.loopLoading,
      loopError: state.loopErrors.loop_missing,
    });
    assert.equal(entry.showStartPanel, true);
    assert.ok(entry.loadErrorText.includes("重新启动"));
  } finally {
    restoreFetch();
  }
});

test("decideLoop 409 LOOP_VERSION_CONFLICT：抛回 ApiError 且 isStreaming 复位", async () => {
  prime({ loops: { loop_1: LOOP_SNAPSHOT }, loopId: "loop_1" });
  installFetch(() =>
    errorBody("LOOP_VERSION_CONFLICT", "闭环状态已更新", 409, { current_loop_version: 4 })
  );
  try {
    await assert.rejects(
      () =>
        useSessionStore.getState().decideLoop({
          loopId: "loop_1",
          expectedLoopVersion: 3,
          decision: "iterate",
        }),
      (error) => {
        assert.equal(api.isApiError(error), true);
        assert.equal(error.code, "LOOP_VERSION_CONFLICT");
        assert.equal(error.status, 409);
        assert.equal(error.details.current_loop_version, 4);
        return true;
      }
    );
    const state = useSessionStore.getState();
    assert.equal(state.isStreaming, false);
    assert.ok(state.error && state.error.includes("闭环状态已在别处更新"));
    // 页面展示的文案来自 loopView 的中文映射
    assert.ok(loopView.loopErrorText("LOOP_VERSION_CONFLICT").includes("刷新后重试"));
  } finally {
    restoreFetch();
  }
});

test("startLoop 409 LOOP_ACTIVE：details.loop_id 可用于跳到已有闭环", async () => {
  prime();
  installFetch(() =>
    errorBody("LOOP_ACTIVE", "已有进行中的闭环", 409, { loop_id: "loop_existing", status: "running" })
  );
  try {
    await assert.rejects(
      () => useSessionStore.getState().startLoop({ maxRounds: 3 }),
      (error) => {
        assert.equal(api.isApiError(error), true);
        assert.equal(error.code, "LOOP_ACTIVE");
        assert.equal(error.details.loop_id, "loop_existing");
        return true;
      }
    );
  } finally {
    restoreFetch();
  }
});
