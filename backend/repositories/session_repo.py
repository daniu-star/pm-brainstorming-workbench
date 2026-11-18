"""会话、消息、产品状态与证据仓储（PRD §5.2 / ADR-03）。

约定（团队共享，见 ``docs/v4-m1-interfaces.md``）：
* 每个函数只做一件事，不做权限或阶段判断；权限由 ``require_*`` 变体做；
* 不自带事务边界：调用方用 ``repositories.database.session_scope()`` 控制提交；
* 枚举参数接受 ``domain.contracts`` 的枚举或字符串，写库时取 ``.value``；
* ``UNSET`` 哨兵用于区分「不改」与「置 None」。
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Sequence

from sqlalchemy import func, select, update

from domain.contracts import (
    BriefStatus,
    Claim,
    DecisionItem,
    ExperimentItem,
    Evidence,
    EvidenceKind,
    MessageStage,
    MessageStatus,
    OptionItem,
    ProductBrief,
    ProductState,
    SenderKind,
    SessionPhase,
    UnresolvedIssue,
    VerificationStatus,
    canonical_json,
    initial_product_state,
    new_id,
    sha256_text,
    utc_now_iso,
)
from domain.errors import AppError, ErrorCode
from repositories.database import EvidenceRow, MessageRow, ProductStateRow, SessionRow

#: 区分「字段不改」与「字段置 None」。
UNSET: Any = object()

MAX_MESSAGES_PAGE = 100
MIN_MESSAGES_PAGE = 1

#: 未完成的消息（流式占位）；软删会话时统一标记为 interrupted。
ACTIVE_MESSAGE_STATUSES = (MessageStatus.draft.value,)


# --------------------------------------------------------------------------- #
# 内部工具
# --------------------------------------------------------------------------- #


def _value(item: Any) -> Any:
    """枚举取 value，其他原样返回。"""

    return getattr(item, "value", item)


def _page_limit(limit: int | None, *, default: int = 50) -> int:
    if limit is None:
        limit = default
    return max(MIN_MESSAGES_PAGE, min(int(limit), MAX_MESSAGES_PAGE))


# --------------------------------------------------------------------------- #
# 会话
# --------------------------------------------------------------------------- #


def create_session(
    db,
    *,
    session_id: str | None = None,
    owner_email: str,
    problem_statement: str,
    now_iso: str | None = None,
) -> SessionRow:
    """建会话：同一事务内写 sessions 行 + 首条人类消息 + ProductState v1 草稿。"""

    now_iso = now_iso or utc_now_iso()
    sid = session_id or new_id()
    session = SessionRow(
        id=sid,
        owner_email=owner_email,
        phase=SessionPhase.clarifying.value,
        session_version=1,
        current_product_version=1,
        problem_statement=problem_statement or "",
        brief_status=BriefStatus.draft.value,
        next_message_seq=1,
        created_at=now_iso,
        updated_at=now_iso,
    )
    db.add(session)
    db.flush()

    # 首条人类消息（seq=1），并把 next_message_seq 推到 2
    db.add(
        MessageRow(
            id=new_id(),
            session_id=sid,
            seq=1,
            sender_kind=SenderKind.human.value,
            role_code=None,
            stage=MessageStage.clarify.value,
            content=problem_statement or "",
            status=MessageStatus.completed.value,
            created_at=now_iso,
        )
    )
    session.next_message_seq = 2

    state = initial_product_state(sid)
    db.add(
        ProductStateRow(
            session_id=sid,
            version=state.version,
            schema_version=state.schema_version,
            payload_json=canonical_json(state.model_dump(mode="json")),
            source_run_id=None,
            created_at=now_iso,
        )
    )
    db.flush()
    return session


def get_session(db, session_id: str, *, include_deleted: bool = False) -> SessionRow | None:
    session = db.get(SessionRow, session_id)
    if session is None:
        return None
    if session.deleted_at and not include_deleted:
        return None
    return session


def require_session(
    db, session_id: str, *, owner_email: str, include_deleted: bool = False
) -> SessionRow:
    session = get_session(db, session_id, include_deleted=include_deleted)
    if session is None:
        raise AppError(ErrorCode.NOT_FOUND, "会话不存在")
    if owner_email and session.owner_email != owner_email:
        # 归属不符与不存在对调用方等价，避免探测他人会话
        raise AppError(ErrorCode.FORBIDDEN, "无权访问该会话")
    return session


def list_sessions(
    db,
    owner_email: str,
    *,
    cursor: str | None = None,
    limit: int = 20,
    include_deleted: bool = False,
) -> tuple[list[SessionRow], str | None]:
    """按 (updated_at DESC, id DESC) 稳定排序；cursor 为上一页最后一条 id。"""

    limit = max(1, min(int(limit), 100))
    stmt = select(SessionRow).where(SessionRow.owner_email == owner_email)
    if not include_deleted:
        stmt = stmt.where(SessionRow.deleted_at.is_(None))
    if cursor:
        anchor = db.get(SessionRow, cursor)
        if anchor is not None:
            # 严格稳定：先按 updated_at 降序，再按 id 降序
            stmt = stmt.where(
                (SessionRow.updated_at < anchor.updated_at)
                | (
                    (SessionRow.updated_at == anchor.updated_at)
                    & (SessionRow.id < anchor.id)
                )
            )
    stmt = stmt.order_by(SessionRow.updated_at.desc(), SessionRow.id.desc()).limit(limit + 1)
    rows = list(db.execute(stmt).scalars().all())
    next_cursor = rows[limit].id if len(rows) > limit else None
    return rows[:limit], next_cursor


def soft_delete_session(db, session: SessionRow, *, now_iso: str | None = None) -> None:
    now_iso = now_iso or utc_now_iso()
    session.deleted_at = now_iso
    session.updated_at = now_iso
    session.active_run_id = None
    db.execute(
        update(MessageRow)
        .where(MessageRow.session_id == session.id, MessageRow.status.in_(ACTIVE_MESSAGE_STATUSES))
        .values(status=MessageStatus.interrupted.value)
    )
    db.flush()


def touch_session(
    db,
    session: SessionRow,
    *,
    now_iso: str | None = None,
    phase: Any = UNSET,
    session_version: Any = UNSET,
    product_version: Any = UNSET,
    active_run_id: Any = UNSET,
    current_audit_id: Any = UNSET,
    current_control_id: Any = UNSET,
    brief_status: Any = UNSET,
) -> SessionRow:
    now_iso = now_iso or utc_now_iso()
    if phase is not UNSET:
        session.phase = _value(phase)
    if session_version is not UNSET:
        session.session_version = int(session_version)
    if product_version is not UNSET:
        session.current_product_version = int(product_version)
    if active_run_id is not UNSET:
        session.active_run_id = active_run_id
    if current_audit_id is not UNSET:
        session.current_audit_id = current_audit_id
    if current_control_id is not UNSET:
        session.current_control_id = current_control_id
    if brief_status is not UNSET:
        session.brief_status = _value(brief_status)
    session.updated_at = now_iso
    db.flush()
    return session


def bump_session_version(db, session: SessionRow) -> int:
    session.session_version = int(session.session_version or 0) + 1
    db.flush()
    return session.session_version


def count_messages(db, session_id: str) -> int:
    return int(
        db.execute(
            select(func.count())
            .select_from(MessageRow)
            .where(MessageRow.session_id == session_id)
        ).scalar_one()
    )


def count_messages_for_sessions(db, session_ids: Sequence[str]) -> dict[str, int]:
    if not session_ids:
        return {}
    rows = db.execute(
        select(MessageRow.session_id, func.count())
        .where(MessageRow.session_id.in_(list(session_ids)))
        .group_by(MessageRow.session_id)
    ).all()
    return {sid: int(count) for sid, count in rows}


# --------------------------------------------------------------------------- #
# 消息
# --------------------------------------------------------------------------- #


def allocate_message_seq(db, session: SessionRow) -> int:
    current = int(session.next_message_seq or 1)
    session.next_message_seq = current + 1
    db.flush()
    return current


def add_message(
    db,
    session: SessionRow,
    *,
    sender_kind: Any,
    stage: Any,
    content: str,
    status: Any = MessageStatus.completed,
    role_code: Any = None,
    run_id: str | None = None,
    audit_id: str | None = None,
    message_id: str | None = None,
    now_iso: str | None = None,
) -> MessageRow:
    now_iso = now_iso or utc_now_iso()
    seq = allocate_message_seq(db, session)
    row = MessageRow(
        id=message_id or new_id(),
        session_id=session.id,
        seq=seq,
        sender_kind=_value(sender_kind),
        role_code=_value(role_code) if role_code is not None else None,
        stage=_value(stage),
        content=content or "",
        status=_value(status),
        run_id=run_id,
        audit_id=audit_id,
        created_at=now_iso,
    )
    db.add(row)
    session.updated_at = now_iso
    db.flush()
    return row


def get_message(db, session_id: str, message_id: str) -> MessageRow | None:
    row = db.get(MessageRow, message_id)
    if row is None or row.session_id != session_id:
        return None
    return row


def list_messages(
    db,
    session_id: str,
    *,
    before_seq: int | None = None,
    limit: int = 50,
    stage: Any = None,
    audit_id: str | None = None,
) -> list[MessageRow]:
    """返回 seq 升序的一页消息；``before_seq`` 取更早的消息。"""

    limit = _page_limit(limit)
    stmt = select(MessageRow).where(MessageRow.session_id == session_id)
    if before_seq is not None:
        stmt = stmt.where(MessageRow.seq < int(before_seq))
    if stage is not None:
        stmt = stmt.where(MessageRow.stage == _value(stage))
    if audit_id is not None:
        stmt = stmt.where(MessageRow.audit_id == audit_id)
    stmt = stmt.order_by(MessageRow.seq.desc()).limit(limit)
    rows = list(db.execute(stmt).scalars().all())
    rows.reverse()
    return rows


# --------------------------------------------------------------------------- #
# 产品状态（只增不改）
# --------------------------------------------------------------------------- #


def get_product_state(db, session_id: str, version: int | None = None) -> ProductState:
    stmt = select(ProductStateRow).where(ProductStateRow.session_id == session_id)
    if version is not None:
        stmt = stmt.where(ProductStateRow.version == int(version))
    stmt = stmt.order_by(ProductStateRow.version.desc()).limit(1)
    row = db.execute(stmt).scalar_one_or_none()
    if row is None:
        return initial_product_state(session_id)
    return ProductState.model_validate(json.loads(row.payload_json))


def save_product_state(
    db, session: SessionRow, state: ProductState, *, now_iso: str | None = None
) -> ProductState:
    """插入新版本行（调用方保证 version 严格递增）；同步会话当前版本。"""

    now_iso = now_iso or utc_now_iso()
    if int(state.version) <= int(session.current_product_version or 1):
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            "产品状态版本必须递增",
            details={
                "current_product_version": session.current_product_version,
                "got": state.version,
            },
        )
    db.add(
        ProductStateRow(
            session_id=session.id,
            version=int(state.version),
            schema_version=int(state.schema_version),
            payload_json=canonical_json(state.model_dump(mode="json")),
            source_run_id=state.source_run_id,
            created_at=now_iso,
        )
    )
    session.current_product_version = int(state.version)
    session.updated_at = now_iso
    db.flush()
    return state


# --------------------------------------------------------------------------- #
# 证据
# --------------------------------------------------------------------------- #


def add_evidence(
    db,
    session: SessionRow | None,
    *,
    kind: Any,
    title: str = "",
    excerpt: str,
    source_uri: str | None = None,
    source_date: str | None = None,
    limitations: str | None = None,
    created_by: str | None = None,
    verification_status: Any = VerificationStatus.unverified,
    now_iso: str | None = None,
) -> EvidenceRow:
    now_iso = now_iso or utc_now_iso()
    content_hash = sha256_text(f"{_value(kind)}:{source_uri or ''}:{excerpt}")
    row = EvidenceRow(
        id=new_id(),
        session_id=session.id if session is not None else None,
        kind=_value(kind),
        source_uri=source_uri,
        title=title or "",
        excerpt=excerpt,
        content_hash=content_hash,
        source_date=source_date,
        retrieved_at=now_iso,
        verification_status=_value(verification_status),
        limitations=limitations,
        created_by=created_by,
        created_at=now_iso,
    )
    db.add(row)
    db.flush()
    return row


def get_evidence(db, evidence_id: str) -> EvidenceRow | None:
    return db.get(EvidenceRow, evidence_id)


def list_evidence(db, session_id: str, *, limit: int = 50) -> list[EvidenceRow]:
    limit = _page_limit(limit)
    return list(
        db.execute(
            select(EvidenceRow)
            .where(EvidenceRow.session_id == session_id)
            .order_by(EvidenceRow.retrieved_at.desc())
            .limit(limit)
        ).scalars().all()
    )


def evidence_to_model(row: EvidenceRow) -> Evidence:
    return Evidence(
        id=row.id,
        session_id=row.session_id,
        kind=EvidenceKind(row.kind),
        source_uri=row.source_uri,
        title=row.title or "",
        excerpt=row.excerpt or "",
        source_date=row.source_date,
        retrieved_at=row.retrieved_at,
        verification_status=VerificationStatus(row.verification_status),
        limitations=row.limitations,
        content_hash=row.content_hash or "",
    )
