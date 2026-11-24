/**
 * 页面级真实渲染测试（react-dom/server 的 renderToStaticMarkup）。
 *
 * 背景：audit / loop 两个页面此前只有 `tests/audit-page.test.cjs` 与
 * `tests/loop-page.test.cjs` 的「纯函数 + 文件 token」断言——页面里的条件渲染
 * 分支被删掉时它们仍然是绿的。本机装不了 jsdom / RTL（npm registry 不可达），
 * 但 react-dom 在依赖里，因此这里把**整个页面组件**编译成 CJS 后做服务端渲染，
 * 断言真实 DOM 文本。
 *
 * 需要两个 Next.js 运行时替身（页面是 "use client" 组件，直接依赖它们）：
 * - `next/navigation`：useParams / useSearchParams / useRouter；
 * - `next/link`：渲染成普通 <a>。
 */
"use strict";

const path = require("node:path");
const Module = require("node:module");
const test = require("node:test");
const assert = require("node:assert/strict");

const BUILD_ROOT = path.join(process.cwd(), "node_modules", ".cache", "render-build");
const SESSION_ID = "a".repeat(32);

const originalResolve = Module._resolveFilename;
Module._resolveFilename = function (request, parent, ...rest) {
  if (typeof request === "string" && request.startsWith("@/")) {
    request = path.join(BUILD_ROOT, request.slice(2));
  }
  return originalResolve.call(this, request, parent, ...rest);
};

const React = require("react");
const originalLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (request === "next/navigation") {
    return {
      useParams: () => ({ id: SESSION_ID }),
      useSearchParams: () => new URLSearchParams(),
      useRouter: () => ({
        push() {},
        replace() {},
        refresh() {},
        prefetch() {},
        back() {},
      }),
      usePathname: () => `/session/${SESSION_ID}/audit`,
      redirect() {},
      notFound() {},
    };
  }
  if (request === "next/link") {
    return function Link(props) {
      const { href, children, ...rest } = props;
      return React.createElement("a", { href: typeof href === "string" ? href : "#", ...rest }, children);
    };
  }
  return originalLoad.call(this, request, parent, isMain);
};

// zustand 的 useStore 在 SSR 下取 getServerSnapshot（恒为初始状态），
// 把它指到 getSnapshot 才能断言「给定 store 状态 → 页面渲染结果」。
const originalUseSyncExternalStore = React.useSyncExternalStore;
React.useSyncExternalStore = (subscribe, getSnapshot) =>
  originalUseSyncExternalStore(subscribe, getSnapshot, getSnapshot);

const { renderToStaticMarkup } = require("react-dom/server");
const { useSessionStore } = require(path.join(BUILD_ROOT, "store/sessionStore.js"));
const { AUDIT_LOAD_ERROR_TEXT } = require(path.join(BUILD_ROOT, "lib/auditView.js"));

const AuditPage = require(path.join(BUILD_ROOT, "app/session/[id]/audit/page.js")).default;
const LoopPage = require(path.join(BUILD_ROOT, "app/session/[id]/loop/page.js")).default;

function resetStore(overrides) {
  useSessionStore.setState({
    sessionId: SESSION_ID,
    sessionView: null,
    sessionLoading: false,
    sessionError: null,
    messagesBySession: {},
    audits: {},
    auditErrors: {},
    loops: {},
    loopErrors: {},
    loopLoading: false,
    loopId: null,
    legacyMode: false,
    v2Phase: null,
    currentSessionId: SESSION_ID,
    runs: {},
    ...overrides,
  });
}

function render(element) {
  return renderToStaticMarkup(element);
}

const COMPLETED_AUDIT = {
  id: "audit-1",
  session_id: SESSION_ID,
  status: "completed",
  product_version: 2,
  audit_version: 3,
  question_count: 1,
  end_reason: "completed",
  close_requested: true,
  dimension_states: [
    { key: "problem_validity", assessment: "supported", rationale: "已澄清目标用户是中小企业财务团队" },
  ],
  questions: [
    {
      id: "q1",
      ordinal: 1,
      dimension: "problem_validity",
      question_text: "谁是目标用户？",
      assessment: "supported",
      rationale: "用户已给出明确回答",
      evidence_refs: [],
    },
  ],
  report: {
    summary: "整体判断：方向可行，但用户证据仍需补齐。",
    dimension_states: [
      { key: "problem_validity", assessment: "supported", rationale: "已澄清目标用户是中小企业财务团队" },
    ],
    supported_points: ["目标用户明确"],
    unresolved_gaps: ["缺少付费意愿证据"],
    recommended_experiments: ["做 5 家试点"],
    limitations: "样本有限",
    generated_by: "model",
  },
};

test("审计页：已完成审计渲染报告摘要、维度评估与问题原文", () => {
  resetStore({
    sessionView: {
      id: SESSION_ID,
      phase: "audit_completed",
      session_version: 4,
      current_product_version: 2,
      current_audit_id: "audit-1",
      active_run_id: null,
    },
    audits: { "audit-1": COMPLETED_AUDIT },
  });

  const html = render(React.createElement(AuditPage));

  assert.match(html, /专家审计/);
  assert.match(html, /报告摘要/);
  assert.match(html, /整体判断：方向可行/);
  assert.match(html, /维度评估/);
  assert.match(html, /已澄清目标用户是中小企业财务团队/);
  assert.match(html, /谁是目标用户？/);
  assert.match(html, /目标用户明确/);
  assert.match(html, /缺少付费意愿证据/);
});

test("审计页：报告含未验证数字时顶部给徽标与补充建议", () => {
  const audit = JSON.parse(JSON.stringify(COMPLETED_AUDIT));
  audit.report.unverified_numbers = ["2", "66"];
  audit.report.verification_suggestion = "未验证数字：2、66。请补充这些数字的来源与统计日期。";

  resetStore({
    sessionView: {
      id: SESSION_ID,
      phase: "audit_completed",
      session_version: 4,
      current_product_version: 2,
      current_audit_id: "audit-1",
      active_run_id: null,
    },
    audits: { "audit-1": audit },
  });

  const html = render(React.createElement(AuditPage));

  assert.match(html, /数字未验证/);
  assert.match(html, /2、66/);
});

test("审计页：未验证数字旁有「补充来源」入口（可点开表单）", () => {
  const audit = JSON.parse(JSON.stringify(COMPLETED_AUDIT));
  audit.report.unverified_numbers = ["2", "66"];

  resetStore({
    sessionView: {
      id: SESSION_ID,
      phase: "audit_completed",
      session_version: 4,
      current_product_version: 2,
      current_audit_id: "audit-1",
      active_run_id: null,
    },
    audits: { "audit-1": audit },
  });

  const html = render(React.createElement(AuditPage));
  // 徽标旁必须是按钮而不是纯文字：第 29 批把它变成可操作入口
  assert.match(html, /<button[^>]*>补充来源<\/button>/);
  // 默认收起，因此首屏不渲染表单字段
  assert.doesNotMatch(html, /aria-label="证据摘录"/);
});

test("审计页：读取失败渲染错误态与重试入口，不渲染「开始审计」", () => {
  resetStore({
    sessionView: {
      id: SESSION_ID,
      phase: "auditing",
      session_version: 3,
      current_product_version: 2,
      current_audit_id: "audit-1",
      active_run_id: null,
    },
    audits: {},
    auditErrors: { "audit-1": { code: "INTERNAL", message: "boom" } },
  });

  const html = render(React.createElement(AuditPage));

  assert.match(html, new RegExp(AUDIT_LOAD_ERROR_TEXT));
  assert.doesNotMatch(html, /开始审计/);
});

test("审计页：会话未加载时禁用开始按钮并给出可恢复提示", () => {
  resetStore({ sessionView: null, sessionLoading: true });

  const html = render(React.createElement(AuditPage));

  // SSR 下没有 effect，页面走「会话状态尚未加载」分支：按钮禁用 + 重新加载入口
  assert.match(html, /会话状态尚未加载，暂时无法启动审计。/);
  assert.match(html, /重新加载会话/);
  assert.match(html, /disabled/);
});

test("闭环页：阶段不满足时渲染门槛说明而不是空白页", () => {
  resetStore({
    sessionView: {
      id: SESSION_ID,
      phase: "auditing",
      session_version: 3,
      current_product_version: 2,
      current_audit_id: "audit-1",
      active_run_id: null,
    },
    v2Phase: "auditing",
  });

  const html = render(React.createElement(LoopPage));

  assert.match(html, /闭环工作室/);
  assert.match(html, /需要先完成头脑风暴或审计，才能开启闭环工作室/);
  assert.doesNotMatch(html, /启动闭环工作室/);
});

test("闭环页：允许开启时渲染启动面板与五阶段说明", () => {
  resetStore({
    sessionView: {
      id: SESSION_ID,
      phase: "audit_completed",
      session_version: 5,
      current_product_version: 3,
      current_audit_id: "audit-1",
      active_run_id: null,
    },
    v2Phase: "audit_completed",
  });

  const html = render(React.createElement(LoopPage));

  assert.match(html, /闭环工作室/);
  assert.match(html, /启动闭环工作室/);
  assert.doesNotMatch(html, /需要先完成头脑风暴或审计/);
});

test("闭环页：会话加载中渲染加载态而不是空白页", () => {
  // 注：SSR 里 useEffect 不执行，页面本地 state sessionSettled 恒为 false，
  // 因此「读取失败」分支（依赖 effect 收尾）只能由浏览器 e2e 覆盖（AC-01/31）。
  resetStore({ sessionView: null, sessionLoading: true });

  const html = render(React.createElement(LoopPage));

  assert.match(html, /正在读取会话与闭环状态/);
  assert.doesNotMatch(html, /需要先完成头脑风暴或审计/);
});
