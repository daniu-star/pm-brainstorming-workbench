"use strict";

/**
 * 真实 zustand store 的行为测试（node --test，无第三方 runner）。
 *
 * 为什么不用 @testing-library：本机 npm registry 不可达，无法安装 RTL / vitest。
 * 做法：`tsc -p tsconfig.test.json` 把 store/sessionStore.ts 编译到
 * node_modules/.cache/test-build，下面用 Module._resolveFilename 钩子把 `@/x`
 * 指到编译产物，再 require 真实 store；fetch 用桩替代，不联网。
 *
 * 覆盖评测清单里两条必须落到 store 的断言：
 *  1. v2Phase=audit_completed 时 sendMessage 必须发出 brainstorm 命令，
 *     且绝不能调用 answerInterview（旧 phase=interview 的误判路径）。
 *  2. ControlReceipt 带 related_run_id 时必须订阅后端派生的收尾 Run（生成报告）。
 * 另外覆盖 answerInterview 的返回值契约：未提交返回 false，调用方保留输入。
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
const phaseRouter = require(path.join(BUILD_DIR, "lib", "phaseRouter.js"));

/** 模块初始 state 快照：每个用例前用它整体复位，避免用例互相污染。 */
const PRISTINE_STATE = useSessionStore.getState();

const SESSION_VIEW = {
  id: "s1",
  session_version: 3,
  phase: "audit_completed",
  current_product_version: 2,
  current_audit_id: "a1",
  active_run_id: null,
  legacy_mode: false,
};

/** 最小 MessageView 夹具：只填 store 合并/排序用得到的字段。 */
function messageView(seq, overrides = {}) {
  return {
    id: `m${seq}`,
    session_id: "s1",
    seq,
    sender_kind: "human",
    role_code: null,
    stage: "clarify",
    content: `第 ${seq} 条`,
    status: "completed",
    run_id: null,
    audit_id: null,
    created_at: new Date(0).toISOString(),
    ...overrides,
  };
}

function prime(overrides = {}) {
  useSessionStore.setState({ ...PRISTINE_STATE }, true);
  useSessionStore.setState({
    sessionId: "s1",
    currentSessionId: "s1",
    legacyMode: false,
    // 模拟「审计刚结束」：旧 phase 仍是 interview，v2 阶段是 audit_completed
    phase: "interview",
    v2Phase: "audit_completed",
    sessionView: { ...SESSION_VIEW },
    isStreaming: false,
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

/** 桩 fetch：记录请求，按 handler 返回 JSON；不发起真实网络请求。 */
function stubFetch(handler) {
  const calls = [];
  global.fetch = async (url, init = {}) => {
    const body = init.body ? JSON.parse(init.body) : null;
    calls.push({ url: String(url), method: init.method ?? "GET", body });
    const payload = typeof handler === "function" ? handler(String(url), body) : handler;
    return {
      ok: true,
      status: 200,
      json: async () => payload,
      text: async () => JSON.stringify(payload),
      headers: { get: () => "application/json" },
    };
  };
  return calls;
}

const realFetch = global.fetch;

test("v2Phase=audit_completed：工作台发送走 brainstorm，且不调用 answerInterview", async () => {
  prime();
  const subscribed = [];
  const answered = [];
  useSessionStore.setState({
    subscribeRun: (runId) => subscribed.push(runId),
    answerInterview: (answer) => {
      answered.push(answer);
      return Promise.resolve(true);
    },
  });
  const calls = stubFetch({
    run_id: "run_1",
    session_id: "s1",
    status: "queued",
    action: "brainstorm",
    session_version: 4,
    audit_id: null,
  });

  useSessionStore.getState().sendMessage("再想想", "all");
  await waitFor(() => {
    const state = useSessionStore.getState();
    return calls.length > 0 && state.runs.run_1 !== undefined;
  });

  const command = calls.find((call) => call.url.includes("/commands"));
  assert.ok(command, "应该向 /commands 发命令");
  assert.equal(command.body.action, "brainstorm");
  assert.equal(command.body.payload.content, "再想想");
  assert.equal(command.body.payload.target, "all");
  assert.deepEqual(answered, [], "audit_completed 下必须走 sendMessage，不能再提交 answer_audit");
  assert.deepEqual(subscribed, ["run_1"], "收到 RunReceipt 后应订阅该 Run");
  // 旧 phase 仍然是 interview，但 v2Phase 不允许被 mapV2Phase 覆盖
  assert.equal(useSessionStore.getState().phase, "interview");
  assert.equal(useSessionStore.getState().v2Phase, "audit_completed");
});

test("v2Phase=brainstorming 时即使旧 phase=coach 也发 brainstorm（不误发 clarify）", async () => {
  prime({ phase: "coach", v2Phase: "brainstorming", sessionView: { ...SESSION_VIEW, phase: "brainstorming" } });
  useSessionStore.setState({ subscribeRun: () => undefined });
  const calls = stubFetch({
    run_id: "run_2",
    session_id: "s1",
    status: "queued",
    action: "brainstorm",
    session_version: 4,
    audit_id: null,
  });

  useSessionStore.getState().sendMessage("继续聊", "all");
  await waitFor(() => calls.length > 0);

  assert.equal(calls[0].body.action, "brainstorm");
});

test("v2Phase=clarifying 时发送走 clarify", async () => {
  prime({ phase: "brainstorm", v2Phase: "clarifying", sessionView: { ...SESSION_VIEW, phase: "clarifying" } });
  useSessionStore.setState({ subscribeRun: () => undefined });
  const calls = stubFetch({
    run_id: "run_3",
    session_id: "s1",
    status: "queued",
    action: "clarify",
    session_version: 4,
    audit_id: null,
  });

  useSessionStore.getState().sendMessage("我想做一个给宠物用的相机", "all");
  await waitFor(() => calls.length > 0);

  assert.equal(calls[0].body.action, "clarify");
  assert.equal(calls[0].body.payload.content, "我想做一个给宠物用的相机");
});

test("ControlReceipt 带 related_run_id 时订阅收尾 Run（只订阅一次）", async () => {
  prime();
  const subscribed = [];
  useSessionStore.setState({ subscribeRun: (runId) => subscribed.push(runId) });
  stubFetch({
    control_id: "ctl_1",
    audit_id: "a1",
    status: "closing",
    session_version: 4,
    related_run_id: "run_close",
    reused: false,
    action: "end_audit",
  });

  const receipt = await useSessionStore.getState().sendCommand("end_audit", { audit_id: "a1" });

  assert.equal(receipt.control_id, "ctl_1");
  assert.deepEqual(subscribed, ["run_close"]);
});

test("ControlReceipt 无 related_run_id 时不订阅，仅提示（由页面退避轮询兜底）", async () => {
  prime();
  const subscribed = [];
  useSessionStore.setState({ subscribeRun: (runId) => subscribed.push(runId), stateWarnings: [] });
  stubFetch({
    control_id: "ctl_2",
    audit_id: "a1",
    status: "closing",
    session_version: 4,
    related_run_id: null,
    reused: false,
    action: "cancel_audit",
  });

  await useSessionStore.getState().sendCommand("cancel_audit", { audit_id: "a1" });

  assert.deepEqual(subscribed, []);
  // 没有收尾 Run 时必须解除 streaming，审计页的 closing 退避轮询才会启动
  assert.equal(useSessionStore.getState().isStreaming, false);
  assert.ok(
    useSessionStore.getState().stateWarnings.some((warning) => warning.includes("结束/取消审计")),
    "应提示控制请求已提交"
  );
});

test("answerInterview：没有待回答的问题时返回 false 并报错（调用方保留输入）", async () => {
  prime();
  stubFetch((url) =>
    url.includes("/audits/")
      ? { id: "a1", status: "active", active_question_id: null, questions: [] }
      : {}
  );

  const submitted = await useSessionStore.getState().answerInterview("我的回答");

  assert.equal(submitted, false);
  assert.equal(useSessionStore.getState().error, "当前没有待回答的审计问题");
});

test("answerInterview：有当前问题时提交 answer_audit 并返回 true", async () => {
  prime();
  useSessionStore.setState({ subscribeRun: () => undefined });
  const calls = stubFetch((url) =>
    url.includes("/audits/")
      ? { id: "a1", status: "active", active_question_id: "q_1", questions: [] }
      : {
          run_id: "run_4",
          session_id: "s1",
          status: "queued",
          action: "answer_audit",
          session_version: 4,
          audit_id: "a1",
        }
  );

  const submitted = await useSessionStore.getState().answerInterview("我的回答");

  assert.equal(submitted, true);
  const command = calls.find((call) => call.url.includes("/commands"));
  assert.ok(command);
  assert.equal(command.body.action, "answer_audit");
  assert.equal(command.body.payload.audit_id, "a1");
  assert.equal(command.body.payload.question_id, "q_1");
  assert.equal(command.body.payload.answer, "我的回答");
});

test("loadSession 切会话：清空会话级视图/画布/产品版本，且新会话按自己的基线 hydrate", async () => {
  // 上一个会话 A 已经把全局 productVersion 推到 5，并缓存了画布
  prime({
    sessionId: "sA",
    currentSessionId: "sA",
    sessionView: { ...SESSION_VIEW, id: "sA", session_version: 9 },
    productVersion: 5,
    productStates: { sA: { version: 5 } },
    canvas: { projection: null, productVersion: 5, status: "latest", error: null },
  });
  const calls = stubFetch((url) => {
    if (url.includes("/sessions/sB/product-state")) return { version: 1 };
    if (url.includes("/sessions/sB/canvas")) return { product_version: 1, nodes: [], edges: [] };
    if (url.includes("/sessions/sB/messages")) return { items: [], next_cursor: null };
    return {
      id: "sB",
      session_version: 1,
      phase: "brainstorming",
      current_product_version: 1,
      current_audit_id: null,
      active_run_id: null,
      legacy_mode: false,
    };
  });

  const loading = useSessionStore.getState().loadSession("sB");
  // set() 在第一个 await 之前同步执行：旧会话的视图/画布/版本必须立刻清空
  const reset = useSessionStore.getState();
  assert.equal(reset.productVersion, 0, "切会话必须把全局 productVersion 复位为 0");
  assert.equal(reset.productState, null);
  assert.equal(reset.canvas, null);
  assert.equal(reset.sessionView, null);
  assert.equal(reset.canvasLoading, false);
  assert.equal(reset.isGeneratingCanvas, false);
  // 按 sessionId 键的缓存保留，便于切回旧会话
  assert.equal(reset.productStates.sA.version, 5);

  await loading;
  await waitFor(() => calls.some((call) => call.url.includes("/sessions/sB/canvas")));
  // 新会话自己是基线：服务端版本 1 不该被上一个会话的 5 判成旧版本
  assert.equal(useSessionStore.getState().productVersion, 1);
  assert.equal(useSessionStore.getState().sessionView.id, "sB");
});

test("事件流连续 EOF：重连次数递传，达到上限后报错并停止 streaming", async () => {
  prime();
  const calls = stubFetch((url) => {
    if (url.includes("/runs/") && url.includes("/events")) return {};
    return {
      id: "run_eof",
      session_id: "s1",
      action: "brainstorm",
      status: "running",
      error_code: null,
      error_message: null,
    };
  });

  useSessionStore.getState().subscribeRun("run_eof");
  assert.equal(useSessionStore.getState().isStreaming, true);

  await waitFor(() => {
    const state = useSessionStore.getState();
    return state.error !== null && state.isStreaming === false;
  }, 8000);

  const state = useSessionStore.getState();
  assert.match(state.error, /事件流已断开/);
  assert.equal(state.subscribingRunId, null);
  const runFetches = calls.filter(
    (call) => call.url.includes("/runs/run_eof") && !call.url.includes("/events")
  ).length;
  assert.ok(runFetches >= 1, "重连时必须先查 Run 是否已进入终态");
  assert.ok(runFetches <= 3, `重连次数应有上限，实际查询 ${runFetches} 次`);
});

test("role.output.updated 不会抹掉尚未确认的本地乐观用户消息", async () => {
  prime();
  const agentFrame = `data: ${JSON.stringify({
    type: "role.output.updated",
    run_id: "run_opt",
    session_id: "s1",
    event_seq: 1,
    payload: { message_id: "m_agent", content: "先对齐目标用户", role_code: "strategist" },
  })}\n\n`;
  const encoder = new TextEncoder();
  let streamed = false;
  global.fetch = async (url) => {
    const href = String(url);
    if (href.includes("/events")) {
      return {
        ok: true,
        status: 200,
        headers: { get: () => "text/event-stream" },
        json: async () => ({}),
        text: async () => "",
        body: {
          getReader() {
            return {
              read: async () => {
                if (streamed) return { done: true, value: undefined };
                streamed = true;
                return { done: false, value: encoder.encode(agentFrame) };
              },
              cancel: async () => undefined,
            };
          },
        },
      };
    }
    if (href.includes("/commands")) {
      return {
        ok: true,
        status: 200,
        headers: { get: () => "application/json" },
        json: async () => ({
          run_id: "run_opt",
          session_id: "s1",
          status: "queued",
          action: "brainstorm",
          session_version: 4,
          audit_id: null,
        }),
        text: async () => "",
      };
    }
    return {
      ok: true,
      status: 200,
      headers: { get: () => "application/json" },
      json: async () => ({}),
      text: async () => "",
    };
  };

  useSessionStore.getState().sendMessage("我的新想法", "all");
  await waitFor(() =>
    (useSessionStore.getState().messagesBySession.s1 ?? []).some(
      (view) => view.id === "m_agent"
    )
  );

  const views = useSessionStore.getState().messagesBySession.s1;
  assert.ok(
    views.some((view) => view.sender_kind === "human" && view.content === "我的新想法"),
    "role.output.updated 只能追加/替换 agent 消息，不能删掉用户刚发的内容"
  );
  // 结束本次订阅，避免残留的重连定时器在 fetch 恢复后再发请求
  useSessionStore.getState().unsubscribe();
  useSessionStore.setState({ generation: useSessionStore.getState().generation + 1 });
});

test("hydrateAuditById：读取失败记入 auditErrors（500 不当作「没有审计」，404 才是）", async () => {
  prime();
  const respond = (status, detail) => async () => ({
    ok: false,
    status,
    headers: { get: () => "application/json" },
    json: async () => ({ detail }),
    text: async () => detail,
  });

  global.fetch = respond(500, "服务器内部错误");
  const ok = await useSessionStore.getState().hydrateAuditById("a1");
  assert.equal(ok, false);
  const failed = useSessionStore.getState().auditErrors.a1;
  assert.ok(failed, "读取失败必须落到 auditErrors，页面才能渲染错误态而不是「开始审计」");
  assert.equal(failed.notFound, false);
  assert.equal(failed.message.length > 0, true);

  global.fetch = respond(404, "审计不存在");
  await useSessionStore.getState().hydrateAuditById("a404");
  assert.equal(useSessionStore.getState().auditErrors.a404.notFound, true);
});

test("hydrateV2 已有 stage=brainstorm 的 agent 消息：不再提示「开始讨论」", async () => {
  prime({
    phase: "clarify",
    v2Phase: "clarifying",
    sessionView: { ...SESSION_VIEW, phase: "clarifying" },
  });
  stubFetch((url) => {
    if (url.includes("/messages")) {
      return {
        items: [
          {
            id: "m1",
            session_id: "s1",
            seq: 1,
            sender_kind: "agent",
            role_code: "strategist",
            stage: "brainstorm",
            content: "四个讨论方向",
            status: "completed",
            run_id: "run_old",
            audit_id: null,
            created_at: new Date().toISOString(),
          },
        ],
        next_cursor: null,
      };
    }
    if (url.includes("/product-state")) return { version: 1 };
    if (url.includes("/canvas")) return { product_version: 1, nodes: [], edges: [] };
    return {
      id: "s1",
      session_version: 3,
      phase: "brainstorming",
      current_product_version: 1,
      current_audit_id: null,
      active_run_id: null,
      legacy_mode: false,
    };
  });

  await useSessionStore.getState().loadSession("s1");
  const state = useSessionStore.getState();
  assert.equal(state.v2Phase, "brainstorming");
  assert.equal(
    (state.messagesBySession.s1 ?? []).some(
      (view) => view.sender_kind === "agent" && view.stage === "brainstorm"
    ),
    true
  );
  assert.equal(
    phaseRouter.shouldShowStartDiscussion({
      v2Phase: state.v2Phase,
      phase: state.phase,
      hasBrainstormRun: false,
      messages: state.messagesBySession.s1 ?? [],
    }),
    false,
    "刷新后内存 runs 为空，但持久化消息已证明讨论开始过"
  );
});

test("sendToCoach：旧链接重复提交同内容 clarify 直接返回，不再撞 SESSION_BUSY", () => {
  prime({
    phase: "coach",
    v2Phase: "clarifying",
    sessionView: { ...SESSION_VIEW, phase: "clarifying", current_audit_id: null },
    messagesBySession: {
      s1: [messageView(1, { content: "做一个宠物相机" })],
    },
  });
  const calls = stubFetch({});

  useSessionStore.getState().sendToCoach("做一个宠物相机");

  // 后端 create_session_core 已写入这条 clarify 消息：前端不能再发第二次命令
  assert.equal(
    calls.filter((call) => call.url.includes("/commands")).length,
    0,
    "重复的初始问题不应再发 clarify 命令"
  );
  const state = useSessionStore.getState();
  assert.equal(state.messagesBySession.s1.length, 1);
  assert.equal(state.isStreaming, false);
});

test("loadEarlierMessages：按 before_seq 拉取更早消息并与首屏无缝合并", async () => {
  const latest = Array.from({ length: 50 }, (_, index) => messageView(51 + index));
  prime({
    messagesBySession: { s1: latest },
    messagePages: { s1: { hasEarlier: true, beforeSeq: 51, earliestSeq: 51 } },
  });
  const calls = stubFetch((url) => {
    if (url.includes("/messages")) {
      return {
        items: Array.from({ length: 50 }, (_, index) => messageView(index + 1)),
        next_before_seq: null,
      };
    }
    return {};
  });

  await useSessionStore.getState().loadEarlierMessages();

  const request = calls.find((call) => call.url.includes("/messages"));
  assert.ok(request, "应该按游标请求更早一页消息");
  assert.match(request.url, /before_seq=51/);

  const state = useSessionStore.getState();
  const views = state.messagesBySession.s1;
  assert.equal(views.length, 100);
  assert.equal(new Set(views.map((view) => view.id)).size, 100, "合并后不应出现重复气泡");
  assert.deepEqual(
    views.map((view) => view.seq),
    Array.from({ length: 100 }, (_, index) => index + 1),
    "消息应按 seq 连续"
  );
  assert.deepEqual(state.messagePages.s1, {
    hasEarlier: false,
    beforeSeq: null,
    earliestSeq: 1,
  });
  assert.equal(state.earlierLoading.s1, false);
});

test("hydrateMessages 保存 next_before_seq：长会话刷新后可继续加载更早", async () => {
  prime();
  stubFetch((url) => {
    if (url.includes("/messages")) {
      return {
        items: Array.from({ length: 100 }, (_, index) => messageView(201 + index)),
        next_before_seq: 201,
      };
    }
    if (url.includes("/product-state")) return { version: 2 };
    if (url.includes("/canvas")) return { product_version: 2, nodes: [], edges: [] };
    return { ...SESSION_VIEW, current_audit_id: null };
  });

  await useSessionStore.getState().loadSession("s1");

  const page = useSessionStore.getState().messagePages.s1;
  assert.ok(page, "首屏加载后必须保存分页游标");
  assert.equal(page.beforeSeq, 201);
  assert.equal(page.earliestSeq, 201);
  assert.equal(page.hasEarlier, true, "还有 200 条历史消息，应显示「加载更早」");
});

// ---------------------------------------------------------------------------
// UX-D1：工作台卸载断流后，审计页需要 resumeRunSubscription 续订同一个 Run
// ---------------------------------------------------------------------------
test("resumeRunSubscription：active_run_id 未订阅时重新订阅，已订阅时不重复", () => {
  const subscribed = [];
  prime({
    sessionView: { ...SESSION_VIEW, active_run_id: "run_9" },
    subscribingRunId: null,
    subscribeRun: (runId) => subscribed.push(runId),
  });

  useSessionStore.getState().resumeRunSubscription();
  assert.deepEqual(subscribed, ["run_9"], "审计页 mount 必须重新订阅运行中的 Run");

  prime({
    sessionView: { ...SESSION_VIEW, active_run_id: "run_9" },
    subscribingRunId: "run_9",
    subscribeRun: (runId) => subscribed.push(runId),
  });
  useSessionStore.getState().resumeRunSubscription();
  assert.deepEqual(subscribed, ["run_9"], "同一个 Run 已在订阅中，不能重复打开流");

  // 没有 active_run_id 时什么都不做；activeRunId 作为兜底来源
  prime({
    sessionView: { ...SESSION_VIEW, active_run_id: null },
    subscribingRunId: null,
    activeRunId: null,
    subscribeRun: (runId) => subscribed.push(runId),
  });
  useSessionStore.getState().resumeRunSubscription();
  assert.deepEqual(subscribed, ["run_9"]);

  prime({
    sessionView: { ...SESSION_VIEW, active_run_id: null },
    subscribingRunId: null,
    activeRunId: "run_8",
    subscribeRun: (runId) => subscribed.push(runId),
  });
  useSessionStore.getState().resumeRunSubscription();
  assert.deepEqual(subscribed, ["run_9", "run_8"], "sessionView 缺失时回退到 activeRunId");
});

// ---------------------------------------------------------------------------
// UX-D4：切换会话必须清空上一个会话的消息投影
// ---------------------------------------------------------------------------
test("loadSession：切换会话先清空 messages，加载失败也不残留旧会话消息", async () => {
  prime({ messages: [messageView(1), messageView(2)] });
  assert.equal(useSessionStore.getState().messages.length, 2);

  stubFetch(() => {
    throw new Error("会话读取失败");
  });

  await useSessionStore.getState().loadSession("s2");

  const state = useSessionStore.getState();
  assert.equal(state.currentSessionId, "s2");
  assert.deepEqual(state.messages, [], "上一个会话的消息必须被清空（UX-D4）");
  assert.equal(state.sessionView, null);
  assert.deepEqual(state.stateWarnings, []);
  assert.ok(state.error, "加载失败必须写入可见错误，而不是静默留空");
  assert.equal(state.sessionLoading, false);
});

// ---------------------------------------------------------------------------
// DX-03：丢弃旧画布版本后必须退出 generating，否则 CanvasPanel 永久「生成中」
test("refreshCanvas：丢弃旧投影后 canvas.status 复位为 stale/error，且 loading 结束", async () => {
  prime({
    productVersion: 2,
    canvas: {
      projection: { product_version: 2, nodes: [], edges: [] },
      productVersion: 2,
      status: "latest",
      error: null,
    },
  });
  stubFetch((url) =>
    url.includes("/canvas") ? { product_version: 1, nodes: [], edges: [] } : {},
  );

  const refreshed = await useSessionStore.getState().refreshCanvas(2);
  assert.equal(refreshed, false, "旧版本投影必须被丢弃");

  const state = useSessionStore.getState();
  assert.notEqual(state.canvas?.status, "generating", "丢弃旧投影后不能停在 generating");
  assert.equal(state.canvas?.status, "stale", "已有旧投影时标 stale（可重试）");
  assert.equal(state.canvasLoading, false);
  assert.equal(state.isGeneratingCanvas, false);
  assert.ok(state.stateWarnings.some((warning) => warning.includes("丢弃旧画布版本")));
});

// FE-05：告警要有生命周期，画布重试成功后不能继续挂旧错误
test("refreshCanvas：失败留下告警，重试成功后自动清除画布类告警", async () => {
  prime({ productVersion: 2, canvas: null });
  let attempt = 0;
  stubFetch((url) => {
    if (!url.includes("/canvas")) return {};
    attempt += 1;
    if (attempt === 1) throw new Error("画布投影读取失败");
    return { product_version: 2, nodes: [], edges: [] };
  });

  assert.equal(await useSessionStore.getState().refreshCanvas(), false);
  assert.ok(
    useSessionStore.getState().stateWarnings.some((warning) => warning.includes("画布加载失败")),
  );

  assert.equal(await useSessionStore.getState().refreshCanvas(), true);
  assert.equal(useSessionStore.getState().stateWarnings.length, 0, "恢复成功后旧告警必须清掉");
});

// PROD-06：refreshCanvas 失败返回 false，调用方不得再弹「画布已刷新」
// ---------------------------------------------------------------------------
test("refreshCanvas：读取失败返回 false 并保留「画布加载失败」提示", async () => {
  prime({ productVersion: 2, canvas: null });
  stubFetch(() => {
    throw new Error("画布投影读取失败");
  });

  const refreshed = await useSessionStore.getState().refreshCanvas();
  assert.equal(refreshed, false);

  const state = useSessionStore.getState();
  assert.equal(state.canvas?.status, "error");
  assert.ok(state.stateWarnings.some((warning) => warning.includes("画布加载失败")));
});

test("generateCanvas：v2 刷新失败不得弹「画布已刷新」成功 toast", async () => {
  const toastModule = require(path.join(BUILD_DIR, "components", "Toast.js"));
  const successCalls = [];
  const originalSuccess = toastModule.toast.success;
  toastModule.toast.success = (message) => successCalls.push(message);

  try {
    prime({ legacyMode: false, productVersion: 2, canvas: null });
    stubFetch(() => {
      throw new Error("画布投影读取失败");
    });

    await useSessionStore.getState().generateCanvas();

    assert.deepEqual(successCalls, [], "失败时不能出现成功 toast");
    assert.ok(
      useSessionStore.getState().stateWarnings.some((warning) => warning.includes("画布加载失败"))
    );
  } finally {
    toastModule.toast.success = originalSuccess;
  }
});

test("发送失败回滚乐观消息；重试复用同一 Idempotency-Key，成功后不误报失败（FE-01/FE-03/DX-05）", async () => {
  prime({
    phase: "brainstorm",
    v2Phase: "brainstorming",
    sessionView: { ...SESSION_VIEW, phase: "brainstorming" },
  });

  const commandCalls = [];
  let commandCount = 0;

  const jsonRes = (payload, status = 200) => ({
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => "application/json" },
    json: async () => payload,
    text: async () => JSON.stringify(payload),
  });

  const sseRes = (frame) => {
    const encoder = new global.TextEncoder();
    let sent = false;
    return {
      ok: true,
      status: 200,
      headers: { get: () => "text/event-stream" },
      json: async () => ({}),
      text: async () => "",
      body: {
        getReader() {
          return {
            read: async () => {
              if (sent) return { done: true, value: undefined };
              sent = true;
              return { done: false, value: encoder.encode(frame) };
            },
            cancel: async () => undefined,
          };
        },
      },
    };
  };

  const HUMAN_MESSAGE = messageView(1, {
    id: "m_human",
    content: "看下动效",
    stage: "brainstorm",
    run_id: "run_r",
  });
  const SNAPSHOT = {
    run_id: "run_r",
    session_id: "s1",
    action: "brainstorm",
    status: "completed",
    base_session_version: 3,
    base_product_version: 2,
    result_product_version: 2,
    steps: [],
    messages: [HUMAN_MESSAGE],
    session_version: 4,
    product_version: 2,
    canvas: null,
    audit: null,
    loop: null,
    loop_id: null,
    snapshot_event_seq: 1,
    latest_event_seq: 1,
  };

  global.fetch = async (url, init = {}) => {
    const href = String(url);
    if (href.includes("/commands")) {
      commandCount += 1;
      const body = JSON.parse(init.body);
      commandCalls.push({
        requestId: body.request_id,
        idempotencyKey: init.headers["Idempotency-Key"],
        target: body.payload && body.payload.target,
      });
      if (commandCount === 1) {
        return jsonRes(
          { error: { code: "VERSION_CONFLICT", message: "内容已在别处更新，请刷新后重试", retryable: false } },
          409,
        );
      }
      return jsonRes(
        {
          run_id: "run_r",
          session_id: "s1",
          status: "queued",
          action: "brainstorm",
          session_version: 4,
          audit_id: null,
        },
        202,
      );
    }
    if (href.includes("/events")) {
      const frame = `event: run.completed\ndata: ${JSON.stringify({
        type: "run.completed",
        run_id: "run_r",
        session_id: "s1",
        event_seq: 1,
        occurred_at: new Date(0).toISOString(),
        payload: { session_version: 4 },
      })}\n\n`;
      return sseRes(frame);
    }
    if (href.includes("/snapshot")) return jsonRes(SNAPSHOT);
    if (href.match(/\/api\/v2\/runs\/[^/]+$/)) {
      return jsonRes({
        id: "run_r",
        session_id: "s1",
        action: "brainstorm",
        status: "completed",
        error_code: null,
        error_message: null,
        loop_id: null,
      });
    }
    if (href.match(/\/api\/v2\/sessions\/s1$/)) return jsonRes(SESSION_VIEW);
    return jsonRes({});
  };

  try {
    // 第一次发送：409 失败
    useSessionStore.getState().sendMessage("看下动效", "designer");
    await waitFor(() => useSessionStore.getState().lastFailedSend !== null, 3000);

    const afterFailure = useSessionStore.getState();
    assert.deepEqual(
      (afterFailure.messagesBySession.s1 ?? []).filter((view) => view.id.startsWith("local-")),
      [],
      "发送失败后必须立刻回滚乐观用户气泡（不能留到下一个 Run 收尾再删）",
    );
    assert.equal(afterFailure.error, "内容已在别处更新，请刷新后重试");
    assert.equal(afterFailure.lastFailedSend.targetRole, "designer", "定向消息不能被降级成群发");
    assert.equal(typeof afterFailure.lastFailedSend.requestId, "string", "失败条必须保存首次 request_id 供重试复用");

    // 用户点「重试」：等价 ChatPanel.retrySend
    const failed = afterFailure.lastFailedSend;
    afterFailure.clearLastFailedSend();
    useSessionStore.getState().sendMessage(failed.content, failed.targetRole, { requestId: failed.requestId });

    await waitFor(() => useSessionStore.getState().runs.run_r !== undefined, 3000);
    await waitFor(() => useSessionStore.getState().isStreaming === false, 5000);

    assert.equal(commandCalls.length, 2, "应恰好发出两次命令（失败 + 重试）");
    assert.equal(commandCalls[0].requestId, commandCalls[1].requestId, "重试必须复用首次 request_id");
    assert.equal(
      commandCalls[0].idempotencyKey,
      commandCalls[1].idempotencyKey,
      "重试的 Idempotency-Key 必须与首次一致，否则响应丢失后会重复执行命令",
    );
    assert.equal(commandCalls[1].target, "designer");

    const finalState = useSessionStore.getState();
    const finalHumans = (finalState.messagesBySession.s1 ?? []).filter(
      (view) => view.sender_kind === "human",
    );
    assert.equal(
      finalHumans.filter((view) => view.id.startsWith("local-")).length,
      0,
      "服务端回执到达后不能残留本地乐观占位",
    );
    assert.equal(finalHumans.filter((view) => view.id === "m_human").length, 1, "重试成功只应有一条真人消息");
    assert.equal(finalState.error, null, "重试成功后不得再报「消息发送未确认」");
    assert.equal(finalState.lastFailedSend, null, "重试成功后必须清空失败条，避免再次误点重试");
  } finally {
    useSessionStore.getState().unsubscribe();
    global.fetch = realFetch;
  }
});

test("v2 会话读取 200 非 JSON 时不静默降级 legacy，报可重试错误且刷新后恢复（FE-02）", async () => {
  prime({
    phase: "interview",
    v2Phase: null,
    sessionView: null,
    sessionLoading: false,
    legacyMode: false,
    error: null,
  });

  const requested = [];
  let sessionAttempts = 0;

  const jsonRes = (payload, status = 200) => ({
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => "application/json" },
    json: async () => payload,
    text: async () => JSON.stringify(payload),
  });

  global.fetch = async (url, init = {}) => {
    const href = String(url);
    requested.push(href);
    if (href.match(/\/api\/v2\/sessions\/s1$/)) {
      sessionAttempts += 1;
      if (sessionAttempts === 1) {
        // 网关/代理返回 HTML：HTTP 200 但正文不是 JSON
        return {
          ok: true,
          status: 200,
          headers: { get: () => "text/html" },
          json: async () => {
            throw new SyntaxError("Unexpected token '<', \"<html>\" is not valid JSON");
          },
          text: async () => "<html><body>gateway error</body></html>",
        };
      }
      return jsonRes({
        ...SESSION_VIEW,
        phase: "brainstorming",
        session_version: 4,
        current_audit_id: null,
      });
    }
    if (href.includes("/messages")) return jsonRes({ items: [], next_before_seq: null });
    if (href.includes("/product-state")) return jsonRes({ version: 2, dimensions: [], decisions: [] });
    if (href.includes("/canvas")) {
      return jsonRes({ session_id: "s1", product_version: 2, status: "ready", tree: null });
    }
    return jsonRes({});
  };

  try {
    await useSessionStore.getState().loadSession("s1");

    const failed = useSessionStore.getState();
    assert.equal(failed.legacyMode, false, "200 非 JSON 绝不能被当成 v1 端点缺失而静默降级");
    assert.notEqual(failed.error, null, "必须给出可见错误，不能让审计/Loop/简报入口静默消失");
    assert.equal(typeof failed.error, "string");
    assert.equal(failed.sessionLoading, false, "失败后必须退出加载态，重试按钮才可用");
    assert.equal(failed.sessionView, null);
    assert.equal(
      requested.filter((href) => href.includes("/api/session/s1")).length,
      0,
      "非 JSON 错误不得回落到 legacy /api/session/:id",
    );

    // 页面「重试」：第二次读到合法 v2 JSON，错误必须被清除且视图恢复
    await useSessionStore.getState().loadSession("s1");
    const recovered = useSessionStore.getState();
    assert.equal(recovered.error, null, "重试成功后必须清除上一次的读取错误");
    assert.equal(recovered.v2Phase, "brainstorming");
    assert.equal(recovered.sessionView?.phase, "brainstorming");
    assert.equal(recovered.legacyMode, false);
    assert.equal(recovered.sessionLoading, false);
  } finally {
    useSessionStore.getState().unsubscribe();
    global.fetch = realFetch;
  }
});


// --------------------------------------------------------------------------- //
// 第 5 轮回归：loop_version 单调 / 失败必须可见 / 新闭环指针立即切换
// --------------------------------------------------------------------------- //

function loopSnapshot(overrides = {}) {
  return {
    loop_id: "lp1",
    session_id: "s1",
    scope: "mvp",
    status: "awaiting_decision",
    current_round: 1,
    max_rounds: 3,
    target_score: 85,
    loop_version: 2,
    focus: "预算协同主流程",
    latest_run_id: "run_9",
    decision: null,
    decision_note: null,
    created_at: new Date(0).toISOString(),
    updated_at: new Date(0).toISOString(),
    closed_at: null,
    rounds: [],
    mvp_spec: null,
    validation: null,
    prototype: null,
    test_report: null,
    scorecard: null,
    ...overrides,
  };
}

test("hydrateLoop 丢弃更旧的 loop_version（不覆盖快照、不移动 loopId）", () => {
  prime();
  useSessionStore.setState({ loopId: "lp1", loops: {}, stateWarnings: [] });
  const store = useSessionStore.getState();

  store.hydrateLoop(loopSnapshot({ status: "awaiting_decision", loop_version: 3 }));
  const afterFresh = useSessionStore.getState();
  assert.equal(afterFresh.loops.lp1.loop_version, 3);
  assert.equal(afterFresh.loopId, "lp1");

  // 迟到的旧响应：如果被接受，决策条会退回 running 并锁死所有决策键
  store.hydrateLoop(loopSnapshot({ status: "running", loop_version: 2 }));
  const afterStale = useSessionStore.getState();
  assert.equal(afterStale.loops.lp1.loop_version, 3, "旧 loop_version 不得覆盖快照");
  assert.equal(afterStale.loops.lp1.status, "awaiting_decision");
  assert.ok(
    afterStale.stateWarnings.some((warning) => /loop_version=2/.test(warning)),
    "必须留下可见 warning，便于排查乱序响应",
  );

  // 非法版本（<1）同样拒绝
  store.hydrateLoop(loopSnapshot({ loop_version: 0 }));
  assert.equal(useSessionStore.getState().loops.lp1.loop_version, 3);
});

test("retryFailedRun 失败返回 false 且写错误（调用方不能提示成功）", async () => {
  prime({ lastFailedRunId: "run_bad" });
  global.fetch = async () => ({
    ok: false,
    status: 500,
    json: async () => ({ error: { code: "INTERNAL", message: "重试失败（测试桩）" } }),
    text: async () => JSON.stringify({ error: { code: "INTERNAL", message: "重试失败（测试桩）" } }),
    headers: { get: () => "application/json" },
  });

  try {
    const ok = await useSessionStore.getState().retryFailedRun("run_bad");
    assert.equal(ok, false, "重试失败必须返回 false，页面据此展示失败而不是成功提示");
    assert.notEqual(useSessionStore.getState().error, null);
  } finally {
    global.fetch = realFetch;
  }
});

test("cancelRun 失败返回 false（中止入口不能提示已中止）", async () => {
  prime({ activeRunId: "run_live" });
  global.fetch = async () => ({
    ok: false,
    status: 409,
    json: async () => ({ error: { code: "SESSION_BUSY", message: "会话正忙（测试桩）" } }),
    text: async () => JSON.stringify({ error: { code: "SESSION_BUSY", message: "会话忙" } }),
    headers: { get: () => "application/json" },
  });

  try {
    const ok = await useSessionStore.getState().cancelRun("run_live");
    assert.equal(ok, false);
    assert.notEqual(useSessionStore.getState().error, null);
  } finally {
    global.fetch = realFetch;
  }
});

test("startLoop 收到回执后立即把 current_loop_id 指向新闭环", async () => {
  prime({ sessionView: { ...SESSION_VIEW, phase: "brainstorming", current_loop_id: null } });
  const requested = [];
  global.fetch = async (url, init = {}) => {
    const href = String(url);
    requested.push(`${init.method ?? "GET"} ${href}`);
    const body = init.body ? JSON.parse(init.body) : null;
    if (href.includes("/commands")) {
      assert.equal(body.action, "start_loop");
      return {
        ok: true,
        status: 202,
        json: async () => ({
          run_id: "run_loop1",
          session_id: "s1",
          status: "queued",
          action: "start_loop",
          session_version: 4,
          loop_id: "lp1",
        }),
        text: async () => "{}",
        headers: { get: () => "application/json" },
      };
    }
    if (href.includes("/api/v2/loops/lp1")) {
      return {
        ok: true,
        status: 200,
        json: async () => loopSnapshot({ status: "running", loop_version: 1 }),
        text: async () => "{}",
        headers: { get: () => "application/json" },
      };
    }
    return {
      ok: true,
      status: 200,
      json: async () => ({}),
      text: async () => "{}",
      headers: { get: () => "application/json" },
    };
  };

  try {
    await useSessionStore.getState().startLoop({ scope: "mvp", maxRounds: 3, targetScore: 85 });
    const state = useSessionStore.getState();
    assert.equal(state.sessionView?.current_loop_id, "lp1", "首轮开始前页面必须认新闭环为当前闭环");
    assert.equal(state.loopId, "lp1");
    assert.ok(requested.some((entry) => entry.includes("/api/v2/loops/lp1")));
  } finally {
    global.fetch = realFetch;
  }
});

test.after(() => {
  if (realFetch) global.fetch = realFetch;
  else delete global.fetch;
});
