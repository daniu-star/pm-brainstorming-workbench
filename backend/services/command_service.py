"""命令服务：V4 唯一写入入口（PRD FR-01/FR-03/FR-07、§5.3、§7.2）。

职责边界：
* 校验权限、phase、``expected_session_version``、``request_id`` 与输入哈希后，
  在**同一事务**内完成「用户消息 + 序号 + Run 占位 + 会话版本」；
* 维护 IdempotencyRecord（owner + request_id 唯一，同键不同输入 → 409）；
* 同步命令（不调用模型的纯状态变更）在同一事务内直接完成并返回 ``succeeded`` 回执；
* ``apply_outcome`` 是 Worker 唯一的提交入口：结果 + 产品状态 + 消息 + 事件 +
  Run 终结 + 会话版本必须一次提交（PRD §5.3「同事务」不变量）；
* 控制意图（end_audit / cancel_audit）先落库再执行，取消优先于未完成的结束。

本模块是**纯同步**的（不 await、不读网络）；模型调用只发生在 workflows/runner。
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from domain import contracts as C
from domain import state_machine as SM
from domain.contracts import (
    Action,
    Assessment,
    AuditStatus,
    BriefField,
    BriefFieldStatus,
    BriefStatus,
    CommandRequest,
    ControlReceipt,
    ControlStatus,
    DecisionStatus,
    EndReason,
    EventType,
    MessageStage,
    MessageStatus,
    ProductBrief,
    ProductState,
    RoleCode,
    RunEvent,
    RunReceipt,
    RunStatus,
    RunView,
    SenderKind,
    SessionPhase,
    SessionView,
    StartAuditRecovery,
    canonical_json,
    input_hash,
    new_id,
    parse_command,
    utc_now_iso,
)
from domain.errors import AppError, ErrorCode
from repositories import audit_repo, run_repo, session_repo
from repositories.database import (
    AuditRow,
    ControlIntentRow,
    IdempotencyRow,
    RunRow,
    SessionRow,
)

logger = logging.getLogger(__name__)

#: 不调用模型、可在命令事务内直接完成的动作（PRD FR-01 / 团队约定）。
SYNC_ACTIONS: frozenset[Action] = frozenset(
    {
        Action.confirm_brief,
        Action.skip_clarification,
        Action.revise_brief,
        Action.add_evidence,
        Action.confirm_decision,
        Action.correct_audit_assessment,
    }
)

#: 需要 Worker 执行的异步动作。
ASYNC_ACTIONS: frozenset[Action] = frozenset(
    {
        Action.clarify,
        Action.brainstorm,
        Action.start_audit,
        Action.answer_audit,
        Action.rebuild_legacy,
    }
)

#: run action → TaskKind 映射（Worker 的 dispatch 依据）。
ACTION_TASK_KIND: dict[Action, C.TaskKind] = {
    Action.clarify: C.TaskKind.clarify,
    Action.brainstorm: C.TaskKind.brainstorm,
    Action.start_audit: C.TaskKind.audit_open,
    Action.answer_audit: C.TaskKind.audit_answer,
    Action.rebuild_legacy: C.TaskKind.rebuild_legacy,
    Action.end_audit: C.TaskKind.audit_report,
    Action.cancel_audit: C.TaskKind.audit_report,
}


# --------------------------------------------------------------------------- #
# runner 与本模块之间的结果契约（团队接口见 docs/v4-m1-interfaces.md §4.1）
# --------------------------------------------------------------------------- #


@dataclass
class OutgoingMessage:
    """workflow 产出的、待提交的会话消息。"""

    content: str
    sender_kind: SenderKind = SenderKind.agent
    role_code: RoleCode | None = None
    stage: MessageStage = MessageStage.clarify
    status: MessageStatus = MessageStatus.completed
    audit_id: str | None = None
    message_id: str | None = None


@dataclass
class RunOutcome:
    """一次 Run 的最终结果；由 ``apply_outcome`` 原子提交。"""

    status: RunStatus
    product_state: ProductState | None = None
    messages: list[OutgoingMessage] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    result_product_version: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    warnings: list[str] = field(default_factory=list)
    usage: dict[str, Any] | None = None
    session_phase: SessionPhase | None = None
    brief_status: BriefStatus | None = None
    session_version_bump: bool = True
    #: 额外写入钩子（审计问题/评估等），在同一个事务内执行。
    apply: Callable[[Any], None] | None = None


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #


def default_budget(*, output_tokens: int | None = None) -> dict[str, Any]:
    """FR-08 默认预算；可由 Run.budget_json 覆盖。"""

    return {
        "max_run_input_tokens": C.MAX_RUN_INPUT_TOKENS,
        "max_run_output_tokens": C.MAX_RUN_OUTPUT_TOKENS,
        "max_upstream_calls": C.MAX_UPSTREAM_CALLS_PER_RUN,
        "max_context_input_tokens": C.MAX_CONTEXT_INPUT_TOKENS,
        "max_output_tokens": output_tokens or C.MAX_OUTPUT_TOKENS,
        "max_step_output_tokens": output_tokens or C.MAX_OUTPUT_TOKENS,
    }


def _events_url(run_id: str) -> str:
    return f"/api/v2/runs/{run_id}/events"


def _session_view(session: SessionRow, message_count: int = 0) -> SessionView:
    return SessionView(
        id=session.id,
        owner_email=session.owner_email,
        phase=SessionPhase(session.phase),
        session_version=session.session_version,
        current_product_version=session.current_product_version,
        current_audit_id=session.current_audit_id,
        active_run_id=session.active_run_id,
        problem_statement=session.problem_statement or "",
        brief_status=BriefStatus(session.brief_status),
        created_at=session.created_at,
        updated_at=session.updated_at,
        deleted_at=session.deleted_at,
        message_count=message_count,
    )


def _count_messages(db, session_id: str) -> int:
    from repositories.database import MessageRow

    return int(
        db.execute(
            select(func.count())
            .select_from(MessageRow)
            .where(MessageRow.session_id == session_id)
        ).scalar_one()
    )


def session_to_view(db, session: SessionRow) -> SessionView:
    """查询层复用的会话视图（message_count 为消息总数）。"""

    return _session_view(session, _count_messages(db, session.id))


def run_to_view(run: RunRow) -> RunView:
    return RunView(
        id=run.id,
        session_id=run.session_id,
        action=Action(run.action),
        status=RunStatus(run.status),
        request_id=run.request_id,
        base_session_version=run.base_session_version or 0,
        base_product_version=run.base_product_version or 1,
        result_product_version=run.result_product_version,
        lease_owner=run.lease_owner,
        lease_epoch=run.lease_epoch or 0,
        lease_expires_at=run.lease_expires_at,
        parent_run_id=run.parent_run_id,
        audit_id=run.audit_id,
        error_code=run.error_code,
        error_message=run.error_message,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        deadline_at=run.deadline_at,
    )


def _receipt(run: RunRow, session: SessionRow, *, reused: bool = False) -> RunReceipt:
    return RunReceipt(
        run_id=run.id,
        session_id=run.session_id,
        action=Action(run.action),
        status=RunStatus(run.status),
        session_version=session.session_version,
        base_product_version=run.base_product_version or 1,
        events_url=_events_url(run.id),
        reused=reused,
        audit_id=run.audit_id,
    )


def _control_receipt(
    control: ControlIntentRow, session: SessionRow, *, reused: bool = False
) -> ControlReceipt:
    return ControlReceipt(
        control_id=control.id,
        audit_id=control.audit_id,
        status=ControlStatus(control.status),
        session_version=session.session_version,
        related_run_id=control.related_run_id,
        reused=reused,
        action=Action(control.action),
    )


def _require_session(db, session_id: str, owner_email: str) -> SessionRow:
    return session_repo.require_session(db, session_id, owner_email=owner_email)


def _rate_limit_ok(db, owner_email: str, *, now_epoch: int) -> bool:
    """每用户每分钟命令数（FR-08）。auth_repo 未就绪时放行并记 warning。"""

    try:
        from repositories import auth_repo as _auth_repo
    except Exception:  # pragma: no cover - 并行开发期的兜底
        logger.warning("auth_repo 不可用，跳过命令限流")
        return True
    return _auth_repo.hit_rate_limit(
        db,
        scope="commands",
        key=owner_email,
        limit=C.COMMANDS_PER_MINUTE,
        window_seconds=60,
        now_epoch=now_epoch,
    )


def _ensure_expected_version(session: SessionRow, expected: int | None) -> None:
    if expected is None:
        return
    if expected != session.session_version:
        raise AppError(
            ErrorCode.VERSION_CONFLICT,
            "会话版本已变化，请刷新后重试",
            details={
                "expected_session_version": expected,
                "current_session_version": session.session_version,
            },
        )


def _ensure_no_active_run(db, session: SessionRow) -> None:
    active = run_repo.active_run_for_session(db, session.id)
    if active is not None:
        raise AppError(
            ErrorCode.SESSION_BUSY,
            "当前会话有正在执行的任务，请稍后再试",
            details={"active_run_id": active.id},
        )


def _ensure_queue_budget(db, owner_email: str) -> None:
    queued = run_repo.count_queued_runs(db, owner_email=owner_email)
    if queued >= C.MAX_QUEUED_RUNS_PER_USER:
        raise AppError(
            ErrorCode.QUEUE_LIMIT_EXCEEDED,
            "排队任务过多，请等待现有任务完成",
            details={"queued": queued, "limit": C.MAX_QUEUED_RUNS_PER_USER},
        )


# --------------------------------------------------------------------------- #
# 幂等
# --------------------------------------------------------------------------- #


def _receipt_from_run(db, record: IdempotencyRow, parsed: C.ParsedCommand) -> RunReceipt | None:
    """兜底：幂等体解析失败时，直接从 Run 行重建回执（AC-06 只要求同一 Run）。"""

    if not record.related_run_id:
        return None
    run = run_repo.get_run(db, record.related_run_id)
    if run is None:
        return None
    session = session_repo.get_session(db, run.session_id, include_deleted=True)
    if session is None:  # pragma: no cover - 会话被硬删
        return None
    return _receipt(run, session, reused=True)


def _replay_record(
    db, record: IdempotencyRow, parsed: C.ParsedCommand
) -> RunReceipt | ControlReceipt | StartAuditRecovery | None:
    """把已提交的幂等记录还原成回执（``reused=True``）。"""

    if record.action != parsed.action.value or record.input_hash != parsed.input_hash:
        raise AppError(
            ErrorCode.IDEMPOTENCY_KEY_REUSED,
            "同一 request_id 已用于不同请求",
            details={
                "request_id": parsed.request_id,
                "original_action": record.action,
                "requested_action": parsed.action.value,
            },
        )
    try:
        stored = json.loads(record.response_json or "{}")
    except ValueError:  # pragma: no cover - 数据损坏
        stored = {}
    kind = stored.get("kind")
    body = stored.get("body") or {}
    try:
        if kind == "control":
            receipt = ControlReceipt.model_validate(body)
            return receipt.model_copy(update={"reused": True})
        if kind == "recovery":
            recovery = StartAuditRecovery.model_validate(body)
            return recovery.model_copy(update={"reused": True})
        receipt = RunReceipt.model_validate(body)
        return receipt.model_copy(update={"reused": True})
    except Exception:  # pragma: no cover - 历史版本兼容
        logger.warning(
            "幂等记录无法还原（request_id=%s），尝试用 Run 行重建", parsed.request_id, exc_info=True
        )
        return _receipt_from_run(db, record, parsed)


def _lookup_idempotent(db, owner_email: str, parsed: C.ParsedCommand) -> RunReceipt | ControlReceipt | StartAuditRecovery | None:
    record = run_repo.get_idempotency(db, owner_email, parsed.request_id)
    if record is None:
        return None
    return _replay_record(db, record, parsed)


def _claim_idempotency_with_retry(
    db, owner_email: str, parsed: C.ParsedCommand, now_iso: str, *, attempts: int = 3
) -> bool:
    """占位幂等键；遇到 SQLite 写锁竞争时短暂重试（不吞其它错误）。"""

    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            return run_repo.claim_idempotency(
                db,
                owner_email=owner_email,
                request_id=parsed.request_id,
                action=parsed.action.value,
                resource_scope=parsed.resource_scope,
                input_hash=parsed.input_hash,
                now_iso=now_iso,
            )
        except OperationalError as exc:  # pragma: no cover - 依赖真实并发时序
            last_exc = exc
            text = str(exc).lower()
            if "locked" not in text and "busy" not in text:
                raise
            db.rollback()
            time.sleep(0.03 * (attempt + 1))
    raise AppError(
        ErrorCode.INTERNAL,
        "数据库繁忙，请稍后重试",
        http_status=503,
        retryable=True,
    ) from last_exc


def _await_idempotent(
    db, owner_email: str, parsed: C.ParsedCommand, *, timeout_seconds: float = 5.0
) -> RunReceipt | ControlReceipt | StartAuditRecovery | None:
    """等待另一进程把同一 request_id 的回执提交出来（AC-06 并发同键）。"""

    deadline = time.monotonic() + timeout_seconds
    while True:
        db.rollback()  # 每轮都换新快照，才能看到对手新提交的行
        record = run_repo.get_idempotency(db, owner_email, parsed.request_id)
        if record is not None and record.response_json:
            replayed = _replay_record(db, record, parsed)
            if replayed is not None:
                return replayed
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.02)


def _store_idempotent(
    db,
    owner_email: str,
    parsed: C.ParsedCommand,
    *,
    kind: str,
    body: dict[str, Any],
    http_status: int,
    related_run_id: str | None,
    related_control_id: str | None,
    now_iso: str,
) -> None:
    run_repo.complete_idempotency(
        db,
        owner_email=owner_email,
        request_id=parsed.request_id,
        response_status=http_status,
        response={"kind": kind, "body": body},
        related_run_id=related_run_id,
        related_control_id=related_control_id,
    )


# --------------------------------------------------------------------------- #
# 命令入口
# --------------------------------------------------------------------------- #


def submit_command(
    db,
    *,
    owner_email: str,
    session_id: str,
    command: CommandRequest,
    now_iso: str | None = None,
    now_epoch: int | None = None,
    _attempt: int = 0,
) -> RunReceipt | ControlReceipt | StartAuditRecovery:
    """接收一条命令（PRD §7.2 POST /commands）。

    成功时返回回执；所有校验失败均抛 ``AppError``（API 层统一转错误体）。
    """

    now_iso = now_iso or utc_now_iso()
    now_epoch = int(now_epoch if now_epoch is not None else time.time())

    resource_scope = f"session:{session_id}"
    try:
        parsed = parse_command(
            command.action,
            command.request_id,
            command.payload,
            command.expected_session_version,
            resource_scope,
        )
    except AppError:
        raise
    except ValueError as exc:  # pydantic 校验失败
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            f"命令参数不合法：{exc}",
            details={"action": command.action.value},
        ) from exc

    # ① 原子占位幂等键：必须先于任何 Run 建行，否则同键并发会各建一个 Run（AC-06）。
    if not _claim_idempotency_with_retry(db, owner_email, parsed, now_iso):
        db.rollback()  # 丢弃本连接快照，才能读到对手已提交的回执
        replayed = _await_idempotent(db, owner_email, parsed)
        if replayed is not None:
            return replayed
        if _attempt < 2:  # 对手回滚/崩溃 → 键已释放，重新竞争一次
            return submit_command(
                db,
                owner_email=owner_email,
                session_id=session_id,
                command=command,
                now_iso=now_iso,
                now_epoch=now_epoch,
                _attempt=_attempt + 1,
            )
        raise AppError(
            ErrorCode.IDEMPOTENCY_KEY_REUSED,
            "同一 request_id 正在处理中，请稍后重试",
            http_status=409,
            retryable=True,
        )

    session = _require_session(db, session_id, owner_email)
    if session.deleted_at:
        raise AppError(ErrorCode.NOT_FOUND, "会话不存在")
    if session.owner_email != owner_email:
        raise AppError(ErrorCode.FORBIDDEN, "无权访问该会话")

    if str(command.action) not in {a.value for a in Action}:
        raise AppError(ErrorCode.UNKNOWN_ACTION, f"未知命令：{command.action}")

    _ensure_expected_version(session, command.expected_session_version)

    if not _rate_limit_ok(db, owner_email, now_epoch=now_epoch):
        raise AppError(ErrorCode.RATE_LIMITED, "操作过于频繁，请稍后再试", http_status=429)

    if command.action in C.CONTROL_ACTIONS:
        control, audit = _submit_control(
            db,
            session=session,
            parsed=parsed,
            owner_email=owner_email,
            now_iso=now_iso,
            now_epoch=now_epoch,
        )
        receipt = _control_receipt(control, session)
        _store_idempotent(
            db,
            owner_email,
            parsed,
            kind="control",
            body=receipt.model_dump(mode="json"),
            http_status=202,
            related_run_id=control.related_run_id,
            related_control_id=control.id,
            now_iso=now_iso,
        )
        return receipt

    SM.validate_action_for_phase(command.action, SessionPhase(session.phase))

    if command.action is Action.start_audit:
        recovery = _maybe_audit_recovery(db, session)
        if recovery is not None:
            _store_idempotent(
                db,
                owner_email,
                parsed,
                kind="recovery",
                body=recovery.model_dump(mode="json"),
                http_status=200,
                related_run_id=recovery.current_run_id,
                related_control_id=None,
                now_iso=now_iso,
            )
            return recovery

    _ensure_no_active_run(db, session)
    _ensure_queue_budget(db, owner_email)

    if command.action in SYNC_ACTIONS:
        receipt = _submit_sync(
            db,
            session=session,
            parsed=parsed,
            owner_email=owner_email,
            now_iso=now_iso,
            now_epoch=now_epoch,
        )
    else:
        receipt = _submit_async(
            db,
            session=session,
            parsed=parsed,
            owner_email=owner_email,
            now_iso=now_iso,
            now_epoch=now_epoch,
        )

    _store_idempotent(
        db,
        owner_email,
        parsed,
        kind="run",
        body=receipt.model_dump(mode="json"),
        http_status=202,
        related_run_id=receipt.run_id,
        related_control_id=None,
        now_iso=now_iso,
    )
    return receipt


# --------------------------------------------------------------------------- #
# 异步命令：建 Run 占位
# --------------------------------------------------------------------------- #


def _submit_async(
    db,
    *,
    session: SessionRow,
    parsed: C.ParsedCommand,
    owner_email: str,
    now_iso: str,
    now_epoch: int,
) -> RunReceipt:
    action = parsed.action
    payload: dict[str, Any] = parsed.payload
    stage = MessageStage.brainstorm if action in (Action.brainstorm, Action.rebuild_legacy) else MessageStage.clarify
    audit_id: str | None = None
    question_id: str | None = None
    base_audit_version: int | None = None
    base_product_version = session.current_product_version

    if action is Action.answer_audit:
        stage = MessageStage.audit
        audit_id = payload["audit_id"]
        question_id = payload["question_id"]
        audit = audit_repo.require_audit(db, audit_id, owner_email=owner_email)
        if audit.session_id != session.id:
            raise AppError(ErrorCode.FORBIDDEN, "审计不属于该会话")
        if AuditStatus(audit.status) in (AuditStatus.completed, AuditStatus.cancelled):
            raise AppError(ErrorCode.AUDIT_CLOSED, "审计已结束，不能再回答")
        if not SM.audit_accepts_answers(AuditStatus(audit.status)):
            raise AppError(ErrorCode.INVALID_REQUEST, "审计当前不接受回答")
        question = audit_repo.get_question(db, audit.id, question_id)
        if question is None:
            raise AppError(ErrorCode.NOT_FOUND, "审计问题不存在")
        if question.answer_message_id:
            raise AppError(ErrorCode.QUESTION_ALREADY_ANSWERED, "该问题已回答")
        if question.assessment not in (Assessment.awaiting_answer, Assessment.needs_clarification):
            raise AppError(ErrorCode.QUESTION_NOT_ACTIVE, "该问题当前不可回答")
        base_audit_version = audit.audit_version

    elif action is Action.start_audit:
        expected = payload.get("expected_product_version")
        if expected is not None and expected != base_product_version:
            raise AppError(
                ErrorCode.VERSION_CONFLICT,
                "产品状态版本已变化，请刷新后再开始审计",
                details={"expected_product_version": expected, "current": base_product_version},
            )
        audit = audit_repo.create_audit(
            db, session_id=session.id, product_version=base_product_version, now_iso=now_iso
        )
        audit_id = audit.id
        if SM.can_transition_audit(AuditStatus.created, AuditStatus.opening):
            audit_repo.update_audit(db, audit, now_iso=now_iso, status=AuditStatus.opening)
        session_repo.touch_session(
            db,
            session,
            now_iso=now_iso,
            phase=SM.phase_after_audit_start(),
            current_audit_id=audit.id,
        )
        base_product_version = audit.product_version

    elif action is Action.rebuild_legacy:
        expected = payload.get("expected_product_version")
        if expected is not None and expected != base_product_version:
            raise AppError(
                ErrorCode.VERSION_CONFLICT,
                "产品状态版本已变化，请刷新后重试",
                details={"expected_product_version": expected, "current": base_product_version},
            )

    # 1) 用户消息（同一事务内分配序号）
    content = payload.get("content") or payload.get("answer") or ""
    user_message = None
    if content:
        user_message = session_repo.add_message(
            db,
            session,
            sender_kind=SenderKind.human,
            stage=stage,
            content=content,
            status=MessageStatus.completed,
            audit_id=audit_id,
            now_iso=now_iso,
        )
        if question_id and action is Action.answer_audit:
            question = audit_repo.get_question(db, audit_id, question_id)
            if question is not None:
                audit_repo.answer_question(
                    db, question, answer_message_id=user_message.id, now_iso=now_iso
                )

    # 2) Run 占位 + 会话占位（同一事务）
    run_budget = default_budget()
    if action is Action.brainstorm:
        # FR-04B：用户选择的「全员 / 指定角色 / 智能分配」随 Run 落库，
        # workflow 从 run.budget_json["target"] 读取（不依赖进程内状态，重放安全）。
        target = parsed.payload.get("target") or C.TargetRole.auto.value
        run_budget["target"] = getattr(target, "value", target)
    run = run_repo.create_run(
        db,
        session_id=session.id,
        owner_email=owner_email,
        request_id=parsed.request_id,
        action=action.value,
        input_hash=parsed.input_hash,
        base_session_version=session.session_version,
        base_product_version=base_product_version,
        budget=run_budget,
        now_iso=now_iso,
        deadline_at=now_epoch + C.RUN_TIMEOUT_SECONDS,
        queue_deadline_at=now_epoch + C.QUEUE_WAIT_LIMIT_SECONDS,
        audit_id=audit_id,
        base_audit_version=base_audit_version,
        question_id=question_id,
        run_id=new_id(),
    )
    session_repo.touch_session(db, session, now_iso=now_iso, active_run_id=run.id)
    session_repo.bump_session_version(db, session)

    if audit_id and action is Action.start_audit:
        audit = audit_repo.get_audit(db, audit_id)
        if audit is not None:
            audit_repo.update_audit(db, audit, now_iso=now_iso, audit_version=audit.audit_version)

    logger.info(
        "命令已入队 action=%s session=%s run=%s", action.value, session.id, run.id
    )
    return _receipt(run, session)


def _maybe_audit_recovery(db, session: SessionRow) -> StartAuditRecovery | None:
    """已存在未完成审计时，返回当前审计与活动 Run，不新建（PRD FR-07）。"""

    open_audit = audit_repo.open_audit_for_session(db, session.id)
    if open_audit is None:
        return None
    active = run_repo.active_run_for_session(db, session.id)
    latest = db.execute(
        select(RunRow)
        .where(RunRow.session_id == session.id, RunRow.audit_id == open_audit.id)
        .order_by(RunRow.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    return StartAuditRecovery(
        audit_id=open_audit.id,
        status=AuditStatus(open_audit.status),
        current_run_id=active.id if active else None,
        latest_run_id=latest.id if latest is not None else None,
        session_version=session.session_version,
    )


# --------------------------------------------------------------------------- #
# 同步命令：同一事务内完成并提交 succeeded
# --------------------------------------------------------------------------- #


def _submit_sync(
    db,
    *,
    session: SessionRow,
    parsed: C.ParsedCommand,
    owner_email: str,
    now_iso: str,
    now_epoch: int,
) -> RunReceipt:
    state = session_repo.get_product_state(db, session.id)
    action = parsed.action
    events: list[dict[str, Any]] = []
    messages: list[list[Any]] = []  # (content, stage, sender_kind, role_code)
    new_state: ProductState | None = None
    session_phase: SessionPhase | None = None
    brief_status: BriefStatus | None = None
    apply_hook: Callable[[Any], None] | None = None

    if action is Action.confirm_brief:
        new_state, session_phase, brief_status = _apply_confirm_brief(
            db, session, state, parsed, now_iso=now_iso
        )
        messages.append(["产品定义已确认，进入共创阶段。", MessageStage.system, SenderKind.system, None])

    elif action is Action.skip_clarification:
        new_state, session_phase, brief_status = _apply_skip_clarification(db, state, now_iso=now_iso)
        messages.append(
            ["已跳过澄清，进入共创阶段（产品定义待后续补充）。", MessageStage.system, SenderKind.system, None]
        )

    elif action is Action.revise_brief:
        new_state, session_phase, brief_status = _apply_revise_brief(
            db, session, state, parsed, now_iso=now_iso
        )
        messages.append(["产品定义已更新，请确认后继续。", MessageStage.system, SenderKind.system, None])

    elif action is Action.add_evidence:
        evidence = session_repo.add_evidence(
            db,
            session,
            kind=parsed.payload["kind"],
            title=parsed.payload.get("title") or "",
            excerpt=parsed.payload["excerpt"],
            source_uri=parsed.payload.get("source_uri"),
            source_date=parsed.payload.get("source_date"),
            limitations=parsed.payload.get("limitations"),
            created_by=owner_email,
            now_iso=now_iso,
        )
        messages.append(
            [f"已登记证据：{evidence.title or evidence.kind}", MessageStage.system, SenderKind.system, None]
        )

    elif action is Action.confirm_decision:
        new_state = _apply_confirm_decision(
            db, session, state, parsed, owner_email=owner_email, now_iso=now_iso
        )
        messages.append(["决策已确认。", MessageStage.system, SenderKind.system, None])

    elif action is Action.correct_audit_assessment:
        apply_hook = _apply_assessment_correction(
            db, session, parsed, owner_email=owner_email, now_iso=now_iso
        )
        messages.append(["审计评估已人工修正。", MessageStage.audit, SenderKind.system, None])

    else:  # pragma: no cover - SYNC_ACTIONS 与分支必须同步
        raise AppError(ErrorCode.UNKNOWN_ACTION, f"未实现的同步命令：{action.value}")

    run = run_repo.create_run(
        db,
        session_id=session.id,
        owner_email=owner_email,
        request_id=parsed.request_id,
        action=action.value,
        input_hash=parsed.input_hash,
        base_session_version=session.session_version,
        base_product_version=state.version,
        budget=default_budget(),
        now_iso=now_iso,
        deadline_at=now_epoch + C.RUN_TIMEOUT_SECONDS,
        queue_deadline_at=now_epoch + C.QUEUE_WAIT_LIMIT_SECONDS,
        run_id=new_id(),
    )
    session_repo.touch_session(db, session, now_iso=now_iso, active_run_id=run.id)

    outgoing: list[OutgoingMessage] = []
    for content, stage, sender_kind, role_code in messages:
        outgoing.append(
            OutgoingMessage(
                content=str(content),
                sender_kind=sender_kind,
                role_code=role_code,
                stage=stage,
            )
        )

    if new_state is not None:
        events.append(
            {
                "type": EventType.product_state_updated,
                "payload": {
                    "product_version": new_state.version,
                    "session_version": session.session_version + 1,
                },
            }
        )

    outcome = RunOutcome(
        status=RunStatus.succeeded,
        product_state=new_state,
        messages=outgoing,
        events=events,
        session_phase=session_phase,
        brief_status=brief_status,
        apply=apply_hook,
    )
    _commit_outcome(
        db,
        run=run,
        session=session,
        outcome=outcome,
        now_iso=now_iso,
        lease_required=False,
    )
    return _receipt(run, session)


def _apply_confirm_brief(
    db, session: SessionRow, state: ProductState, parsed: C.ParsedCommand, *, now_iso: str
) -> tuple[ProductState, SessionPhase, BriefStatus]:
    expected = parsed.payload["expected_product_version"]
    if expected != state.version:
        raise AppError(
            ErrorCode.VERSION_CONFLICT,
            "产品状态版本已变化，请刷新后重试",
            details={"expected_product_version": expected, "current_product_version": state.version},
        )
    brief: ProductBrief = state.brief.model_copy(deep=True)
    missing = brief.required_missing()
    if missing:
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            "仍有关键字段未确认，无法确认产品定义",
            details={"missing_fields": missing},
        )
    overrides: dict[str, str] = parsed.payload.get("fields") or {}
    for name, value in overrides.items():
        if name not in C.BRIEF_FIELDS:  # pragma: no cover - pydantic 已限制 dict 键
            continue
        field_obj = getattr(brief, name)
        setattr(
            brief,
            name,
            field_obj.model_copy(update={"value": value, "status": BriefFieldStatus.confirmed}),
        )
    for name in C.BRIEF_FIELDS:
        field_obj = getattr(brief, name)
        if field_obj.status != BriefFieldStatus.unknown:
            setattr(brief, name, field_obj.model_copy(update={"status": BriefFieldStatus.confirmed}))
    brief = brief.model_copy(update={"status": BriefStatus.confirmed, "skipped": False})
    new_state = _bump_state(state, brief=brief)
    session_repo.touch_session(db, session, now_iso=now_iso, brief_status=BriefStatus.confirmed)
    return new_state, SM.phase_after_confirm(), BriefStatus.confirmed


def _apply_skip_clarification(
    db, state: ProductState, *, now_iso: str
) -> tuple[ProductState, SessionPhase, BriefStatus]:
    brief = state.brief.model_copy(update={"status": BriefStatus.skipped, "skipped": True})
    new_state = _bump_state(state, brief=brief)
    return new_state, SM.phase_after_skip(), BriefStatus.skipped


def _apply_revise_brief(
    db, session: SessionRow, state: ProductState, parsed: C.ParsedCommand, *, now_iso: str
) -> tuple[ProductState, SessionPhase | None, BriefStatus]:
    expected = parsed.payload["expected_product_version"]
    if expected != state.version:
        raise AppError(
            ErrorCode.VERSION_CONFLICT,
            "产品状态版本已变化，请刷新后重试",
            details={"expected_product_version": expected, "current_product_version": state.version},
        )
    brief: ProductBrief = state.brief.model_copy(deep=True)
    updates: dict[str, Any] = parsed.payload.get("fields") or {}
    protected_touched = False
    for name, raw in updates.items():
        if name not in C.BRIEF_FIELDS:
            continue
        update = raw if isinstance(raw, dict) else {"value": raw, "status": BriefFieldStatus.proposed}
        new_status = update.get("status") or BriefFieldStatus.proposed
        if new_status == BriefFieldStatus.confirmed:
            # 用户显式提交的修正视为已确认；模型无法走到这里。
            new_status = BriefFieldStatus.confirmed
        setattr(
            brief,
            name,
            BriefField(value=update.get("value"), status=new_status, source_message_ids=[]),
        )
        if name in (C.BRIEF_REQUIRED_FIELDS):
            protected_touched = True

    was_confirmed = brief.status == BriefStatus.confirmed
    if was_confirmed and protected_touched:
        brief = brief.model_copy(update={"status": BriefStatus.ready})
    elif was_confirmed:
        brief = brief.model_copy(update={"status": BriefStatus.confirmed})
    status = brief.status
    new_state = _bump_state(state, brief=brief)

    decisions = []
    for decision in state.decisions:
        if protected_touched and decision.status == DecisionStatus.confirmed:
            decisions.append(decision.model_copy(update={"needs_review": True}))
        else:
            decisions.append(decision)
    if decisions:
        new_state = new_state.model_copy(update={"decisions": decisions})

    phase: SessionPhase | None = None
    if SessionPhase(session.phase) is SessionPhase.audit_completed:
        phase = SessionPhase.brainstorming
        session_repo.touch_session(db, session, now_iso=now_iso, phase=phase)
    session_repo.touch_session(db, session, now_iso=now_iso, brief_status=status)
    return new_state, phase, status


def _apply_confirm_decision(
    db,
    session: SessionRow,
    state: ProductState,
    parsed: C.ParsedCommand,
    *,
    owner_email: str,
    now_iso: str,
) -> ProductState:
    expected = parsed.payload["expected_product_version"]
    if expected != state.version:
        raise AppError(
            ErrorCode.VERSION_CONFLICT,
            "产品状态版本已变化，请刷新后重试",
            details={"expected_product_version": expected, "current_product_version": state.version},
        )
    decision_id = parsed.payload["decision_id"]
    decisions = list(state.decisions)
    found = False
    for index, decision in enumerate(decisions):
        if decision.id != decision_id:
            continue
        found = True
        decisions[index] = decision.model_copy(
            update={
                "status": DecisionStatus.confirmed,
                "option_id": parsed.payload.get("option_id") or decision.option_id,
                "rationale": parsed.payload.get("rationale") or decision.rationale,
                "confirmed_by": owner_email,
                "confirmed_at": now_iso,
                "needs_review": False,
            }
        )
    if not found:
        raise AppError(ErrorCode.NOT_FOUND, "决策不存在", details={"decision_id": decision_id})

    options = list(state.options)
    chosen = parsed.payload.get("option_id")
    if chosen:
        for index, option in enumerate(options):
            if option.id == chosen:
                options[index] = option.model_copy(update={"status": C.ClaimStatus.accepted})
    return _bump_state(state, decisions=decisions, options=options)


def _apply_assessment_correction(
    db,
    session: SessionRow,
    parsed: C.ParsedCommand,
    *,
    owner_email: str,
    now_iso: str,
) -> Callable[[Any], None]:
    audit_id = parsed.payload["audit_id"]
    audit = audit_repo.require_audit(db, audit_id, owner_email=owner_email)
    if audit.session_id != session.id:
        raise AppError(ErrorCode.FORBIDDEN, "审计不属于该会话")
    if parsed.payload["expected_audit_version"] != audit.audit_version:
        raise AppError(
            ErrorCode.VERSION_CONFLICT,
            "审计版本已变化，请刷新后重试",
            details={
                "expected_audit_version": parsed.payload["expected_audit_version"],
                "current_audit_version": audit.audit_version,
            },
        )
    question_id = parsed.payload.get("question_id")
    question = audit_repo.get_question(db, audit.id, question_id) if question_id else None
    if question_id and question is None:
        raise AppError(ErrorCode.NOT_FOUND, "审计问题不存在")
    assessment = Assessment(parsed.payload["assessment"])
    before = {
        "assessment": question.assessment if question else None,
        "question_id": question_id,
    }
    after = {
        "assessment": assessment.value,
        "rationale": parsed.payload["rationale"],
        "evidence_refs": parsed.payload.get("evidence_refs") or [],
    }

    def _hook(inner_db) -> None:
        target_audit = audit_repo.get_audit(inner_db, audit.id)
        if target_audit is None:  # pragma: no cover - 同事务内不应消失
            return
        if question is not None:
            fresh = audit_repo.get_question(inner_db, audit.id, question.id)
            if fresh is not None:
                audit_repo.record_assessment(
                    inner_db,
                    fresh,
                    assessment=assessment,
                    rationale=parsed.payload["rationale"],
                    evidence_refs=parsed.payload.get("evidence_refs") or [],
                    now_iso=now_iso,
                )
        audit_repo.add_assessment_revision(
            inner_db,
            audit_id=audit.id,
            audit_version=target_audit.audit_version + 1,
            dimension=parsed.payload.get("dimension"),
            question_id=question_id,
            before=before,
            after=after,
            origin="human",
            source_message_ids=[],
            evidence_refs=parsed.payload.get("evidence_refs") or [],
            rationale=parsed.payload["rationale"],
            now_iso=now_iso,
        )
        audit_repo.update_audit(
            inner_db,
            target_audit,
            now_iso=now_iso,
            audit_version=target_audit.audit_version + 1,
        )
        session_repo.touch_session(
            inner_db, session, now_iso=now_iso, current_audit_id=audit.id
        )

    return _hook


def _bump_state(state: ProductState, **updates: Any) -> ProductState:
    payload = state.model_dump(mode="json")
    payload.update(updates)
    payload["version"] = state.version + 1
    payload["created_at"] = utc_now_iso()
    return ProductState.model_validate(payload)


# --------------------------------------------------------------------------- #
# 控制意图
# --------------------------------------------------------------------------- #


def _submit_control(
    db,
    *,
    session: SessionRow,
    parsed: C.ParsedCommand,
    owner_email: str,
    now_iso: str,
    now_epoch: int,
) -> tuple[ControlIntentRow, AuditRow]:
    action = parsed.action
    audit_id = parsed.payload["audit_id"]
    audit = audit_repo.require_audit(db, audit_id, owner_email=owner_email)
    if audit.session_id != session.id:
        raise AppError(ErrorCode.FORBIDDEN, "审计不属于该会话")
    audit_status = AuditStatus(audit.status)
    if audit_status in (AuditStatus.completed, AuditStatus.cancelled):
        raise AppError(ErrorCode.AUDIT_CLOSED, "审计已结束")

    active_run = run_repo.active_run_for_session(db, session.id)

    if action is Action.cancel_audit:
        pending = audit_repo.pending_end_intent(db, audit.id)
        if pending is not None:
            audit_repo.update_control(
                db,
                pending,
                status=ControlStatus.failed,
                error_code=ErrorCode.CONTROL_SUPERSEDED,
                now_iso=now_iso,
            )
        SM.validate_audit_transition(audit_status, AuditStatus.cancelled)
        control = audit_repo.create_control_intent(
            db,
            session_id=session.id,
            audit_id=audit.id,
            owner_email=owner_email,
            request_id=parsed.request_id,
            action=action.value,
            target_run_id=active_run.id if active_run else None,
            now_iso=now_iso,
        )
        audit_repo.update_audit(
            db,
            audit,
            now_iso=now_iso,
            status=AuditStatus.cancelled,
            end_reason=EndReason.cancelled,
            close_requested=1,
            closing_control_id=control.id,
        )
        if active_run is not None:
            _request_cancel(db, active_run, now_iso=now_iso, audit=cancel_audit_event(audit))
            audit_repo.update_control(
                db, control, status=ControlStatus.scheduled, now_iso=now_iso
            )
        else:
            audit_repo.update_control(
                db, control, status=ControlStatus.applied, now_iso=now_iso
            )
            session_repo.touch_session(
                db,
                session,
                now_iso=now_iso,
                phase=SM.phase_after_audit_end(EndReason.cancelled),
                active_run_id=None,
            )
        session_repo.touch_session(db, session, now_iso=now_iso, current_control_id=control.id)
        session_repo.bump_session_version(db, session)
        return control, audit

    # end_audit：重复提交复用未完成意图
    existing = audit_repo.pending_end_intent(db, audit.id)
    if existing is not None:
        return existing, audit

    control = audit_repo.create_control_intent(
        db,
        session_id=session.id,
        audit_id=audit.id,
        owner_email=owner_email,
        request_id=parsed.request_id,
        action=action.value,
        target_run_id=active_run.id if active_run else None,
        now_iso=now_iso,
    )
    SM.validate_audit_transition(audit_status, AuditStatus.closing)
    audit_repo.update_audit(
        db,
        audit,
        now_iso=now_iso,
        status=AuditStatus.closing,
        end_reason=EndReason.completed,
        close_requested=1,
        closing_control_id=control.id,
    )
    if active_run is not None:
        _request_cancel(db, active_run, now_iso=now_iso, audit=cancel_audit_event(audit))
        audit_repo.update_control(db, control, status=ControlStatus.scheduled, now_iso=now_iso)
    else:
        closing_run = _create_closing_run(
            db,
            session=session,
            audit=audit,
            control=control,
            owner_email=owner_email,
            now_iso=now_iso,
            now_epoch=now_epoch,
        )
        audit_repo.update_control(
            db,
            control,
            status=ControlStatus.scheduled,
            related_run_id=closing_run.id,
            now_iso=now_iso,
        )
    session_repo.touch_session(
        db, session, now_iso=now_iso, current_control_id=control.id
    )
    session_repo.bump_session_version(db, session)
    return control, audit


def cancel_audit_event(audit: AuditRow) -> dict[str, Any]:
    """取消请求触发的审计事件负载（供 runner / 控制器复用）。"""

    return {"audit_id": audit.id, "status": "cancelled"}


def _request_cancel(db, run: RunRow, *, now_iso: str, audit: dict[str, Any] | None = None) -> None:
    """请求取消 Run：queued/interrupted 立即终结，running 标 cancel_requested 由 Worker 收尾。

    PRD FR-03：状态机允许 queued → cancelled；没有 Worker 会再领取的排队任务
    必须当场终结，否则会留下永远不结束的占位（AC-30）。
    """

    from sqlalchemy import update as sa_update

    status = RunStatus(run.status)
    if status in (RunStatus.queued, RunStatus.interrupted):
        _set_run_fields(
            db, run, status=RunStatus.cancelled.value, finished_at=now_iso, lease_owner=None
        )
        run_repo.append_event(
            db,
            session_id=run.session_id,
            run_id=run.id,
            type=EventType.run_cancelled.value,
            payload={"status": RunStatus.cancelled.value, "reason": "control_cancelled", "audit": audit or {}},
            now_iso=now_iso,
        )
        session = session_repo.get_session(db, run.session_id)
        if session is not None:
            if session.active_run_id == run.id:
                session_repo.touch_session(db, session, now_iso=now_iso, active_run_id=None)
            session_repo.bump_session_version(db, session)
        return

    db.execute(
        sa_update(RunRow)
        .where(RunRow.id == run.id, RunRow.status.in_(["running", "cancel_requested"]))
        .values(status=RunStatus.cancel_requested.value)
    )
    db.refresh(run)
    run_repo.append_event(
        db,
        session_id=run.session_id,
        run_id=run.id,
        type=EventType.audit_updated.value,
        payload={"audit": audit or {}, "control": "cancel_requested"},
        now_iso=now_iso,
    )


def _create_closing_run(
    db,
    *,
    session: SessionRow,
    audit: AuditRow,
    control: ControlIntentRow,
    owner_email: str,
    now_iso: str,
    now_epoch: int,
) -> RunRow:
    run = run_repo.create_run(
        db,
        session_id=session.id,
        owner_email=owner_email,
        request_id=f"control:{control.id}:{new_id()[:8]}",
        action=Action.end_audit.value,
        input_hash=input_hash(Action.end_audit.value, f"audit:{audit.id}", {}, audit.product_version),
        base_session_version=session.session_version,
        base_product_version=audit.product_version,
        budget=default_budget(output_tokens=C.MAX_SYNTHESIS_OUTPUT_TOKENS),
        now_iso=now_iso,
        deadline_at=now_epoch + C.RUN_TIMEOUT_SECONDS,
        queue_deadline_at=now_epoch + C.QUEUE_WAIT_LIMIT_SECONDS,
        audit_id=audit.id,
        base_audit_version=audit.audit_version,
        run_id=new_id(),
    )
    session_repo.touch_session(db, session, now_iso=now_iso, active_run_id=run.id)
    return run


def reconcile_control_intents(db, *, now_iso: str | None = None, now_epoch: int | None = None) -> list[str]:
    """扫描器：旧 Run 终结后建唯一 closing Run / 完成取消（PRD FR-07）。

    由 Worker 每轮调用；返回本轮处理的 control_id 列表。
    """

    now_iso = now_iso or utc_now_iso()
    now_epoch = int(now_epoch if now_epoch is not None else time.time())
    touched: list[str] = []
    controls = db.execute(
        select(ControlIntentRow).where(
            ControlIntentRow.status.in_(
                [ControlStatus.accepted.value, ControlStatus.scheduled.value]
            )
        )
    ).scalars().all()
    for control in controls:
        audit = audit_repo.get_audit(db, control.audit_id)
        if audit is None:
            audit_repo.update_control(
                db, control, status=ControlStatus.failed, error_code=ErrorCode.NOT_FOUND, now_iso=now_iso
            )
            touched.append(control.id)
            continue
        session = session_repo.get_session(db, control.session_id)
        if session is None:
            continue
        active = run_repo.active_run_for_session(db, session.id)
        if active is not None:
            continue

        if control.action == Action.cancel_audit.value:
            if AuditStatus(audit.status) not in (AuditStatus.cancelled, AuditStatus.completed):
                try:
                    audit_repo.update_audit(
                        db,
                        audit,
                        now_iso=now_iso,
                        status=AuditStatus.cancelled,
                        end_reason=EndReason.cancelled,
                    )
                except AppError:  # pragma: no cover - 已终态
                    pass
            audit_repo.update_control(db, control, status=ControlStatus.applied, now_iso=now_iso)
            session_repo.touch_session(
                db,
                session,
                now_iso=now_iso,
                phase=SM.phase_after_audit_end(EndReason.cancelled),
                active_run_id=None,
            )
            touched.append(control.id)
            continue

        # end_audit：若已有 closing run 在跑或已终态则跳过
        if control.related_run_id:
            related = run_repo.get_run(db, control.related_run_id)
            if related is not None and not SM.is_terminal_run(RunStatus(related.status)):
                continue
            if related is not None and RunStatus(related.status) is RunStatus.failed:
                audit_repo.update_control(
                    db,
                    control,
                    status=ControlStatus.failed,
                    error_code=related.error_code or ErrorCode.INTERNAL,
                    now_iso=now_iso,
                )
                touched.append(control.id)
                continue
            if related is not None:
                continue
        closing = _create_closing_run(
            db,
            session=session,
            audit=audit,
            control=control,
            owner_email=control.owner_email,
            now_iso=now_iso,
            now_epoch=now_epoch,
        )
        audit_repo.update_control(
            db,
            control,
            status=ControlStatus.scheduled,
            related_run_id=closing.id,
            now_iso=now_iso,
        )
        touched.append(control.id)
    return touched


# --------------------------------------------------------------------------- #
# 结果提交（Worker 唯一写路径）
# --------------------------------------------------------------------------- #


def _set_run_fields(db, run: RunRow, **values: Any) -> None:
    """直接更新 runs 行（命令服务内部使用；仓储层 release_run 需要租约）。"""

    from sqlalchemy import update as sa_update

    if values:
        db.execute(sa_update(RunRow).where(RunRow.id == run.id).values(**values))
    db.refresh(run)


def _walk_run_status(db, run: RunRow, target: RunStatus, *, now_iso: str) -> None:
    """把 Run 从任意非终结态推到 target，途经合法中间态（queued→running→…）。"""

    current = RunStatus(run.status)
    if current is target:
        return
    if current is RunStatus.queued:
        SM.validate_run_transition(current, RunStatus.running)
        _set_run_fields(db, run, status=RunStatus.running.value, started_at=run.started_at or now_iso)
        current = RunStatus.running
    if current is not target:
        SM.validate_run_transition(current, target)
        _set_run_fields(db, run, status=target.value, finished_at=now_iso)


def _commit_outcome(
    db,
    *,
    run: RunRow,
    session: SessionRow,
    outcome: RunOutcome,
    now_iso: str,
    worker_id: str | None = None,
    lease_epoch: int | None = None,
    lease_required: bool = True,
) -> None:
    """一次事务提交：产品状态 + 消息 + 事件 + Run 终结 + 会话版本（PRD §5.3）。

    调用方负责事务边界（``session_scope``）。失败时整笔回滚，不得留下半成品。

    ``lease_required``：同步命令在同一事务内直接完成，没有 Worker 租约，
    因此显式跳过租约校验；异步 Run 必须由持有租约的 Worker 提交。
    """

    if lease_required and worker_id is not None:
        if run.lease_owner != worker_id or int(run.lease_epoch or 0) != int(lease_epoch or 0):
            raise AppError(
                ErrorCode.LEASE_LOST,
                "执行租约已失效，结果不再提交",
                details={"run_id": run.id, "worker_id": worker_id},
            )

    target = RunStatus(outcome.status)
    if SM.terminal_event_for(target) is None:
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            f"提交结果的状态必须是终结态，收到 {target.value}",
            details={"run_id": run.id},
        )

    # 取消请求优先：一旦 cancel_requested，任何成功结果都按 cancelled 终结。
    if RunStatus(run.status) is RunStatus.cancel_requested and target is not RunStatus.cancelled:
        target = RunStatus.cancelled
        outcome = RunOutcome(status=target, error_code=outcome.error_code, warnings=outcome.warnings)

    result_product_version = run.base_product_version or 1
    state_applied = False

    # 1) 产品状态（只增不改）
    if outcome.product_state is not None:
        state = outcome.product_state
        if state.session_id != session.id:
            raise AppError(ErrorCode.INVALID_REQUEST, "产品状态与会话不匹配")
        current = session.current_product_version
        if state.version != current + 1:
            raise AppError(
                ErrorCode.INVALID_REQUEST,
                "产品状态版本必须严格递增 1",
                details={"current_product_version": current, "got": state.version},
            )
        session_repo.save_product_state(db, session, state, now_iso=now_iso)
        result_product_version = state.version
        state_applied = True
        if not any(str(e.get("type")) == EventType.product_state_updated.value for e in outcome.events):
            outcome.events.append(
                {
                    "type": EventType.product_state_updated.value,
                    "payload": {"product_version": state.version},
                }
            )

    # 2) 消息（与结果同事务）
    for message in outcome.messages:
        session_repo.add_message(
            db,
            session,
            sender_kind=message.sender_kind,
            stage=message.stage,
            content=message.content,
            status=message.status,
            role_code=message.role_code,
            run_id=run.id,
            audit_id=message.audit_id,
            message_id=message.message_id,
            now_iso=now_iso,
        )

    # 3) 中间事件（顺序即 event_seq）
    for event in outcome.events:
        event_type = event.get("type")
        if not event_type:
            continue
        run_repo.append_event(
            db,
            session_id=session.id,
            run_id=run.id,
            type=str(event_type),
            payload=event.get("payload") or {},
            now_iso=now_iso,
        )

    # 4) Run 终结 + 恰好一个终结事件
    _walk_run_status(db, run, target, now_iso=now_iso)
    terminal_payload: dict[str, Any] = {"status": target.value}
    if outcome.error_code:
        terminal_payload["error_code"] = outcome.error_code
    if outcome.error_message:
        terminal_payload["message"] = outcome.error_message
    if outcome.result_product_version or result_product_version:
        terminal_payload["product_version"] = result_product_version
    run_repo.append_event(
        db,
        session_id=session.id,
        run_id=run.id,
        type=SM.terminal_event_for(target).value,
        payload=terminal_payload,
        now_iso=now_iso,
    )

    _set_run_fields(
        db,
        run,
        result_product_version=result_product_version,
        error_code=outcome.error_code,
        error_message=outcome.error_message,
        warnings_json=canonical_json(outcome.warnings or []),
        usage_json=canonical_json(outcome.usage or {}),
        lease_owner=None,
        lease_expires_at=None,
    )

    # 5) 会话：释放占位 + 版本推进 + 阶段/简报状态
    #
    # ``session.brief_status`` 只是 ProductState 简报状态的镜像：workflow 不必
    # 关心它（传 None），这里一律从已提交的 product_state 推导，保证两者永不
    # 漂移；只有同步命令（revise/skip/confirm）才显式给出 brief_status。
    effective_brief_status = outcome.brief_status
    if effective_brief_status is None and outcome.product_state is not None:
        effective_brief_status = outcome.product_state.brief.status

    updates: dict[str, Any] = {}
    released_active = session.active_run_id == run.id
    if released_active:
        updates["active_run_id"] = None
    if outcome.session_phase is not None:
        updates["phase"] = outcome.session_phase.value
    if effective_brief_status is not None:
        updates["brief_status"] = effective_brief_status.value
    session_repo.touch_session(db, session, now_iso=now_iso, **updates)
    # 会话版本是并发写校验的锚点：任何可见变更（状态/消息/阶段/占位释放）都要推进。
    if (
        outcome.session_version_bump
        or state_applied
        or bool(outcome.messages)
        or outcome.session_phase is not None
        or effective_brief_status is not None
        or released_active
    ):
        session_repo.bump_session_version(db, session)

    # 6) 额外写入钩子（审计问题/评估等，同事务）
    if outcome.apply is not None:
        outcome.apply(db)


def apply_outcome(
    db,
    *,
    run_id: str,
    worker_id: str,
    lease_epoch: int,
    outcome: RunOutcome,
    now_iso: str | None = None,
) -> None:
    """Worker 提交入口（PRD FR-03）。校验租约后一次事务写入全部结果。"""

    now_iso = now_iso or utc_now_iso()
    run = run_repo.get_run(db, run_id)
    if run is None:
        raise AppError(ErrorCode.NOT_FOUND, "任务不存在", details={"run_id": run_id})
    if SM.is_terminal_run(RunStatus(run.status)):
        logger.warning("忽略迟到结果：run=%s 已终结（%s）", run_id, run.status)
        return
    session = session_repo.get_session(db, run.session_id)
    if session is None or session.deleted_at:
        raise AppError(ErrorCode.NOT_FOUND, "会话不存在或已删除", details={"run_id": run_id})
    _commit_outcome(
        db,
        run=run,
        session=session,
        outcome=outcome,
        now_iso=now_iso,
        worker_id=worker_id,
        lease_epoch=lease_epoch,
    )


# --------------------------------------------------------------------------- #
# 取消与重试（PRD FR-03 / §7.2）
# --------------------------------------------------------------------------- #


def cancel_run(db, *, owner_email: str, run_id: str, now_iso: str | None = None) -> RunView:
    """取消 Run：queued 立即终结；running 置 cancel_requested 由 Worker 收尾。"""

    now_iso = now_iso or utc_now_iso()
    run = run_repo.require_run(db, run_id, owner_email=owner_email)
    status = RunStatus(run.status)
    if SM.is_terminal_run(status):
        return run_to_view(run)

    session = session_repo.get_session(db, run.session_id)
    if session is None:  # pragma: no cover - 外键保证
        raise AppError(ErrorCode.NOT_FOUND, "会话不存在")

    if status in (RunStatus.queued, RunStatus.interrupted):
        _set_run_fields(db, run, status=RunStatus.cancelled.value, finished_at=now_iso, lease_owner=None)
        run_repo.append_event(
            db,
            session_id=session.id,
            run_id=run.id,
            type=EventType.run_cancelled.value,
            payload={"status": RunStatus.cancelled.value, "reason": "user_cancelled"},
            now_iso=now_iso,
        )
        if session.active_run_id == run.id:
            session_repo.touch_session(db, session, now_iso=now_iso, active_run_id=None)
        session_repo.bump_session_version(db, session)
        return run_to_view(run)

    # running / cancel_requested：交给 Worker 在下一个安全点终结
    if status is not RunStatus.cancel_requested:
        _set_run_fields(db, run, status=RunStatus.cancel_requested.value)
        run_repo.append_event(
            db,
            session_id=session.id,
            run_id=run.id,
            type=EventType.audit_updated.value,
            payload={"status": RunStatus.cancel_requested.value, "reason": "cancel_requested"},
            now_iso=now_iso,
        )
    return run_to_view(run)


def retry_run(
    db,
    *,
    owner_email: str,
    run_id: str,
    request_id: str,
    now_iso: str | None = None,
    now_epoch: int | None = None,
) -> RunReceipt:
    """失败步骤重试：建子 Run（PRD FR-03「retry_failed」）。"""

    now_iso = now_iso or utc_now_iso()
    now_epoch = int(now_epoch if now_epoch is not None else time.time())
    original = run_repo.require_run(db, run_id, owner_email=owner_email)

    existing = run_repo.get_idempotency(db, owner_email, request_id)
    if existing is not None:
        body = json.loads(existing.response_json or "{}").get("body") or {}
        try:
            return RunReceipt.model_validate(body).model_copy(update={"reused": True})
        except Exception:  # pragma: no cover
            pass

    status = RunStatus(original.status)
    if status not in (RunStatus.failed, RunStatus.partial_succeeded):
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            "只有失败或部分成功的任务可以重试",
            details={"run_id": run_id, "status": status.value},
        )

    session = session_repo.require_session(db, original.session_id, owner_email=owner_email)
    active = run_repo.active_run_for_session(db, session.id)
    if active is not None:
        raise AppError(
            ErrorCode.SESSION_BUSY,
            "当前会话有正在执行的任务",
            details={"active_run_id": active.id},
        )

    if original.audit_id:
        audit = audit_repo.get_audit(db, original.audit_id)
        if audit is None or AuditStatus(audit.status) in (AuditStatus.cancelled, AuditStatus.completed):
            raise AppError(ErrorCode.RETRY_AUDIT_CONTEXT_CHANGED, "审计已结束，不能重试该步骤")
        if original.base_audit_version is not None and audit.audit_version != original.base_audit_version:
            raise AppError(ErrorCode.RETRY_AUDIT_CONTEXT_CHANGED, "审计上下文已变化，不能重试该步骤")
    if original.base_product_version != session.current_product_version:
        raise AppError(
            ErrorCode.RETRY_CONTEXT_CHANGED,
            "产品状态已变化，不能重试该步骤",
            details={
                "base_product_version": original.base_product_version,
                "current_product_version": session.current_product_version,
            },
        )

    parsed_input_hash = input_hash(
        original.action, f"session:{session.id}", {"retry_of": original.id}, session.session_version
    )
    child = run_repo.create_run(
        db,
        session_id=session.id,
        owner_email=owner_email,
        request_id=request_id,
        action=original.action,
        input_hash=parsed_input_hash,
        base_session_version=session.session_version,
        base_product_version=session.current_product_version,
        budget=json.loads(original.budget_json or "{}") if original.budget_json else default_budget(),
        now_iso=now_iso,
        deadline_at=now_epoch + C.RUN_TIMEOUT_SECONDS,
        queue_deadline_at=now_epoch + C.QUEUE_WAIT_LIMIT_SECONDS,
        parent_run_id=original.id,
        audit_id=original.audit_id,
        base_audit_version=original.base_audit_version,
        run_id=new_id(),
    )
    session_repo.touch_session(db, session, now_iso=now_iso, active_run_id=child.id)
    session_repo.bump_session_version(db, session)
    receipt = _receipt(child, session)
    run_repo.save_idempotency(
        db,
        owner_email=owner_email,
        request_id=request_id,
        action=child.action,
        resource_scope=f"session:{session.id}",
        input_hash=parsed_input_hash,
        response_status=202,
        response={"kind": "run", "body": receipt.model_dump(mode="json")},
        related_run_id=child.id,
        related_control_id=None,
        now_iso=now_iso,
    )
    return receipt

