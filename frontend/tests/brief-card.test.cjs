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

function valuesBriefWith(values) {
  const brief = {};
  for (const field of REQUIRED) {
    brief[field] = { value: values[field] ?? "", status: "confirmed" };
  }
  return brief;
}

test("missingRequiredBriefFields：服务端齐全且无编辑时不缺", () => {
  const brief = valuesBriefWith({
    target_users: "3-10 人财务团队",
    usage_scenario: "每周五下午写周报",
    core_problem: "手工汇总耗时 40 分钟",
    success_criteria: "5 分钟内出稿",
  });
  assert.deepEqual(draft.missingRequiredBriefFields(brief, {}, REQUIRED), []);
  assert.deepEqual(draft.missingRequiredBriefFields(brief, null, REQUIRED), []);
});

test("missingRequiredBriefFields：本地编辑清空必填项必须被判定缺失（AUD-02）", () => {
  const brief = valuesBriefWith({
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
  const brief = valuesBriefWith({ target_users: "3-10 人财务团队" });
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
// P1-4：已确认简报的字段漂移必须可再确认（不再是不可达的说明文案）
// --------------------------------------------------------------------------- //

function briefWith(overrides = {}) {
  const base = {};
  for (const field of [
    "target_users",
    "usage_scenario",
    "core_problem",
    "current_alternatives",
    "product_form",
    "constraints",
    "success_criteria",
  ]) {
    base[field] = { value: `${field} 内容`, status: "confirmed", source_message_ids: [] };
  }
  return { ...base, status: "confirmed", skipped: false, ...overrides };
}

test("briefFieldsAwaitingConfirm：只挑出已确认简报里非 confirmed 的字段", () => {
  assert.deepEqual(draft.briefFieldsAwaitingConfirm(briefWith()), []);
  assert.deepEqual(
    draft.briefFieldsAwaitingConfirm(briefWith({ target_users: { value: "新用户", status: "proposed", source_message_ids: [] } })),
    ["target_users"],
  );
  // 未确认/已跳过简报不产生「再确认」入口
  assert.deepEqual(draft.briefFieldsAwaitingConfirm(briefWith({ status: "ready" })), []);
  assert.deepEqual(draft.briefFieldsAwaitingConfirm(briefWith({ status: "skipped" })), []);
  assert.deepEqual(draft.briefFieldsAwaitingConfirm(null), []);
  // unknown 字段不算漂移（还没填过，不该催确认）
  assert.deepEqual(
    draft.briefFieldsAwaitingConfirm(briefWith({ constraints: { value: null, status: "unknown", source_message_ids: [] } })),
    [],
  );
  // 字段整体缺失（旧数据）也不算漂移：没有内容可并入
  const partial = briefWith();
  delete partial.success_criteria;
  assert.deepEqual(draft.briefFieldsAwaitingConfirm(partial), []);
});

test("BRIEF_FIELD_DRIFT_NOTICE 指向真实存在的「确认字段修订」入口", () => {
  assert.match(draft.BRIEF_FIELD_DRIFT_NOTICE, /确认字段修订/);
  assert.match(draft.BRIEF_FIELD_DRIFT_NOTICE, /待确认/);
});
