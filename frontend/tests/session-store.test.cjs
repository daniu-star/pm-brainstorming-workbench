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

test.after(() => {
  if (realFetch) global.fetch = realFetch;
  else delete global.fetch;
});
