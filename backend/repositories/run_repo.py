"""Run / Step / Event / 幂等 仓储（PRD §5.3、FR-03、ADR-04）。

关键约束：
* 所有状态迁移必须走 ``domain.state_machine``，仓储不自行发明迁移；
* 提交结果必须校验 ``lease_owner`` + ``lease_epoch``（PRD §5.3 第 8 条）；
* ``events`` 表无 ``next_event_seq`` 列，序号用 ``MAX(seq)+1`` 并处理唯一键冲突；
* 每一步成功结果最多应用一次：``finish_step`` 只允许从 running 迁移一次。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Sequence

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError, OperationalError

from domain import state_machine as SM
from domain.contracts import (
    EventType,
    RunStatus,
    StepStatus,
    canonical_json,
    new_id,
    utc_now_iso,
)
from domain.errors import AppError, ErrorCode
from repositories.database import (
    EventRow,
    IdempotencyRow,
    RunRow,
    StepCandidateRow,
    StepRow,
)

logger = logging.getLogger(__name__)

#: queued/running/interrupted 都算「占位中」，cancel_requested 也算活动。
ACTIVE_RUN_STATUSES = (
    RunStatus.queued.value,
    RunStatus.running.value,
    RunStatus.interrupted.value,
    RunStatus.cancel_requested.value,
)

MAX_EVENT_SEQ_ATTEMPTS = 3


def _value(item: Any) -> Any:
    return getattr(item, "value", item)


# --------------------------------------------------------------------------- #
# Run 生命周期
# --------------------------------------------------------------------------- #


def create_run(
    db,
    *,
    session_id: str,
    owner_email: str,
    request_id: str,
    action: str,
    input_hash: str,
    base_session_version: int,
    base_product_version: int,
    budget: dict[str, Any] | None = None,
    now_iso: str | None = None,
    deadline_at: int | None = None,
    queue_deadline_at: int | None = None,
    parent_run_id: str | None = None,
    failed_steps: list[str] | None = None,
    audit_id: str | None = None,
    base_audit_version: int | None = None,
    question_id: str | None = None,
    run_id: str | None = None,
) -> RunRow:
    """建 queued Run，并在同一事务写 ``run.queued`` 事件（seq=1）。"""

    now_iso = now_iso or utc_now_iso()
    run = RunRow(
        id=run_id or new_id(),
        session_id=session_id,
        owner_email=owner_email,
        request_id=request_id,
        action=_value(action),
        input_hash=input_hash,
        base_session_version=int(base_session_version),
        base_product_version=int(base_product_version),
        status=RunStatus.queued.value,
        budget_json=canonical_json(budget or {}),
        usage_json="{}",
        warnings_json="[]",
        deadline_at=int(deadline_at) if deadline_at is not None else None,
        queue_deadline_at=int(queue_deadline_at) if queue_deadline_at is not None else None,
        parent_run_id=parent_run_id,
        failed_steps_json=canonical_json(list(failed_steps or [])),
        audit_id=audit_id,
        base_audit_version=base_audit_version,
        question_id=question_id,
        created_at=now_iso,
    )
    db.add(run)
    db.flush()
    append_event(
        db,
        session_id=session_id,
        run_id=run.id,
        type=EventType.run_queued,
        payload={"action": _value(action), "run_id": run.id},
        now_iso=now_iso,
    )
    return run


def get_run(db, run_id: str) -> RunRow | None:
    return db.get(RunRow, run_id)


def require_run(db, run_id: str, *, owner_email: str | None = None) -> RunRow:
    run = get_run(db, run_id)
    if run is None:
        raise AppError(ErrorCode.NOT_FOUND, "任务不存在")
    if owner_email and run.owner_email != owner_email:
        raise AppError(ErrorCode.FORBIDDEN, "无权访问该任务")
    return run


def active_run_for_session(db, session_id: str) -> RunRow | None:
    return db.execute(
        select(RunRow)
        .where(RunRow.session_id == session_id, RunRow.status.in_(ACTIVE_RUN_STATUSES))
        .order_by(RunRow.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def claim_next_run(
    db, *, worker_id: str, now_epoch: int, lease_seconds: int = 30, attempts: int = 3
) -> RunRow | None:
    """原子领取一个可执行 Run：queued 优先，其次租约过期的 running/interrupted。

    领取成功即 ``lease_epoch += 1``（PRD FR-03：接管必须递增 epoch，旧 Worker 提交被拒）。

    实现要点（AC-08）：**不能用「SELECT 一条 → 改 ORM 对象」**，因为两个 Worker 进程
    会各自 SELECT 到同一条 queued Run 然后都认为领取成功。这里改为「取候选 id →
    带条件的 UPDATE → 用 rowcount 判定」，只有把 status 从 queued 改成 running 的那一次
    才算领取成功，竞争失败的连接会继续尝试下一个候选。
    """

    last_exc: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            return _claim_once(
                db, worker_id=worker_id, now_epoch=int(now_epoch), lease_seconds=int(lease_seconds)
            )
        except OperationalError as exc:  # pragma: no cover - 依赖真实并发时序
            text = str(exc).lower()
            if "locked" not in text and "busy" not in text:
                raise
            last_exc = exc
            logger.warning(
                "event=claim.retry worker=%s attempt=%s reason=%s", worker_id, attempt + 1, exc
            )
            db.rollback()
            time.sleep(0.02 * (attempt + 1))
    logger.error("event=claim.failed worker=%s error=%s", worker_id, last_exc)
    return None


def _claim_once(db, *, worker_id: str, now_epoch: int, lease_seconds: int) -> RunRow | None:
    from domain.contracts import RUN_TIMEOUT_SECONDS

    now_iso = utc_now_iso()
    candidates = db.execute(
        select(RunRow.id, RunRow.status)
        .where(
            or_(
                RunRow.status == RunStatus.queued.value,
                and_(
                    RunRow.status.in_(
                        [
                            RunStatus.running.value,
                            RunStatus.interrupted.value,
                            RunStatus.cancel_requested.value,
                        ]
                    ),
                    or_(RunRow.lease_expires_at.is_(None), RunRow.lease_expires_at < int(now_epoch)),
                ),
            )
        )
        .order_by(RunRow.created_at.asc())
        .limit(8)
    ).all()
    if not candidates:
        return None

    for run_id, status in candidates:
        conditions = [RunRow.id == run_id, RunRow.status == status]
        if status == RunStatus.queued.value:
            # queued 只要求未被别人抢先领取。
            conditions.append(RunRow.lease_owner.is_(None))
        else:
            # 接管过期 Run：必须仍然过期（原子条件），否则两个 Worker 会同时接管。
            conditions.append(
                or_(RunRow.lease_expires_at.is_(None), RunRow.lease_expires_at < int(now_epoch))
            )
        result = db.execute(
            update(RunRow)
            .where(*conditions)
            .values(
                status=RunStatus.running.value,
                lease_owner=worker_id,
                lease_epoch=func.coalesce(RunRow.lease_epoch, 0) + 1,
                lease_expires_at=int(now_epoch) + int(lease_seconds),
                heartbeat_at=now_iso,
                started_at=func.coalesce(RunRow.started_at, now_iso),
                deadline_at=func.coalesce(RunRow.deadline_at, int(now_epoch) + RUN_TIMEOUT_SECONDS),
            )
        )
        if result.rowcount != 1:
            continue  # 别人先领取了这条，试下一个候选

        db.flush()
        run = db.get(RunRow, run_id)
        if run is None:  # pragma: no cover - 刚更新过的行不会消失
            continue
        db.refresh(run)
        append_event(
            db,
            session_id=run.session_id,
            run_id=run.id,
            type=EventType.run_started,
            payload={
                "run_id": run.id,
                "worker_id": worker_id,
                "lease_epoch": run.lease_epoch,
                "status": run.status,
            },
            now_iso=now_iso,
        )
        return run
    return None


def renew_lease(db, run: RunRow, *, worker_id: str, lease_epoch: int, now_epoch: int, lease_seconds: int = 30) -> bool:
    """续租；租约已被他人接管返回 False（不抛错，由调用方停止执行）。"""

    result = db.execute(
        update(RunRow)
        .where(
            RunRow.id == run.id,
            RunRow.lease_owner == worker_id,
            RunRow.lease_epoch == int(lease_epoch),
        )
        .values(lease_expires_at=int(now_epoch) + int(lease_seconds), heartbeat_at=utc_now_iso())
    )
    db.flush()
    return result.rowcount == 1


def save_usage(
    db,
    run: RunRow,
    *,
    usage: dict[str, Any],
    worker_id: str,
    lease_epoch: int,
) -> bool:
    """持久化预算账本（不改 Run 状态）；租约不符时返回 False。

    执行器在每个步骤落库后调用，使租约接管/重试后的 ``BudgetTracker``
    能从上一 epoch 的计数继续累计（单 Run 上游调用上限跨进程成立）。
    """

    if run.lease_owner != worker_id or int(run.lease_epoch or 0) != int(lease_epoch):
        return False
    run.usage_json = canonical_json(usage or {})
    db.flush()
    return True


def release_run(
    db,
    run: RunRow,
    *,
    worker_id: str,
    lease_epoch: int,
    status: Any,
    error_code: str | None = None,
    error_message: str | None = None,
    result_product_version: int | None = None,
    warnings: Sequence[str] | None = None,
    usage: dict[str, Any] | None = None,
    now_iso: str | None = None,
) -> RunRow:
    """终结 Run：必须持有当前租约，否则 LEASE_LOST（旧 Worker 的迟到提交）。"""

    now_iso = now_iso or utc_now_iso()
    if run.lease_owner != worker_id or int(run.lease_epoch or 0) != int(lease_epoch):
        raise AppError(
            ErrorCode.LEASE_LOST,
            "执行租约已失效",
            details={"run_id": run.id, "worker_id": worker_id, "expected_epoch": run.lease_epoch},
        )
    current = RunStatus(run.status)
    target = RunStatus(_value(status))
    if not SM.is_terminal_run(target):
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            f"release_run 只接受终结态，收到 {target.value}",
            details={"current": current.value, "target": target.value},
        )
    if current is not target:
        SM.validate_run_transition(current, target)
    run.status = target.value
    run.finished_at = now_iso
    run.lease_owner = None
    run.lease_expires_at = None
    if error_code is not None:
        run.error_code = error_code
    if error_message is not None:
        run.error_message = error_message
    if result_product_version is not None:
        run.result_product_version = int(result_product_version)
    if warnings is not None:
        run.warnings_json = canonical_json(list(warnings))
    if usage is not None:
        run.usage_json = canonical_json(usage)
    db.flush()
    return run


def recover_expired_leases(db, *, now_epoch: int, now_iso: str | None = None) -> list[str]:
    """把租约过期的 running Run 置为 interrupted，等待重新领取（AC-08）。"""

    now_iso = now_iso or utc_now_iso()
    rows = list(
        db.execute(
            select(RunRow).where(
                RunRow.status == RunStatus.running.value,
                RunRow.lease_expires_at.is_not(None),
                RunRow.lease_expires_at < int(now_epoch),
            )
        ).scalars().all()
    )
    recovered: list[str] = []
    for run in rows:
        previous_epoch = run.lease_epoch
        run.status = RunStatus.interrupted.value
        run.lease_owner = None
        run.lease_expires_at = None
        db.flush()
        append_event(
            db,
            session_id=run.session_id,
            run_id=run.id,
            type=EventType.run_recovered,
            payload={"run_id": run.id, "reason": "lease_expired", "lease_epoch": previous_epoch},
            now_iso=now_iso,
        )
        recovered.append(run.id)
    return recovered


def requeue_interrupted(db, *, limit: int = 10) -> list[str]:
    """把 interrupted 且未超时的 Run 重新排队（保持 epoch 不变，领取时再递增）。"""

    rows = list(
        db.execute(
            select(RunRow)
            .where(RunRow.status == RunStatus.interrupted.value)
            .order_by(RunRow.created_at.asc())
            .limit(limit)
        ).scalars().all()
    )
    requeued: list[str] = []
    for run in rows:
        run.status = RunStatus.queued.value
        run.lease_owner = None
        run.lease_expires_at = None
        requeued.append(run.id)
    db.flush()
    return requeued


def expire_queued_runs(db, *, now_epoch: int, now_iso: str | None = None) -> list[str]:
    """排队超时（默认 10 分钟）→ failed(QUEUE_WAIT_EXCEEDED)（FR-03）。"""

    now_iso = now_iso or utc_now_iso()
    rows = list(
        db.execute(
            select(RunRow).where(
                RunRow.status == RunStatus.queued.value,
                RunRow.queue_deadline_at.is_not(None),
                RunRow.queue_deadline_at < int(now_epoch),
            )
        ).scalars().all()
    )
    expired: list[str] = []
    for run in rows:
        run.status = RunStatus.failed.value
        run.error_code = ErrorCode.QUEUE_WAIT_EXCEEDED
        run.error_message = "排队超时，请稍后重试"
        run.finished_at = now_iso
        db.flush()
        append_event(
            db,
            session_id=run.session_id,
            run_id=run.id,
            type=EventType.run_failed,
            payload={
                "status": RunStatus.failed.value,
                "error_code": ErrorCode.QUEUE_WAIT_EXCEEDED,
                "message": "排队超时，请稍后重试",
            },
            now_iso=now_iso,
        )
        expired.append(run.id)
    return expired


def count_running_runs(db) -> int:
    return int(
        db.execute(
            select(func.count()).select_from(RunRow).where(RunRow.status == RunStatus.running.value)
        ).scalar_one()
    )


def count_queued_runs(db, *, owner_email: str | None = None) -> int:
    stmt = select(func.count()).select_from(RunRow).where(RunRow.status == RunStatus.queued.value)
    if owner_email:
        stmt = stmt.where(RunRow.owner_email == owner_email)
    return int(db.execute(stmt).scalar_one())


# --------------------------------------------------------------------------- #
# Step
# --------------------------------------------------------------------------- #


def ensure_step(
    db,
    run: RunRow,
    *,
    logical_key: str,
    kind: str,
    role_code: Any = None,
    input_hash: str | None = None,
    input_snapshot: dict[str, Any] | None = None,
    previous_step_id: str | None = None,
    now_iso: str | None = None,
) -> StepRow:
    """幂等建步骤：同一 (run_id, logical_key) 只建一次（AC-09 不重跑已落库步骤）。"""

    existing = db.execute(
        select(StepRow).where(StepRow.run_id == run.id, StepRow.logical_key == logical_key)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    now_iso = now_iso or utc_now_iso()
    step = StepRow(
        id=new_id(),
        run_id=run.id,
        logical_key=logical_key,
        kind=kind,
        role_code=_value(role_code) if role_code is not None else None,
        status=StepStatus.pending.value,
        attempt_count=0,
        input_hash=input_hash,
        input_snapshot_json=canonical_json(input_snapshot) if input_snapshot is not None else None,
        previous_step_id=previous_step_id,
        created_at=now_iso,
    )
    db.add(step)
    db.flush()
    return step


def get_step(db, step_id: str) -> StepRow | None:
    return db.get(StepRow, step_id)


def get_step_by_key(db, run_id: str, logical_key: str) -> StepRow | None:
    return db.execute(
        select(StepRow).where(StepRow.run_id == run_id, StepRow.logical_key == logical_key)
    ).scalar_one_or_none()


def list_steps(db, run_id: str) -> list[StepRow]:
    return list(
        db.execute(select(StepRow).where(StepRow.run_id == run_id).order_by(StepRow.created_at.asc()))
        .scalars()
        .all()
    )


def start_step(db, step: StepRow, *, now_iso: str | None = None) -> StepRow:
    """pending/interrupted/failed/cancelled → running，并累加尝试次数（上限 2 由 runner 判断）。"""

    now_iso = now_iso or utc_now_iso()
    previous = StepStatus(step.status)
    if previous is not StepStatus.running:
        SM.validate_step_transition(previous, StepStatus.running)
    step.status = StepStatus.running.value
    step.attempt_count = int(step.attempt_count or 0) + 1
    step.started_at = now_iso
    db.flush()
    return step


def finish_step(
    db,
    step: StepRow,
    *,
    status: Any,
    output: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    now_iso: str | None = None,
) -> StepRow:
    """running → 终结态；同一 step 只允许成功应用一次（重复调用直接返回原结果）。"""

    now_iso = now_iso or utc_now_iso()
    target = StepStatus(_value(status))
    current = StepStatus(step.status)
    if current is target:
        return step
    SM.validate_step_transition(current, target)
    step.status = target.value
    step.finished_at = now_iso
    if output is not None:
        step.output_json = canonical_json(output)
    if error_code is not None:
        step.error_code = error_code
    if error_message is not None:
        step.error_message = error_message
    db.flush()
    return step


# --------------------------------------------------------------------------- #
# 候选登记器（FR-04C）
# --------------------------------------------------------------------------- #


def reserve_candidate_id(
    db,
    *,
    run_id: str,
    step_id: str,
    local_id: str,
    kind: str,
    payload: dict[str, Any],
    now_iso: str | None = None,
) -> str:
    """给 (run_id, step_id, local_id) 分配稳定的服务器 ID；已存在返回原 ID（幂等）。"""

    existing = db.execute(
        select(StepCandidateRow).where(
            StepCandidateRow.run_id == run_id,
            StepCandidateRow.step_id == step_id,
            StepCandidateRow.local_id == local_id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing.id
    now_iso = now_iso or utc_now_iso()
    row = StepCandidateRow(
        id=new_id(),
        run_id=run_id,
        step_id=step_id,
        local_id=local_id,
        kind=kind,
        payload_json=canonical_json(payload),
        created_at=now_iso,
    )
    db.add(row)
    db.flush()
    return row.id


def list_candidates(db, run_id: str, *, step_id: str | None = None) -> list[StepCandidateRow]:
    stmt = select(StepCandidateRow).where(StepCandidateRow.run_id == run_id)
    if step_id:
        stmt = stmt.where(StepCandidateRow.step_id == step_id)
    return list(db.execute(stmt.order_by(StepCandidateRow.created_at.asc())).scalars().all())


# --------------------------------------------------------------------------- #
# 事件
# --------------------------------------------------------------------------- #


def latest_event_seq(db, run_id: str) -> int:
    value = db.execute(
        select(func.max(EventRow.seq)).where(EventRow.run_id == run_id)
    ).scalar_one_or_none()
    return int(value or 0)


def append_event(
    db,
    *,
    session_id: str,
    run_id: str,
    type: Any,
    payload: dict[str, Any] | None = None,
    now_iso: str | None = None,
) -> EventRow:
    """追加事件：seq = MAX(seq)+1，唯一键冲突时重试（events 无自增序号列）。"""

    now_iso = now_iso or utc_now_iso()
    event_type = _value(type)
    last_error: Exception | None = None
    for _ in range(MAX_EVENT_SEQ_ATTEMPTS):
        seq = latest_event_seq(db, run_id) + 1
        row = EventRow(
            run_id=run_id,
            seq=seq,
            session_id=session_id,
            schema_version=1,
            type=event_type,
            payload_json=canonical_json(payload or {}),
            created_at=now_iso,
        )
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
            return row
        except IntegrityError as exc:  # 并发追加撞到唯一键，重算 seq
            last_error = exc
            continue
    raise AppError(
        ErrorCode.INTERNAL,
        "事件序号分配失败，请重试",
        retryable=True,
        details={"run_id": run_id},
    ) from last_error


def events_after(db, run_id: str, *, after_seq: int = 0, limit: int = 200) -> list[EventRow]:
    return list(
        db.execute(
            select(EventRow)
            .where(EventRow.run_id == run_id, EventRow.seq > int(after_seq))
            .order_by(EventRow.seq.asc())
            .limit(max(1, min(int(limit), 500)))
        )
        .scalars()
        .all()
    )


def message_snapshot_for_run(db, run_id: str, *, limit: int = 100) -> list[Any]:
    """回放用：该 Run 产生的消息（按 seq 升序）。"""

    from repositories.database import MessageRow

    return list(
        db.execute(
            select(MessageRow)
            .where(MessageRow.run_id == run_id)
            .order_by(MessageRow.seq.asc())
            .limit(limit)
        )
        .scalars()
        .all()
    )


# --------------------------------------------------------------------------- #
# 幂等
# --------------------------------------------------------------------------- #


def get_idempotency(db, owner_email: str, request_id: str) -> IdempotencyRow | None:
    return db.get(IdempotencyRow, {"owner_email": owner_email, "request_id": request_id})


def save_idempotency(
    db,
    *,
    owner_email: str,
    request_id: str,
    action: str,
    resource_scope: str,
    input_hash: str,
    response_status: int,
    response: dict[str, Any],
    related_run_id: str | None = None,
    related_control_id: str | None = None,
    ttl_days: int = 30,
    now_iso: str | None = None,
) -> IdempotencyRow:
    now_iso = now_iso or utc_now_iso()
    expires = time.time() + ttl_days * 86400
    row = IdempotencyRow(
        owner_email=owner_email,
        request_id=request_id,
        action=_value(action),
        resource_scope=resource_scope,
        input_hash=input_hash,
        response_status=int(response_status),
        response_json=canonical_json(response),
        related_run_id=related_run_id,
        related_control_id=related_control_id,
        created_at=now_iso,
        expires_at=int(expires),
    )
    existing = db.get(IdempotencyRow, {"owner_email": owner_email, "request_id": request_id})
    if existing is not None:
        return existing
    db.add(row)
    db.flush()
    return row


def claim_idempotency(
    db,
    *,
    owner_email: str,
    request_id: str,
    action: str,
    resource_scope: str,
    input_hash: str,
    ttl_days: int = 30,
    now_iso: str | None = None,
) -> bool:
    """原子占位幂等键（AC-06）。

    必须在命令处理的**第一步**调用：以主键 ``(owner_email, request_id)`` 抢占一行。
    返回 ``True`` 表示本次请求拿到该键，后续必须在同一事务内调用
    :func:`complete_idempotency` 写入回执；返回 ``False`` 表示键已被别的进程占用
    （处理中或已完成），调用方应回滚后回放已有回执。

    为什么必须"先占位再干活"：若先建 Run 再写幂等记录，两个进程用同一个
    ``request_id`` 并发提交时会各自建 Run（或第二个返回 SESSION_BUSY），
    违反 PRD §5.3 / AC-06 的"一个 Run、一条用户消息、同一回执"。
    """

    now_iso = now_iso or utc_now_iso()
    row = IdempotencyRow(
        owner_email=owner_email,
        request_id=request_id,
        action=_value(action),
        resource_scope=resource_scope,
        input_hash=input_hash,
        response_status=0,
        response_json=None,
        related_run_id=None,
        related_control_id=None,
        created_at=now_iso,
        expires_at=int(time.time() + ttl_days * 86400),
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        return False
    return True


def complete_idempotency(
    db,
    *,
    owner_email: str,
    request_id: str,
    response_status: int,
    response: dict[str, Any],
    related_run_id: str | None = None,
    related_control_id: str | None = None,
) -> IdempotencyRow:
    """给已占位的幂等键补上回执（与结果同事务提交，满足 PRD §5.3）。"""

    row = db.get(IdempotencyRow, {"owner_email": owner_email, "request_id": request_id})
    if row is None:  # pragma: no cover - 占位与完成必须在同一事务内
        raise RuntimeError("幂等键未占位，无法完成")
    row.response_status = int(response_status)
    row.response_json = canonical_json(response)
    row.related_run_id = related_run_id
    row.related_control_id = related_control_id
    db.flush()
    return row
