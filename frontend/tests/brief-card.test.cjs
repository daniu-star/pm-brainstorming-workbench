"use strict";

/**
 * BriefCard 本地草稿保护纯逻辑测试（node --test，无第三方 runner）。
 *
 * 被测模块：frontend/lib/briefDraft.ts（经 tsconfig.test.json 编译到
 * node_modules/.cache/test-build/lib/briefDraft.js）。本机 npm registry 不可达，
 * 不能安装 @testing-library / jsdom，所以把「服务端版本变化时怎么处理本地草稿」
 * 抽成纯函数，页面组件（frontend/components/brief/BriefCard.tsx）在
 * productState?.version 变化时调用它。
 *
 * 回归点：原实现依赖全局 productVersion 且无条件 setEdits({})，用户正在输入时
 * 收到 product_state.updated 会把草稿静默抹掉。
 */

const { test } = require("node:test");
const assert = require("node:assert/strict");

const draft = require("../node_modules/.cache/test-build/lib/briefDraft.js");

test("hasBriefEdits：只有真正输入过的字段才算草稿", () => {
  assert.equal(draft.hasBriefEdits({}), false);
  assert.equal(draft.hasBriefEdits(null), false);
  assert.equal(draft.hasBriefEdits(undefined), false);
  assert.equal(draft.hasBriefEdits({ target_user: "" }), true);
  assert.equal(draft.hasBriefEdits({ target_user: "独立开发者" }), true);
});

test("服务端版本更新时：有草稿必须保留并提示「覆盖 / 丢弃」", () => {
  const decision = draft.decideBriefDraftOnServerVersion({ target_user: "独立开发者" });
  assert.equal(decision.keepEdits, true, "不能静默清空用户输入");
  assert.equal(decision.notice, draft.BRIEF_STALE_NOTICE);
  assert.match(decision.notice, /服务端已更新/);
  assert.match(decision.notice, /覆盖/);
  assert.match(decision.notice, /丢弃/);
});

test("服务端版本更新时：没有草稿才允许清空", () => {
  const decision = draft.decideBriefDraftOnServerVersion({});
  assert.equal(decision.keepEdits, false);
  assert.equal(decision.notice, null);
});

const REQUIRED = ["target_users", "usage_scenario", "core_problem", "success_criteria"];

function briefWith(values) {
  const brief = {};
  for (const field of REQUIRED) {
    brief[field] = { value: values[field] ?? "", status: "confirmed" };
  }
  return brief;
}

test("missingRequiredBriefFields：服务端齐全且无编辑时不缺", () => {
  const brief = briefWith({
    target_users: "3-10 人财务团队",
    usage_scenario: "每周五下午写周报",
    core_problem: "手工汇总耗时 40 分钟",
    success_criteria: "5 分钟内出稿",
  });
  assert.deepEqual(draft.missingRequiredBriefFields(brief, {}, REQUIRED), []);
  assert.deepEqual(draft.missingRequiredBriefFields(brief, null, REQUIRED), []);
});

test("missingRequiredBriefFields：本地编辑清空必填项必须被判定缺失（AUD-02）", () => {
  const brief = briefWith({
    target_users: "3-10 人财务团队",
    usage_scenario: "每周五下午写周报",
    core_problem: "手工汇总耗时 40 分钟",
    success_criteria: "5 分钟内出稿",
  });
  const edits = { target_users: "", success_criteria: "   " };
  assert.deepEqual(
    draft.missingRequiredBriefFields(brief, edits, REQUIRED),
    ["target_users", "success_criteria"]
  );
  // 只有被清空的两项缺失，未被编辑的不做推断
  assert.deepEqual(
    draft.missingRequiredBriefFields(brief, { usage_scenario: "" }, REQUIRED),
    ["usage_scenario"]
  );
});

test("missingRequiredBriefFields：服务端本来就缺字段时也返回缺失", () => {
  const brief = briefWith({ target_users: "3-10 人财务团队" });
  assert.deepEqual(
    draft.missingRequiredBriefFields(brief, null, REQUIRED),
    ["usage_scenario", "core_problem", "success_criteria"]
  );
  assert.deepEqual(draft.missingRequiredBriefFields(null, null, REQUIRED), REQUIRED);
});

// ---------------------------------------------------------------------------
// UX-D2：简报动作按会话阶段门控（backend ACTION_PHASES[confirm_brief] 仅 clarifying）
// ---------------------------------------------------------------------------

test("briefActionsVisible：只有 clarifying 才渲染「确认简报 / 带假设进入脑暴」", () => {
  assert.equal(draft.briefActionsVisible("clarifying"), true);
  assert.equal(draft.briefActionsVisible({ phase: "clarifying" }), true);
  assert.equal(draft.briefActionsVisible({ v2Phase: "clarifying" }), true);
  for (const phase of ["brainstorming", "auditing", "audit_completed"]) {
    assert.equal(draft.briefActionsVisible(phase), false, phase);
  }
  assert.equal(draft.briefActionsVisible(null), false);
  assert.equal(draft.briefActionsVisible(undefined), false);
});

test("briefReviseAllowed：brainstorming / audit_completed 仍可修订，auditing 与未知阶段不可", () => {
  assert.equal(draft.briefReviseAllowed("clarifying"), true);
  assert.equal(draft.briefReviseAllowed("brainstorming"), true);
  assert.equal(draft.briefReviseAllowed("audit_completed"), true);
  assert.equal(draft.briefReviseAllowed({ phase: "audit_completed" }), true);
  assert.equal(draft.briefReviseAllowed("auditing"), false);
  assert.equal(draft.briefReviseAllowed("shipped"), false);
  // phase 未知时放行，避免旧会话/加载中被误锁
  assert.equal(draft.briefReviseAllowed(null), true);
});

test("briefPhaseNotice：非 clarifying 阶段给出只读说明，audit_completed 显式提示阶段回退", () => {
  assert.equal(draft.briefPhaseNotice("clarifying"), null);
  assert.equal(draft.briefPhaseNotice(null), null);
  const brainstorming = draft.briefPhaseNotice("brainstorming");
  assert.ok(brainstorming.includes("脑暴阶段"));
  const auditing = draft.briefPhaseNotice("auditing");
  assert.ok(auditing.includes("审计进行中"));
  const auditCompleted = draft.briefPhaseNotice("audit_completed");
  assert.ok(auditCompleted.includes("回退到「脑暴」"));
  assert.ok(auditCompleted.includes("重新审计"));
  assert.ok(draft.briefPhaseNotice("shipped").includes("不允许"));
});

// --------------------------------------------------------------------------- //
// PRD-02：已确认简报被改回 ready 时如实说明「当前阶段无确认入口」
// --------------------------------------------------------------------------- //

test("briefReconfirmNotice 只在 ready 且非 clarifying 阶段给出说明", () => {
  const view = require("../node_modules/.cache/test-build/lib/briefDraft.js");
  assert.match(
    view.briefReconfirmNotice({ status: "ready", phase: "brainstorming" }) ?? "",
    /当前阶段不提供再次确认入口/,
  );
  assert.match(
    view.briefReconfirmNotice({ status: "ready", phase: "audit_completed" }) ?? "",
    /必填字段已更新/,
  );
  // clarifying 阶段仍可正常点「确认」，不应出现误导说明
  assert.equal(view.briefReconfirmNotice({ status: "ready", phase: "clarifying" }), null);
  assert.equal(view.briefReconfirmNotice({ status: "confirmed", phase: "brainstorming" }), null);
  assert.equal(view.briefReconfirmNotice({ status: "ready", phase: null }), null);
});
