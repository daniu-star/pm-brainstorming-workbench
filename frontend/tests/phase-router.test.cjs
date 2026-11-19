"use strict";

/**
 * v2 阶段路由纯函数测试（node --test，无第三方 runner）。
 *
 * 被测模块：frontend/lib/phaseRouter.ts（经 tsconfig.test.json 编译到
 * node_modules/.cache/test-build/lib/phaseRouter.js）。
 *
 * 背景：旧 phase 只有 define/coach/brainstorm/interview，审计结束后仍是
 * interview，输入框会误走 answer_audit（后端只允许 auditing，见
 * backend/domain/state_machine.py:44）。这里锁定三条路由规则。
 */

const { test } = require("node:test");
const assert = require("node:assert/strict");

const router = require("../node_modules/.cache/test-build/lib/phaseRouter.js");

test("isAuditAnswerPhase 只在 auditing 为 true（audit_completed 必须走普通消息）", () => {
  assert.equal(router.isAuditAnswerPhase("auditing"), true);
  assert.equal(router.isAuditAnswerPhase("audit_completed"), false);
  assert.equal(router.isAuditAnswerPhase("brainstorming"), false);
  assert.equal(router.isAuditAnswerPhase("clarifying"), false);
  assert.equal(router.isAuditAnswerPhase(null), false);
  assert.equal(router.isAuditAnswerPhase(undefined), false);
});

test("messageActionForPhase：clarifying 澄清，其余（含 audit_completed）都发 brainstorm", () => {
  assert.equal(router.messageActionForPhase("clarifying"), "clarify");
  assert.equal(router.messageActionForPhase("brainstorming"), "brainstorm");
  assert.equal(router.messageActionForPhase("auditing"), "brainstorm");
  // 审计结束后继续讨论：state_machine.py:41 允许 brainstorm ∈ {brainstorming, audit_completed}
  assert.equal(router.messageActionForPhase("audit_completed"), "brainstorm");
  assert.equal(router.messageActionForPhase(null), "brainstorm");
  // 任何阶段都不应该由文本发送产生 answer_audit（只能来自审计页的显式提交）
  for (const phase of ["clarifying", "brainstorming", "auditing", "audit_completed", null]) {
    assert.notEqual(router.messageActionForPhase(phase), "answer_audit");
  }
});

test("canStartAuditPhase：brainstorming / audit_completed 可发起（重新）审计", () => {
  assert.equal(router.canStartAuditPhase("brainstorming"), true);
  assert.equal(router.canStartAuditPhase("audit_completed"), true);
  assert.equal(router.canStartAuditPhase("auditing"), false);
  assert.equal(router.canStartAuditPhase("clarifying"), false);
  assert.equal(router.canStartAuditPhase(null), false);
  assert.equal(router.canStartAuditPhase(undefined), false);
});

test("shouldShowStartDiscussion：brainstorm 产物是持久证据，刷新后不再提示「开始讨论」", () => {
  // 刷新后内存 runs 为空，但会话消息里已经有 stage=brainstorm 的 agent 消息
  const messages = [
    { sender_kind: "human", stage: "brainstorm" },
    { sender_kind: "agent", stage: "brainstorm" },
  ];
  assert.equal(router.hasBrainstormMessages(messages), true);
  assert.equal(
    router.shouldShowStartDiscussion({
      v2Phase: "brainstorming",
      phase: "brainstorm",
      hasBrainstormRun: false,
      messages,
    }),
    false
  );
  // 仅有用户消息不算产物
  assert.equal(
    router.shouldShowStartDiscussion({
      v2Phase: "brainstorming",
      phase: "brainstorm",
      hasBrainstormRun: false,
      messages: [{ sender_kind: "human", stage: "brainstorm" }],
    }),
    true
  );
  // 非 brainstorming 阶段不显示
  assert.equal(
    router.shouldShowStartDiscussion({
      v2Phase: "audit_completed",
      phase: "interview",
      hasBrainstormRun: false,
      messages: [],
    }),
    false
  );
  // 本次会话刚点过（hasBrainstormRun）也不显示，避免重复提交第二个 brainstorm Run
  assert.equal(
    router.shouldShowStartDiscussion({
      v2Phase: "brainstorming",
      phase: "brainstorm",
      hasBrainstormRun: true,
      messages: [],
    }),
    false
  );
  // 旧 phase 兜底：v2Phase 为空时用 phase 判断
  assert.equal(
    router.shouldShowStartDiscussion({
      v2Phase: null,
      phase: "brainstorm",
      hasBrainstormRun: false,
      messages: [],
    }),
    true
  );
});

// ---------------------------------------------------------------------------
// PROD-01 / PROD-03：画布决策确认的阶段门控
// ---------------------------------------------------------------------------

test("canConfirmDecisionPhase：只有 brainstorming / audit_completed 允许 confirm_decision", () => {
  assert.equal(router.canConfirmDecisionPhase("brainstorming"), true);
  assert.equal(router.canConfirmDecisionPhase("audit_completed"), true);
  assert.equal(router.canConfirmDecisionPhase("auditing"), false);
  assert.equal(router.canConfirmDecisionPhase("clarifying"), false);
  assert.equal(router.canConfirmDecisionPhase(null), false);
  assert.equal(router.canConfirmDecisionPhase(undefined), false);
});

test("decisionActionGate：proposed + brainstorming + 有效版本才可点", () => {
  const gate = router.decisionActionGate({
    status: "proposed",
    phase: "brainstorming",
    productVersion: 3,
  });
  assert.deepEqual(gate, { visible: true, enabled: true, reason: null });
  assert.equal(
    router.decisionActionGate({ status: "proposed", phase: "audit_completed", productVersion: 1 }).enabled,
    true
  );
});

test("decisionActionGate：已确认/已否决的决策不再渲染入口", () => {
  for (const status of ["confirmed", "rejected", "superseded"]) {
    assert.equal(
      router.decisionActionGate({ status, phase: "brainstorming", productVersion: 2 }).visible,
      false,
      status
    );
  }
  assert.equal(router.decisionActionGate({ status: null, phase: "brainstorming", productVersion: 2 }).visible, false);
});

test("decisionActionGate：auditing 阶段禁用并给出「先结束审计」原因（后端必 INVALID_REQUEST）", () => {
  const gate = router.decisionActionGate({
    status: "proposed",
    phase: "auditing",
    productVersion: 2,
  });
  assert.equal(gate.visible, true);
  assert.equal(gate.enabled, false);
  assert.ok(gate.reason.includes("当前阶段不能确认决策"));
  assert.equal(
    router.decisionActionGate({ status: "proposed", phase: "clarifying", productVersion: 2 }).enabled,
    false
  );
});

test("decisionActionGate：阶段或产品版本未加载时禁用而非隐藏，给用户可读原因", () => {
  const noPhase = router.decisionActionGate({ status: "proposed", phase: null, productVersion: 2 });
  assert.equal(noPhase.visible, true);
  assert.equal(noPhase.enabled, false);
  assert.ok(noPhase.reason.includes("会话阶段尚未加载"));

  const noVersion = router.decisionActionGate({ status: "proposed", phase: "brainstorming", productVersion: 0 });
  assert.equal(noVersion.enabled, false);
  assert.ok(noVersion.reason.includes("产品状态尚未加载"));
});
