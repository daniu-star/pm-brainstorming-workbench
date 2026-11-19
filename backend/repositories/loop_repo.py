"""Loop Studio 仓储：loops / loop_rounds / loop_stage_results / loop_artifacts（ADR-03）。

事务纪律与其它仓储一致：只做读写与 flush，不 commit（由调用方 ``session_scope`` 统一提交）。
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from domain import contracts as C
from domain.errors import AppError, ErrorCode

from .database import LoopArtifactRow, LoopRoundRow, LoopRow, LoopStageResultRow
from .session_repo import UNSET

__all__ = [
    "create_loop",
    "get_loop",
    "require_loop",
    "latest_loop_for_session",
    "list_loops",
    "active_loop_for_session",
    "touch_loop",
    "bump_loop_version",
    "close_loop",
    "get_round",
    "list_rounds",
    "ensure_round",
    "update_round",
    "record_stage",
    "get_stage",
    "list_stages",
    "add_artifact",
    "get_artifact",
    "list_artifacts",
    "loop_to_snapshot",
    "loop_round_to_view",
    "stage_to_view",
]

_ACTIVE_LOOP_STATUSES = (C.LoopStatus.running.value, C.LoopStatus.awaiting_decision.value)


def _dumps(model_or_dict: Any) -> str:
    if hasattr(model_or_dict, "model_dump"):
        return C.canonical_json(model_or_dict.model_dump(mode="json"))
    return C.canonical_json(model_or_dict or {})


def _loads(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):  # pragma: no cover - 数据损坏时降级
        return {}
    return value if isinstance(value, dict) else {}


# --------------------------------------------------------------------------- #
# loops
# --------------------------------------------------------------------------- #


def create_loop(
    db,
    *,
    session_id: str,
    owner_email: str,
    scope: C.LoopScope | str = C.LoopScope.mvp,
    max_rounds: int = C.LOOP_ROUNDS_DEFAULT,
    target_score: int = C.LOOP_TARGET_SCORE_DEFAULT,
    focus: str = "",
    latest_run_id: str | None = None,
    now_iso: str | None = None,
    loop_id: str | None = None,
) -> LoopRow:
    now = now_iso or C.utc_now_iso()
    scope_value = scope.value if isinstance(scope, C.LoopScope) else str(scope)
    if not C.is_session_id(session_id):
        raise AppError(ErrorCode.INVALID_REQUEST, "session_id 非法")
    if not (C.MIN_LOOP_ROUNDS <= max_rounds <= C.MAX_LOOP_ROUNDS):
        raise AppError(ErrorCode.INVALID_REQUEST, "max_rounds 超出范围")
    if not (C.LOOP_TARGET_SCORE_MIN <= target_score <= C.LOOP_TARGET_SCORE_MAX):
        raise AppError(ErrorCode.INVALID_REQUEST, "target_score 超出范围")

    row = LoopRow(
        id=loop_id or C.new_id(),
        session_id=session_id,
        owner_email=owner_email,
        scope=scope_value,
        status=C.LoopStatus.running.value,
        current_round=1,
        max_rounds=max_rounds,
        target_score=target_score,
        loop_version=1,
        focus=focus or "",
        latest_run_id=latest_run_id,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    return row


def get_loop(db, loop_id: str) -> LoopRow | None:
    if not loop_id:
        return None
    return db.get(LoopRow, loop_id)


def require_loop(db, *, loop_id: str, owner_email: str | None = None) -> LoopRow:
    row = get_loop(db, loop_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "闭环不存在", details={"loop_id": loop_id})
    if owner_email is not None and row.owner_email != owner_email:
        raise AppError(ErrorCode.FORBIDDEN, "无权访问该闭环")
    return row


def latest_loop_for_session(db, session_id: str) -> LoopRow | None:
    return db.execute(
        select(LoopRow)
        .where(LoopRow.session_id == session_id)
        .order_by(LoopRow.created_at.desc(), LoopRow.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def active_loop_for_session(db, session_id: str) -> LoopRow | None:
    return db.execute(
        select(LoopRow)
        .where(LoopRow.session_id == session_id, LoopRow.status.in_(_ACTIVE_LOOP_STATUSES))
        .order_by(LoopRow.created_at.desc(), LoopRow.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def list_loops(db, session_id: str, *, limit: int = 20) -> list[LoopRow]:
    limit = max(1, min(int(limit), 50))
    return list(
        db.execute(
            select(LoopRow)
            .where(LoopRow.session_id == session_id)
            .order_by(LoopRow.created_at.desc(), LoopRow.id.desc())
            .limit(limit)
        ).scalars()
    )


def touch_loop(
    db,
    row: LoopRow,
    *,
    status: Any = UNSET,
    current_round: Any = UNSET,
    loop_version: Any = UNSET,
    target_score: Any = UNSET,
    max_rounds: Any = UNSET,
    latest_run_id: Any = UNSET,
    decision: Any = UNSET,
    decision_note: Any = UNSET,
    closed_at: Any = UNSET,
    now_iso: str | None = None,
) -> LoopRow:
    now = now_iso or C.utc_now_iso()
    payload: dict[str, Any] = {"updated_at": now}
    if status is not UNSET:
        payload["status"] = status.value if isinstance(status, C.LoopStatus) else status
    if current_round is not UNSET:
        payload["current_round"] = int(current_round)
    if loop_version is not UNSET:
        payload["loop_version"] = int(loop_version)
    if target_score is not UNSET:
        payload["target_score"] = int(target_score)
    if max_rounds is not UNSET:
        payload["max_rounds"] = int(max_rounds)
    if latest_run_id is not UNSET:
        payload["latest_run_id"] = latest_run_id
    if decision is not UNSET:
        payload["decision"] = decision.value if isinstance(decision, C.LoopDecisionKind) else decision
    if decision_note is not UNSET:
        payload["decision_note"] = decision_note
    if closed_at is not UNSET:
        payload["closed_at"] = closed_at
    for key, value in payload.items():
        setattr(row, key, value)
    db.flush()
    return row


def bump_loop_version(db, row: LoopRow, *, now_iso: str | None = None) -> int:
    return int(touch_loop(db, row, loop_version=int(row.loop_version or 0) + 1, now_iso=now_iso).loop_version)


def close_loop(
    db,
    row: LoopRow,
    *,
    status: C.LoopStatus,
    decision: C.LoopDecisionKind | None = None,
    note: str | None = None,
    now_iso: str | None = None,
) -> LoopRow:
    now = now_iso or C.utc_now_iso()
    return touch_loop(
        db,
        row,
        status=status,
        decision=decision,
        decision_note=note,
        closed_at=now,
        loop_version=int(row.loop_version or 0) + 1,
        now_iso=now,
    )


# --------------------------------------------------------------------------- #
# loop_rounds
# --------------------------------------------------------------------------- #


def get_round(db, *, loop_id: str, ordinal: int) -> LoopRoundRow | None:
    return db.execute(
        select(LoopRoundRow).where(LoopRoundRow.loop_id == loop_id, LoopRoundRow.ordinal == int(ordinal))
    ).scalar_one_or_none()


def list_rounds(db, loop_id: str) -> list[LoopRoundRow]:
    return list(
        db.execute(
            select(LoopRoundRow).where(LoopRoundRow.loop_id == loop_id).order_by(LoopRoundRow.ordinal.asc())
        ).scalars()
    )


def ensure_round(
    db,
    *,
    loop_id: str,
    ordinal: int,
    run_id: str | None = None,
    now_iso: str | None = None,
) -> LoopRoundRow:
    """幂等创建轮次行（(loop_id, ordinal) 唯一）。"""

    existing = get_round(db, loop_id=loop_id, ordinal=ordinal)
    if existing is not None:
        if run_id and not existing.run_id:
            existing.run_id = run_id
            db.flush()
        return existing
    now = now_iso or C.utc_now_iso()
    row = LoopRoundRow(
        id=C.new_id(),
        loop_id=loop_id,
        ordinal=int(ordinal),
        run_id=run_id,
        status=C.LoopRoundStatus.running.value,
        verdict=C.LoopVerdict.pending.value,
        started_at=now,
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        again = get_round(db, loop_id=loop_id, ordinal=ordinal)
        if again is None:
            raise
        return again
    return row


def update_round(
    db,
    row: LoopRoundRow,
    *,
    status: C.LoopRoundStatus | None = None,
    score: Any = UNSET,
    verdict: C.LoopVerdict | None = None,
    run_id: Any = UNSET,
    finished_at: Any = UNSET,
) -> LoopRoundRow:
    if status is not None:
        row.status = status.value
    if score is not UNSET:
        row.score = score
    if verdict is not None:
        row.verdict = verdict.value
    if run_id is not UNSET:
        row.run_id = run_id
    if finished_at is not UNSET:
        row.finished_at = finished_at
    db.flush()
    return row


# --------------------------------------------------------------------------- #
# loop_stage_results
# --------------------------------------------------------------------------- #


def get_stage(db, *, loop_id: str, ordinal: int, stage: C.LoopStage | str) -> LoopStageResultRow | None:
    stage_value = stage.value if isinstance(stage, C.LoopStage) else str(stage)
    return db.execute(
        select(LoopStageResultRow).where(
            LoopStageResultRow.loop_id == loop_id,
            LoopStageResultRow.round_ordinal == int(ordinal),
            LoopStageResultRow.stage == stage_value,
        )
    ).scalar_one_or_none()


def list_stages(db, *, loop_id: str, ordinal: int) -> list[LoopStageResultRow]:
    rows = list(
        db.execute(
            select(LoopStageResultRow).where(
                LoopStageResultRow.loop_id == loop_id,
                LoopStageResultRow.round_ordinal == int(ordinal),
            )
        ).scalars()
    )
    order = {stage.value: index for index, stage in enumerate(C.LOOP_STAGES)}
    return sorted(rows, key=lambda r: order.get(r.stage, 99))


def record_stage(
    db,
    *,
    loop_id: str,
    ordinal: int,
    stage: C.LoopStage | str,
    status: C.LoopStageStatus,
    summary: str = "",
    payload: Any = None,
    error_code: str | None = None,
    now_iso: str | None = None,
) -> LoopStageResultRow:
    """upsert 阶段结果；同一轮同一阶段只保留最新一条（重试/修订幂等）。"""

    now = now_iso or C.utc_now_iso()
    stage_value = stage.value if isinstance(stage, C.LoopStage) else str(stage)
    status_value = status.value if isinstance(status, C.LoopStageStatus) else str(status)
    row = get_stage(db, loop_id=loop_id, ordinal=ordinal, stage=stage_value)

    if row is None:
        row = LoopStageResultRow(
            id=C.new_id(),
            loop_id=loop_id,
            round_ordinal=int(ordinal),
            stage=stage_value,
            status=status_value,
            summary=summary or "",
            payload_json=_dumps(payload),
            error_code=error_code,
            created_at=now,
            updated_at=now,
        )
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
            return row
        except IntegrityError:
            row = get_stage(db, loop_id=loop_id, ordinal=ordinal, stage=stage_value)
            if row is None:  # pragma: no cover - 唯一约束冲突后必然可读
                raise

    row.status = status_value
    row.summary = summary or ""
    if payload is not None:
        row.payload_json = _dumps(payload)
    row.error_code = error_code
    row.updated_at = now
    db.flush()
    return row


def stage_to_view(row: LoopStageResultRow) -> C.LoopStageView:
    return C.LoopStageView(
        stage=C.LoopStage(row.stage),
        status=C.LoopStageStatus(row.status),
        summary=row.summary or "",
        error_code=row.error_code,
        updated_at=row.updated_at or "",
    )


# --------------------------------------------------------------------------- #
# loop_artifacts
# --------------------------------------------------------------------------- #


def add_artifact(
    db,
    *,
    loop_id: str,
    ordinal: int,
    kind: C.LoopArtifactKind | str,
    title: str,
    payload: Any,
    now_iso: str | None = None,
) -> LoopArtifactRow:
    """同一轮同一产物类型只保留一条（先删后插，保证重放不重复）。"""

    now = now_iso or C.utc_now_iso()
    kind_value = kind.value if isinstance(kind, C.LoopArtifactKind) else str(kind)
    for old in list_artifacts(db, loop_id=loop_id, ordinal=ordinal):
        if old.kind == kind_value:
            db.delete(old)
    db.flush()
    row = LoopArtifactRow(
        id=C.new_id(),
        loop_id=loop_id,
        round_ordinal=int(ordinal),
        kind=kind_value,
        title=(title or "")[:300],
        payload_json=_dumps(payload),
        created_at=now,
    )
    db.add(row)
    db.flush()
    return row


def get_artifact(db, artifact_id: str) -> LoopArtifactRow | None:
    if not artifact_id:
        return None
    return db.get(LoopArtifactRow, artifact_id)


def list_artifacts(db, *, loop_id: str, ordinal: int | None = None) -> list[LoopArtifactRow]:
    stmt = select(LoopArtifactRow).where(LoopArtifactRow.loop_id == loop_id)
    if ordinal is not None:
        stmt = stmt.where(LoopArtifactRow.round_ordinal == int(ordinal))
    return list(db.execute(stmt.order_by(LoopArtifactRow.round_ordinal.desc(), LoopArtifactRow.created_at.asc())).scalars())


def _artifact_view(row: LoopArtifactRow) -> C.LoopArtifactView:
    return C.LoopArtifactView(
        id=row.id,
        kind=C.LoopArtifactKind(row.kind),
        title=row.title,
        size_bytes=len(row.payload_json or ""),
        created_at=row.created_at or "",
    )


def _payload_of(row: LoopArtifactRow | None) -> dict:
    return _loads(row.payload_json) if row is not None else {}


def loop_round_to_view(db, row: LoopRoundRow) -> C.LoopRoundView:
    stages = [stage_to_view(item) for item in list_stages(db, loop_id=row.loop_id, ordinal=row.ordinal)]
    artifacts = [_artifact_view(item) for item in list_artifacts(db, loop_id=row.loop_id, ordinal=row.ordinal)]
    return C.LoopRoundView(
        id=row.id,
        ordinal=int(row.ordinal),
        status=C.LoopRoundStatus(row.status),
        score=row.score,
        verdict=C.LoopVerdict(row.verdict),
        stages=stages,
        artifacts=artifacts,
        started_at=row.started_at or "",
        finished_at=row.finished_at,
    )


def loop_to_snapshot(db, row: LoopRow, *, include_rounds: bool = True) -> C.LoopSnapshot:
    rounds_rows = list_rounds(db, row.id)
    current_ordinal = int(row.current_round or 1)
    artifacts = list_artifacts(db, loop_id=row.id)

    def latest_of(kind: C.LoopArtifactKind) -> LoopArtifactRow | None:
        for item in artifacts:  # 已按 round_ordinal desc / created_at asc 排序
            if item.kind == kind.value:
                return item
        return None

    snapshot = C.LoopSnapshot(
        loop_id=row.id,
        session_id=row.session_id,
        owner_email=row.owner_email,
        status=C.LoopStatus(row.status),
        scope=C.LoopScope(row.scope),
        current_round=current_ordinal,
        max_rounds=int(row.max_rounds or C.LOOP_ROUNDS_DEFAULT),
        target_score=int(row.target_score or C.LOOP_TARGET_SCORE_DEFAULT),
        loop_version=int(row.loop_version or 1),
        focus=row.focus or "",
        latest_run_id=row.latest_run_id,
        created_at=row.created_at or "",
        updated_at=row.updated_at or "",
    )

    spec_row = latest_of(C.LoopArtifactKind.mvp_spec)
    validation_row = latest_of(C.LoopArtifactKind.validation)
    prototype_row = latest_of(C.LoopArtifactKind.prototype)
    report_row = latest_of(C.LoopArtifactKind.test_report)
    score_row = latest_of(C.LoopArtifactKind.scorecard)

    if spec_row is not None:
        snapshot.mvp_spec = C.LoopMvpSpec.model_validate(_payload_of(spec_row))
    if validation_row is not None:
        snapshot.validation = C.LoopValidation.model_validate(_payload_of(validation_row))
    if prototype_row is not None:
        snapshot.prototype = C.PrototypeSpec.model_validate(_payload_of(prototype_row))
    if report_row is not None:
        snapshot.test_report = C.LoopTestReport.model_validate(_payload_of(report_row))
    if score_row is not None:
        snapshot.scorecard = C.LoopScoreCard.model_validate(_payload_of(score_row))

    if include_rounds:
        snapshot.rounds = [loop_round_to_view(db, item) for item in rounds_rows]
        if not snapshot.rounds:
            # 还没有轮次行时至少给出当前轮的骨架，前端可直接渲染时间线
            snapshot.rounds = [
                C.LoopRoundView(
                    id=f"round-{current_ordinal}",
                    ordinal=current_ordinal,
                    stages=[C.LoopStageView(stage=stage) for stage in C.LOOP_STAGES],
                )
            ]
    return snapshot
