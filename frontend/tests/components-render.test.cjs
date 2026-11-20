/**
 * 真实渲染测试（react-dom/server 的 renderToStaticMarkup）。
 *
 * 背景（TST-01）：此前页面/组件只有「全文件 token 正则」级别的 wiring 断言，
 * 删掉一个条件渲染分支测试仍然是绿的。本机 npm registry 不可达、装不了
 * jsdom / RTL，但 react-dom 已在依赖里，因此这里用**服务端真渲染**：
 * 把组件编译成 CJS（tsconfig.render.json）后 renderToStaticMarkup，断言真实
 * 输出的 DOM 文本与结构。它不能替代浏览器交互测试，但确实能证明组件会渲染、
 * 分支会产生可观察的 DOM 差异。
 */
"use strict";

const path = require("node:path");
const Module = require("node:module");
const test = require("node:test");
const assert = require("node:assert/strict");

const BUILD_ROOT = path.join(process.cwd(), "node_modules", ".cache", "render-build");
const originalResolve = Module._resolveFilename;
Module._resolveFilename = function (request, parent, ...rest) {
  if (typeof request === "string" && request.startsWith("@/")) {
    request = path.join(BUILD_ROOT, request.slice(2));
  }
  return originalResolve.call(this, request, parent, ...rest);
};

const React = require("react");

// zustand 的 useStore 走 useSyncExternalStore；在 SSR（renderToStaticMarkup）下
// React 会取 getServerSnapshot，而 zustand 的 server snapshot 恒为「初始状态」，
// 于是 setState 播种的数据看不到。这里把 server snapshot 指向 getSnapshot，
// 让 SSR 读到当前 store 状态 —— 这正是我们要断言的「给定状态 → 渲染结果」。
const originalUseSyncExternalStore = React.useSyncExternalStore;
React.useSyncExternalStore = (subscribe, getSnapshot) =>
  originalUseSyncExternalStore(subscribe, getSnapshot, getSnapshot);

const { renderToStaticMarkup } = require("react-dom/server");

const { MessageList } = require(path.join(BUILD_ROOT, "components/chat/MessageList.js"));
const { BriefCard } = require(path.join(BUILD_ROOT, "components/brief/BriefCard.js"));
const { useSessionStore } = require(path.join(BUILD_ROOT, "store/sessionStore.js"));

// 渲染前统一把 store 重置成「空会话」，避免用例间状态串味。
function resetStore(overrides = {}) {
  useSessionStore.setState({
    currentSessionId: null,
    messages: [],
    messagesBySession: {},
    messagePages: {},
    earlierLoading: {},
    isStreaming: false,
    streamingContent: "",
    streamingRole: null,
    streamingMessageId: null,
    sessionView: null,
    productState: null,
    v2Phase: null,
    sessionLoading: false,
    error: null,
    ...overrides,
  });
}

function render(element) {
  return renderToStaticMarkup(element);
}

function brief({ status = "draft", overrides = {}, allConfirmed = false } = {}) {
  const base = allConfirmed ? "confirmed" : "proposed";
  const field = (value, fieldStatus = base) => ({ value, status: fieldStatus, source_message_ids: [] });
  return {
    target_users: field("中小企业财务团队", status === "confirmed" ? "confirmed" : "proposed"),
    usage_scenario: field("每月对账", status === "confirmed" ? "confirmed" : "proposed"),
    core_problem: field("对账靠人肉 Excel，容易漏", status === "confirmed" ? "confirmed" : "proposed"),
    current_alternatives: field("Excel + 手工核对"),
    product_form: field("浏览器插件"),
    constraints: field("三个月上线"),
    success_criteria: field("对账时间缩短一半", status === "confirmed" ? "confirmed" : "proposed"),
    status,
    skipped: false,
    ...overrides,
  };
}

// --------------------------------------------------------------------------- //
// MessageList
// --------------------------------------------------------------------------- //

test("MessageList 空会话渲染引导文案", () => {
  resetStore();
  const html = render(React.createElement(MessageList));
  assert.match(html, /准备开始脑暴/);
  assert.match(html, /还没有消息/);
});

test("MessageList 渲染 v2 消息：内容 / 角色名 / 阶段标签", () => {
  const view = (id, seq, senderKind, content, extra = {}) => ({
    id,
    session_id: "s1",
    seq,
    sender_kind: senderKind,
    role_code: extra.role_code ?? null,
    stage: extra.stage ?? "clarify",
    content,
    status: "completed",
    run_id: null,
    audit_id: null,
    created_at: "2026-11-20T00:00:00Z",
  });
  resetStore({
    currentSessionId: "s1",
    messagesBySession: {
      s1: [
        view("m1", 1, "human", "我们做中小企业对账工具", {}),
        view("m2", 2, "agent", "先确认一下：目标用户具体是财务还是老板？", {
          role_code: "coach",
          stage: "clarify",
        }),
        view("m3", 3, "agent", "我建议从对账差异检测切入。", {
          role_code: "cto",
          stage: "brainstorm",
        }),
      ],
    },
  });

  const html = render(React.createElement(MessageList));
  assert.match(html, /中小企业对账工具/, "用户消息必须渲染");
  assert.match(html, /目标用户具体是财务还是老板/, "助手消息必须渲染");
  assert.match(html, /产品教练/, "coach 应显示中文角色名");
  assert.match(html, /技术负责人/, "cto 应显示中文角色名");
  assert.match(html, /澄清/, "clarify 阶段标签必须渲染");
  assert.match(html, /脑暴/, "brainstorm 阶段标签必须渲染");
});

test("MessageList 有更早消息时渲染「加载更早」按钮，没有时不渲染", () => {
  resetStore({
    currentSessionId: "s1",
    messagesBySession: {
      s1: [
        {
          id: "m1",
          session_id: "s1",
          seq: 5,
          sender_kind: "human",
          role_code: null,
          stage: "clarify",
          content: "第五条消息",
          status: "completed",
          run_id: null,
          audit_id: null,
          created_at: "2026-11-20T00:00:00Z",
        },
      ],
    },
    messagePages: { s1: { hasEarlier: true, earliestSeq: 5 } },
  });
  const withEarlier = render(React.createElement(MessageList));
  assert.match(withEarlier, /更早/, "应该出现加载更早的入口");

  resetStore({
    currentSessionId: "s1",
    messagesBySession: { s1: [] },
    messagePages: { s1: { hasEarlier: false, earliestSeq: 1 } },
  });
  const withoutEarlier = render(React.createElement(MessageList));
  assert.doesNotMatch(withoutEarlier, /更早/);
});

// --------------------------------------------------------------------------- //
// BriefCard
// --------------------------------------------------------------------------- //

function seedSession({ phase = "clarifying", briefStatus = "draft", productBrief, productVersion = 1 }) {
  resetStore({
    currentSessionId: "s1",
    v2Phase: phase,
    sessionView: {
      id: "s1",
      owner_email: "owner@example.com",
      phase,
      session_version: 3,
      current_product_version: productVersion,
      current_audit_id: null,
      active_run_id: null,
      problem_statement: "我想做对账工具",
      brief_status: briefStatus,
      current_loop_id: null,
      created_at: "2026-11-20T00:00:00Z",
      updated_at: "2026-11-20T00:00:00Z",
      deleted_at: null,
      message_count: 2,
    },
    productState: {
      session_id: "s1",
      version: productVersion,
      schema_version: 1,
      brief: productBrief,
      claims: [],
      options: [],
      decisions: [],
      experiments: [],
      unresolved_issues: [],
      source_run_id: null,
      created_at: "2026-11-20T00:00:00Z",
    },
  });
}

test("BriefCard 澄清阶段渲染字段与「确认简报」入口", () => {
  seedSession({ phase: "clarifying", briefStatus: "draft", productBrief: brief({ status: "draft" }) });
  const html = render(React.createElement(BriefCard));
  assert.match(html, /目标用户/, "字段标签必须渲染");
  assert.match(html, /中小企业财务团队/, "字段值必须渲染");
  assert.match(html, />确认简报</, "clarifying 阶段必须出现确认入口");
});

test("BriefCard 已进入脑暴阶段时不再出现确认入口，而是阶段说明", () => {
  seedSession({
    phase: "brainstorming",
    briefStatus: "confirmed",
    productBrief: brief({ status: "confirmed" }),
  });
  const html = render(React.createElement(BriefCard));
  assert.doesNotMatch(html, />确认简报</, "非澄清阶段不得出现确认入口（点了必被状态机拒绝）");
  assert.doesNotMatch(html, />跳过澄清</, "非澄清阶段不得出现跳过入口");
  assert.match(html, /已进入脑暴阶段/, "必须给出阶段说明");
});

test("BriefCard 已确认简报若仍有「待确认」字段，渲染真实再确认入口（历史数据兜底）", () => {
  seedSession({
    phase: "brainstorming",
    briefStatus: "confirmed",
    productBrief: brief({
      status: "confirmed",
      // 模拟旧数据：brief 已确认，但 current_alternatives 仍停 proposed（AUD 第 4 轮的真实场景）
      overrides: { current_alternatives: { value: "Excel + 手工核对", status: "proposed", source_message_ids: [] } },
    }),
  });
  const html = render(React.createElement(BriefCard));
  assert.match(html, /简报已确认，但以下字段仍停留在「待确认」/, "应给出字段漂移说明");
  assert.match(html, /待并入：/, "应指出具体字段");
  assert.match(html, /确认字段修订/, "必须提供可点击的再确认入口");
});

test("BriefCard 已确认且无漂移时不渲染漂移提示", () => {
  seedSession({
    phase: "brainstorming",
    briefStatus: "confirmed",
    productBrief: brief({ status: "confirmed", allConfirmed: true }),
  });
  const html = render(React.createElement(BriefCard));
  assert.doesNotMatch(html, /简报已确认，但以下字段仍停留在/);
});
