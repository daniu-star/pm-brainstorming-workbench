"""状态机：会话阶段、Run、Step、Audit 的合法迁移（PRD §5.3 / §6）。

所有写路径必须先经过这里，禁止在路由或 workflow 里手写 if 判断。
"""
from __future__ import annotations

from .contracts import (
    Action,
    AuditStatus,
    ControlStatus,
    EndReason,
    EventType,
    RunStatus,
    SessionPhase,
    StepStatus,
    TERMINAL_RUN_STATUSES,
)
from .errors import AppError, ErrorCode


# --------------------------------------------------------------------------- #
# 会话动作 → 允许的 phase
# --------------------------------------------------------------------------- #

#: 普通命令在每个 phase 下是否允许。审计中的 end/cancel 属于控制意图，见 CONTROL_ALLOWED。
ACTION_PHASES: dict[Action, frozenset[SessionPhase]] = {
    Action.clarify: frozenset({SessionPhase.clarifying}),
    Action.confirm_brief: frozenset({SessionPhase.clarifying}),
    Action.skip_clarification: frozenset({SessionPhase.clarifying}),
    Action.revise_brief: frozenset(
        {SessionPhase.clarifying, SessionPhase.brainstorming, SessionPhase.audit_completed}
    ),
    Action.add_evidence: frozenset(
        {
            SessionPhase.clarifying,
            SessionPhase.brainstorming,
            SessionPhase.auditing,
            SessionPhase.audit_completed,
        }
    ),
    Action.brainstorm: frozenset({SessionPhase.brainstorming, SessionPhase.audit_completed}),
    Action.confirm_decision: frozenset({SessionPhase.brainstorming, SessionPhase.audit_completed}),
    Action.start_audit: frozenset({SessionPhase.brainstorming, SessionPhase.audit_completed}),
    Action.answer_audit: frozenset({SessionPhase.auditing}),
    Action.correct_audit_assessment: frozenset({SessionPhase.auditing, SessionPhase.audit_completed}),
    Action.rebuild_legacy: frozenset({SessionPhase.brainstorming, SessionPhase.audit_completed}),
    Action.end_audit: frozenset({SessionPhase.auditing}),
    Action.cancel_audit: frozenset({SessionPhase.auditing}),
}

#: 控制意图不受 SESSION_BUSY 约束（PRD FR-07）
CONTROL_ALLOWED = frozenset({Action.end_audit, Action.cancel_audit})

#: 允许改变的阶段（用于 session_version 校验、活动 Run 判定之外的语义变化）
ACTION_ADVANCES_PHASE = frozenset(
    {Action.confirm_brief, Action.skip_clarification, Action.start_audit, Action.end_audit, Action.cancel_audit}
)


def phase_error_code(action: Action, phase: SessionPhase) -> str:
    """按 PRD 给出更精确的错误码，而不是笼统的 INVALID_REQUEST。"""
    if action is Action.revise_brief and phase is SessionPhase.auditing:
        return ErrorCode.AUDIT_ACTIVE
    if action is Action.answer_audit and phase is SessionPhase.audit_completed:
        return ErrorCode.AUDIT_CLOSED
    if action in (Action.end_audit, Action.cancel_audit) and phase is SessionPhase.audit_completed:
        return ErrorCode.AUDIT_CLOSED
    if action is Action.start_audit and phase is SessionPhase.auditing:
        return ErrorCode.AUDIT_ACTIVE
    return ErrorCode.INVALID_REQUEST


def validate_action_for_phase(action: Action, phase: SessionPhase) -> None:
    """校验动作是否允许在当前阶段执行，不允许则抛 AppError。"""
    allowed = ACTION_PHASES.get(action)
    if allowed is None:
        raise AppError(ErrorCode.UNKNOWN_ACTION, f"未知动作：{action}")
    if phase in allowed:
        return
    code = phase_error_code(action, phase)
    raise AppError(
        code,
        f"当前会话阶段 {phase.value} 不允许执行 {action.value}",
        details={"phase": phase.value, "action": str(action), "allowed_phases": sorted(p.value for p in allowed)},
    )


# --------------------------------------------------------------------------- #
# Run 状态机
# --------------------------------------------------------------------------- #

RUN_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.queued: frozenset(
        {RunStatus.running, RunStatus.cancelled, RunStatus.failed, RunStatus.interrupted}
    ),
    RunStatus.running: frozenset(
        {
            RunStatus.succeeded,
            RunStatus.partial_succeeded,
            RunStatus.failed,
            RunStatus.cancel_requested,
            RunStatus.interrupted,
        }
    ),
    RunStatus.interrupted: frozenset(
        {RunStatus.queued, RunStatus.running, RunStatus.cancel_requested, RunStatus.cancelled, RunStatus.failed}
    ),
    RunStatus.cancel_requested: frozenset({RunStatus.cancelled, RunStatus.failed}),
    RunStatus.succeeded: frozenset(),
    RunStatus.partial_succeeded: frozenset(),
    RunStatus.failed: frozenset(),
    RunStatus.cancelled: frozenset(),
}


def can_transition_run(current: RunStatus, target: RunStatus) -> bool:
    return target in RUN_TRANSITIONS.get(current, frozenset())


def validate_run_transition(current: RunStatus, target: RunStatus) -> None:
    if current == target:
        return
    if not can_transition_run(current, target):
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            f"非法 Run 状态迁移：{current.value} → {target.value}",
            details={"current": current.value, "target": target.value},
        )


def is_terminal_run(status: RunStatus) -> bool:
    return status in TERMINAL_RUN_STATUSES


def terminal_event_for(status: RunStatus) -> EventType | None:
    """每个 Run 恰好一个终结事件。"""
    if status in (RunStatus.succeeded, RunStatus.partial_succeeded):
        return EventType.run_completed
    if status is RunStatus.failed:
        return EventType.run_failed
    if status is RunStatus.cancelled:
        return EventType.run_cancelled
    return None


# --------------------------------------------------------------------------- #
# Step 状态机
# --------------------------------------------------------------------------- #

STEP_TRANSITIONS: dict[StepStatus, frozenset[StepStatus]] = {
    StepStatus.pending: frozenset({StepStatus.running, StepStatus.skipped, StepStatus.cancelled}),
    StepStatus.running: frozenset(
        {StepStatus.succeeded, StepStatus.failed, StepStatus.interrupted, StepStatus.cancelled}
    ),
    StepStatus.interrupted: frozenset(
        {StepStatus.running, StepStatus.succeeded, StepStatus.failed, StepStatus.cancelled, StepStatus.skipped}
    ),
    StepStatus.succeeded: frozenset(),
    StepStatus.failed: frozenset({StepStatus.running}),  # retry_failed 子 Run
    StepStatus.cancelled: frozenset({StepStatus.running}),  # retry_failed 子 Run
    StepStatus.skipped: frozenset(),
}


def can_transition_step(current: StepStatus, target: StepStatus) -> bool:
    return target in STEP_TRANSITIONS.get(current, frozenset())


def validate_step_transition(current: StepStatus, target: StepStatus) -> None:
    """非法步骤迁移抛 AppError(INVALID_TRANSITION)。

    步骤重试（failed/cancelled → running）是合法的：``retry_failed`` 会在子 Run 里重置步骤。
    """

    if not can_transition_step(current, target):
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            f"非法的步骤状态迁移：{current.value} → {target.value}",
            details={"current": current.value, "target": target.value},
        )


# --------------------------------------------------------------------------- #
# Audit 状态机（FR-07 异常表）
# --------------------------------------------------------------------------- #

AUDIT_TRANSITIONS: dict[AuditStatus, frozenset[AuditStatus]] = {
    AuditStatus.created: frozenset({AuditStatus.opening, AuditStatus.failed, AuditStatus.cancelled}),
    # 开场失败后可重试原开场（版本相同）
    AuditStatus.opening: frozenset({AuditStatus.active, AuditStatus.failed, AuditStatus.cancelled}),
    AuditStatus.active: frozenset({AuditStatus.closing, AuditStatus.cancelled, AuditStatus.failed}),
    # closing 失败后可只重试报告步骤；未完成的结束意图可被取消优先（PRD FR-07 / AC-42）
    AuditStatus.closing: frozenset({AuditStatus.completed, AuditStatus.failed, AuditStatus.cancelled}),
    AuditStatus.failed: frozenset({AuditStatus.opening, AuditStatus.closing, AuditStatus.cancelled}),
    AuditStatus.completed: frozenset(),
    AuditStatus.cancelled: frozenset(),
}


def can_transition_audit(current: AuditStatus, target: AuditStatus) -> bool:
    return target in AUDIT_TRANSITIONS.get(current, frozenset())


def validate_audit_transition(current: AuditStatus, target: AuditStatus) -> None:
    if current == target:
        return
    if not can_transition_audit(current, target):
        raise AppError(
            ErrorCode.AUDIT_CLOSED if current in (AuditStatus.completed, AuditStatus.cancelled) else ErrorCode.INVALID_REQUEST,
            f"非法审计状态迁移：{current.value} → {target.value}",
            details={"current": current.value, "target": target.value},
        )


AUDIT_OPEN_STATUSES = frozenset({AuditStatus.created, AuditStatus.opening, AuditStatus.active, AuditStatus.closing})
AUDIT_ACCEPTS_ANSWERS = frozenset({AuditStatus.active})


def audit_accepts_answers(status: AuditStatus) -> bool:
    return status in AUDIT_ACCEPTS_ANSWERS


def audit_is_open(status: AuditStatus) -> bool:
    return status in AUDIT_OPEN_STATUSES


# --------------------------------------------------------------------------- #
# 控制意图（end_audit / cancel_audit）
# --------------------------------------------------------------------------- #

CONTROL_TRANSITIONS: dict[ControlStatus, frozenset[ControlStatus]] = {
    ControlStatus.accepted: frozenset({ControlStatus.scheduled, ControlStatus.applied, ControlStatus.failed}),
    ControlStatus.scheduled: frozenset({ControlStatus.applied, ControlStatus.failed}),
    ControlStatus.applied: frozenset(),
    ControlStatus.failed: frozenset(),
}


def can_transition_control(current: ControlStatus, target: ControlStatus) -> bool:
    return target in CONTROL_TRANSITIONS.get(current, frozenset())


#: 取消优先于未完成的结束意图（PRD FR-07 控制竞争规则）
def cancel_supersedes_end(end_status: ControlStatus) -> bool:
    return end_status in (ControlStatus.accepted, ControlStatus.scheduled)


# --------------------------------------------------------------------------- #
# 阶段推进
# --------------------------------------------------------------------------- #


def phase_after_confirm() -> SessionPhase:
    return SessionPhase.brainstorming


def phase_after_skip() -> SessionPhase:
    return SessionPhase.brainstorming


def phase_after_audit_start() -> SessionPhase:
    return SessionPhase.auditing


def phase_after_audit_end(reason: EndReason) -> SessionPhase:
    if reason in (EndReason.cancelled, EndReason.failed, EndReason.legacy_unstructured):
        return SessionPhase.brainstorming
    return SessionPhase.audit_completed


def phase_after_brainstorm(current: SessionPhase) -> SessionPhase:
    """audit_completed 后开始新讨论：回到 brainstorming。"""
    if current is SessionPhase.audit_completed:
        return SessionPhase.brainstorming
    return current


__all__ = [
    "ACTION_PHASES",
    "AUDIT_TRANSITIONS",
    "CONTROL_ALLOWED",
    "CONTROL_TRANSITIONS",
    "RUN_TRANSITIONS",
    "STEP_TRANSITIONS",
    "audit_accepts_answers",
    "audit_is_open",
    "can_transition_audit",
    "can_transition_control",
    "can_transition_run",
    "can_transition_step",
    "cancel_supersedes_end",
    "is_terminal_run",
    "phase_after_audit_end",
    "phase_after_audit_start",
    "phase_after_brainstorm",
    "phase_after_confirm",
    "phase_after_skip",
    "terminal_event_for",
    "validate_action_for_phase",
    "validate_audit_transition",
    "validate_run_transition",
]