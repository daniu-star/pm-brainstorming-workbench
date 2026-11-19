/**
 * v2 阶段路由（M3 修复）
 *
 * 旧 UI 只有 define/coach/brainstorm/interview 四个 phase，`mapV2Phase` 会把
 * auditing 与 audit_completed 都映射成 interview，导致审计结束后发送仍走
 * answer_audit（后端 answer_audit 只允许 auditing，见
 * backend/domain/state_machine.py:44）。这里以 v2 阶段原值做纯函数判断，
 * 便于 node --test 直接覆盖：
 * - 仅 auditing 才回答审计问题；
 * - clarifying 发 clarify，brainstorming / auditing / audit_completed 发 brainstorm
 *   （backend/domain/state_machine.py:41 允许 brainstorm ∈ {brainstorming, audit_completed}）；
 * - brainstorming / audit_completed 可以发起（重新）审计
 *   （backend/domain/state_machine.py:43）。
 */

import type { Action, SessionPhase } from "./contracts.generated";

/** 是否处于「回答审计问题」阶段（只有 auditing 允许 answer_audit） */
export function isAuditAnswerPhase(phase: SessionPhase | null | undefined): boolean {
  return phase === "auditing";
}

/** 文本消息对应的命令：clarifying 澄清，其余走 brainstorm */
export function messageActionForPhase(phase: SessionPhase | null | undefined): Action {
  return phase === "clarifying" ? "clarify" : "brainstorm";
}

/** 是否允许发起（重新）审计：audit_completed 下也可以重新审计 */
export function canStartAuditPhase(phase: SessionPhase | null | undefined): boolean {
  return phase === "brainstorming" || phase === "audit_completed";
}

// --------------------------------------------------------------------------- //
// 画布决策确认（PROD-01 接线 + PROD-03 阶段门控）
// --------------------------------------------------------------------------- //

/**
 * confirm_decision 允许的阶段（backend/domain/state_machine.py:42
 * ACTION_PHASES[Action.confirm_decision] = {brainstorming, audit_completed}）。
 */
export function canConfirmDecisionPhase(phase: SessionPhase | null | undefined): boolean {
  return phase === "brainstorming" || phase === "audit_completed";
}

export interface DecisionActionGateInput {
  /** 决策节点/决策项的 status；只有 proposed 才需要确认 */
  status?: string | null;
  /** 会话 v2 阶段 */
  phase?: SessionPhase | null;
  /** 当前产品状态版本（画布投影依据） */
  productVersion?: number | null;
}

export interface DecisionActionGate {
  /** 是否渲染「确认决策」入口 */
  visible: boolean;
  /** 是否可点击 */
  enabled: boolean;
  /** 不可点击时展示的原因（enabled 为 true 时为 null） */
  reason: string | null;
}

/**
 * 画布决策节点的确认入口门控。
 *
 * 审计进行中（auditing）时后端会以 INVALID_REQUEST 拒绝 confirm_decision，
 * 因此这里提前禁用并给出原因，避免「可点但必失败」。
 */
export function decisionActionGate(input: DecisionActionGateInput): DecisionActionGate {
  if (input.status !== "proposed") {
    return { visible: false, enabled: false, reason: null };
  }
  if (!input.phase) {
    return { visible: true, enabled: false, reason: "会话阶段尚未加载，请稍后重试" };
  }
  if (!canConfirmDecisionPhase(input.phase)) {
    return {
      visible: true,
      enabled: false,
      reason: "当前阶段不能确认决策，请先进入脑暴或结束审计",
    };
  }
  if (!input.productVersion || input.productVersion < 1) {
    return { visible: true, enabled: false, reason: "产品状态尚未加载，请稍后重试" };
  }
  return { visible: true, enabled: true, reason: null };
}

// --------------------------------------------------------------------------- //
// 「开始讨论」入口（M3 修复）
// --------------------------------------------------------------------------- //

/** 判断脑暴产物所需的最小消息形状（MessageView 的子集） */
export interface BrainstormEvidenceMessage {
  sender_kind?: string | null;
  stage?: string | null;
}

/**
 * 是否已有脑暴产物：只看持久化证据（agent 的 brainstorm 消息）。
 * 内存里的 runs 会在刷新后清空，单靠它会导致 F5 后「开始讨论」横幅重新出现，
 * 再点一次就会提交第二个 brainstorm Run。
 */
export function hasBrainstormMessages(
  messages: readonly BrainstormEvidenceMessage[]
): boolean {
  return messages.some(
    (view) => view.sender_kind === "agent" && view.stage === "brainstorm"
  );
}

export interface StartDiscussionInput {
  v2Phase?: SessionPhase | null;
  phase?: string | null;
  /** 内存/runs 证据：回执到达后立刻隐藏入口 */
  hasBrainstormRun: boolean;
  /** 持久化证据：刷新后仍然有效 */
  messages: readonly BrainstormEvidenceMessage[];
}

/** 是否展示「开始讨论」横幅：处于脑暴阶段且尚无任何脑暴产物 */
export function shouldShowStartDiscussion(input: StartDiscussionInput): boolean {
  const brainstorming = input.v2Phase
    ? input.v2Phase === "brainstorming"
    : input.phase === "brainstorm";
  if (!brainstorming) return false;
  if (input.hasBrainstormRun) return false;
  return !hasBrainstormMessages(input.messages);
}
