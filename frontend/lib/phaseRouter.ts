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
