"""审计仓储（PRD FR-07 / 接口契约 §3.3）。

职责：
- 审计实例、审计问题、评估修订、报告修订与控制意图的持久化访问；
- 快照只增不改：评估修订与报告修订只追加新行，旧修订永不覆盖；
- 事务边界由调用方通过 ``repositories.database.session_scope()`` 控制，
  本模块只 ``flush``，**不 commit**。

所有写库函数都兼容 ``domain.contracts`` 的枚举入参（写入前取 ``.value``），
也接受同值字符串，便于服务层/测试按需传参。
"""
from __future__ import annotations

import json
import logging
from typing import Any, Iterable

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from domain.contracts import (
    AUDIT_DIMENSIONS,
    AUDIT_QUESTION_HARD_LIMIT,
    Action,
    Assessment,
    AuditReport,
    AuditSnapshot,
    AuditStatus,
    ControlStatus,
    DimensionState,
    EndReason,
    OriginKind,
    canonical_json,
    new_id,
)
from domain.errors import AppError, ErrorCode
from repositories.database import (
    AuditAssessmentRevisionRow,
    AuditQuestionRow,
    AuditReportRevisionRow,
    AuditRow,
    ControlIntentRow,
    SessionRow,
)

logger = logging.getLogger(__name__)

UNSET: object = object()
"""哨兵：区分“保持原值”与“显式置空”。"""

_OPEN_AUDIT_STATUSES: tuple[str, ...] = ("created", "opening", "active", "closing")
_PENDING_END_STATUSES: tuple[str, ...] = ("accepted", "scheduled")


# --------------------------------------------------------------------------- #
# 内部工具
# --------------------------------------------------------------------------- #


def _value(raw: Any) -> Any:
    """枚举/字符串统一取标量值；None 原样返回。"""

    if raw is None:
        return None
    return getattr(raw, "value", raw)


def _plain(payload: Any) -> Any:
    """把 pydantic 模型/模型列表转换为可 JSON 序列化的原生结构。"""

    if isinstance(payload, (list, tuple)):
        return [_plain(item) for item in payload]
    dump = getattr(payload, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    return payload


def _json_text(payload: Any) -> str:
    return canonical_json(_plain(payload))


def _email_matches(stored: str | None, given: str) -> bool:
    return (stored or "").strip().lower() == (given or "").strip().lower()


def _enum_values(enum_cls: Any) -> frozenset[str]:
    return frozenset(item.value for item in enum_cls)


def _load_list(text: str | None) -> list[Any]:
    if not text:
        return []
    try:
        value = json.loads(text)
    except ValueError:  # pragma: no cover - 旧数据损坏时降级为空列表
        logger.warning("审计 JSON 列不是合法数组，已降级为空：%r", text[:80])
        return []
    return value if isinstance(value, list) else []


def _load_dict(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    try:
        value = json.loads(text)
    except ValueError:  # pragma: no cover - 旧数据损坏时降级为空
        logger.warning("审计 JSON 列不是合法对象，已忽略：%r", text[:80])
        return None
    return value if isinstance(value, dict) else None


# --------------------------------------------------------------------------- #
# 审计实例
# --------------------------------------------------------------------------- #


def create_audit(
    db: Session,
    *,
    session_id: str,
    product_version: int,
    now_iso: str,
    audit_id: str | None = None,
) -> AuditRow:
    """在指定产品版本上创建审计实例（status=created）。"""

    if not isinstance(product_version, int) or product_version < 1:
        raise AppError(ErrorCode.INVALID_REQUEST, "product_version 必须是大于等于 1 的整数")
    audit_id_value = audit_id or new_id()
    # ON CONFLICT DO NOTHING + RETURNING：重复 ID 不抛异常，避免污染调用方事务。
    stmt = (
        sqlite_insert(AuditRow)
        .values(
            id=audit_id_value,
            session_id=session_id,
            product_version=product_version,
            audit_version=1,
            status=AuditStatus.created.value,
            question_count=0,
            end_reason=None,
            dimension_states_json=canonical_json([]),
            report_json=None,
            active_question_id=None,
            pending_continuation=0,
            close_requested=0,
            closing_control_id=None,
            created_at=now_iso,
            updated_at=now_iso,
        )
        .on_conflict_do_nothing(index_elements=["id"])
        .returning(AuditRow.id)
    )
    inserted = db.execute(stmt).scalar_one_or_none()
    if inserted is None:
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            "审计创建失败：审计 ID 已存在",
            details={"audit_id": audit_id_value},
        )
    row = db.get(AuditRow, inserted)
    if row is None:  # pragma: no cover - 插入成功后必然可读回
        raise AppError(ErrorCode.NOT_FOUND, "审计创建后无法读回", details={"audit_id": inserted})
    return row


def get_audit(db: Session, audit_id: str) -> AuditRow | None:
    """按 ID 读取审计实例；不存在返回 None。"""

    if not audit_id:
        return None
    return db.get(AuditRow, audit_id)


def require_audit(db: Session, audit_id: str, *, owner_email: str) -> AuditRow:
    """读取审计并校验归属：缺失/已删除返回 NOT_FOUND，非本人返回 FORBIDDEN。"""

    audit = get_audit(db, audit_id)
    if audit is None:
        raise AppError(ErrorCode.NOT_FOUND, "审计不存在", details={"audit_id": audit_id})
    session = db.get(SessionRow, audit.session_id)
    if session is None or session.deleted_at:
        raise AppError(ErrorCode.NOT_FOUND, "审计不存在", details={"audit_id": audit_id})
    if not _email_matches(session.owner_email, owner_email):
        raise AppError(ErrorCode.FORBIDDEN, "无权访问该审计", details={"audit_id": audit_id})
    return audit


def open_audit_for_session(db: Session, session_id: str) -> AuditRow | None:
    """返回会话中未结束的审计（created/opening/active/closing），无则 None。"""

    stmt = (
        select(AuditRow)
        .where(
            AuditRow.session_id == session_id,
            AuditRow.status.in_(_OPEN_AUDIT_STATUSES),
        )
        .order_by(AuditRow.created_at.desc(), AuditRow.id.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def latest_audit_for_session(db: Session, session_id: str) -> AuditRow | None:
    """返回会话最近一次审计实例（任意状态）。"""

    stmt = (
        select(AuditRow)
        .where(AuditRow.session_id == session_id)
        .order_by(AuditRow.created_at.desc(), AuditRow.id.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def update_audit(
    db: Session,
    audit: AuditRow,
    *,
    now_iso: str,
    status: Any = UNSET,
    question_count: Any = UNSET,
    end_reason: Any = UNSET,
    active_question_id: Any = UNSET,
    pending_continuation: Any = UNSET,
    close_requested: Any = UNSET,
    closing_control_id: Any = UNSET,
    audit_version: Any = UNSET,
    dimension_states: Any = UNSET,
    report: Any = UNSET,
) -> AuditRow:
    """局部更新审计；未传入的字段保持原值。"""

    if status is not UNSET:
        value = _value(status)
        if value not in _enum_values(AuditStatus):
            raise AppError(ErrorCode.INVALID_REQUEST, f"未知审计状态：{value!r}")
        audit.status = str(value)

    if question_count is not UNSET:
        try:
            count = int(question_count)
        except (TypeError, ValueError) as exc:
            raise AppError(ErrorCode.INVALID_REQUEST, "question_count 必须是整数") from exc
        if count < 0 or count > AUDIT_QUESTION_HARD_LIMIT:
            raise AppError(
                ErrorCode.INVALID_REQUEST,
                f"question_count 必须在 0..{AUDIT_QUESTION_HARD_LIMIT} 之间",
                details={"question_count": count},
            )
        audit.question_count = count

    if end_reason is not UNSET:
        value = _value(end_reason)
        if value is not None and value not in _enum_values(EndReason):
            raise AppError(ErrorCode.INVALID_REQUEST, f"未知结束原因：{value!r}")
        audit.end_reason = None if value is None else str(value)

    if active_question_id is not UNSET:
        audit.active_question_id = None if active_question_id is None else str(active_question_id)

    if pending_continuation is not UNSET:
        audit.pending_continuation = 1 if pending_continuation else 0

    if close_requested is not UNSET:
        audit.close_requested = 1 if close_requested else 0

    if closing_control_id is not UNSET:
        audit.closing_control_id = None if closing_control_id is None else str(closing_control_id)

    if audit_version is not UNSET:
        try:
            version = int(audit_version)
        except (TypeError, ValueError) as exc:
            raise AppError(ErrorCode.INVALID_REQUEST, "audit_version 必须是整数") from exc
        if version < 1:
            raise AppError(ErrorCode.INVALID_REQUEST, "audit_version 必须大于等于 1")
        audit.audit_version = version

    if dimension_states is not UNSET:
        audit.dimension_states_json = _json_text(dimension_states or [])

    if report is not UNSET:
        audit.report_json = None if report is None else _json_text(report)

    audit.updated_at = now_iso
    db.add(audit)
    db.flush()
    return audit


# --------------------------------------------------------------------------- #
# 审计问题
# --------------------------------------------------------------------------- #


def add_question(
    db: Session,
    audit: AuditRow,
    *,
    ordinal: int,
    dimension: Any,
    issue_refs: Iterable[str] | None,
    question_text: str,
    question_message_id: str | None = None,
    now_iso: str,
) -> AuditQuestionRow:
    """新增审计问题；序号 1..18 内唯一，并同步审计 question_count。"""

    try:
        number = int(ordinal)
    except (TypeError, ValueError) as exc:
        raise AppError(ErrorCode.INVALID_REQUEST, "ordinal 必须是整数") from exc
    if number < 1 or number > AUDIT_QUESTION_HARD_LIMIT:
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            f"审计问题序号必须在 1..{AUDIT_QUESTION_HARD_LIMIT} 之间",
            details={"ordinal": number},
        )
    dimension_value = _value(dimension)
    if dimension_value not in AUDIT_DIMENSIONS:
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            f"未知审计维度：{dimension_value!r}",
            details={"dimension": dimension_value},
        )

    question_id_value = new_id()
    # ON CONFLICT DO NOTHING + RETURNING：序号重复时返回 None，不污染调用方事务。
    stmt = (
        sqlite_insert(AuditQuestionRow)
        .values(
            id=question_id_value,
            audit_id=audit.id,
            ordinal=number,
            dimension=str(dimension_value),
            issue_refs_json=canonical_json([str(ref) for ref in (issue_refs or [])]),
            question_text=question_text or "",
            question_message_id=question_message_id,
            answer_message_id=None,
            assessment=Assessment.awaiting_answer.value,
            rationale="",
            evidence_refs_json=canonical_json([]),
            created_at=now_iso,
            updated_at=now_iso,
        )
        .on_conflict_do_nothing(index_elements=["audit_id", "ordinal"])
        .returning(AuditQuestionRow.id)
    )
    inserted = db.execute(stmt).scalar_one_or_none()
    if inserted is None:
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            f"审计问题序号 {number} 已存在",
            details={"audit_id": audit.id, "ordinal": number},
        )
    row = db.get(AuditQuestionRow, inserted)
    if row is None:  # pragma: no cover - 插入成功后必然可读回
        raise AppError(
            ErrorCode.NOT_FOUND,
            "审计问题创建后无法读回",
            details={"question_id": inserted},
        )

    if number > int(audit.question_count or 0):
        audit.question_count = number
    audit.updated_at = now_iso
    db.add(audit)
    db.flush()
    return row


def get_question(db: Session, audit_id: str, question_id: str | None) -> AuditQuestionRow | None:
    """读取审计问题；ID 为空或不存在返回 None。"""

    if not question_id:
        return None
    stmt = select(AuditQuestionRow).where(
        AuditQuestionRow.audit_id == audit_id,
        AuditQuestionRow.id == question_id,
    )
    return db.execute(stmt).scalar_one_or_none()


def questions_for_audit(db: Session, audit_id: str) -> list[AuditQuestionRow]:
    """按 ordinal 升序返回审计问题列表。"""

    stmt = (
        select(AuditQuestionRow)
        .where(AuditQuestionRow.audit_id == audit_id)
        .order_by(AuditQuestionRow.ordinal.asc(), AuditQuestionRow.id.asc())
    )
    return list(db.execute(stmt).scalars())


def answer_question(
    db: Session,
    question: AuditQuestionRow,
    *,
    answer_message_id: str,
    now_iso: str,
) -> None:
    """记录问题对应的回答消息；评估结论由 ``record_assessment`` 单独写入。"""

    question.answer_message_id = answer_message_id
    question.updated_at = now_iso
    db.add(question)
    db.flush()


def record_assessment(
    db: Session,
    question: AuditQuestionRow,
    *,
    assessment: Any,
    rationale: str,
    evidence_refs: Iterable[str] | None,
    now_iso: str,
) -> None:
    """写入问题的评估结论与依据（当前值可被后续修订覆盖，修订历史另存）。"""

    value = _value(assessment)
    if value not in _enum_values(Assessment):
        raise AppError(ErrorCode.INVALID_REQUEST, f"未知评估结论：{value!r}")
    question.assessment = str(value)
    question.rationale = rationale or ""
    question.evidence_refs_json = canonical_json([str(ref) for ref in (evidence_refs or [])])
    question.updated_at = now_iso
    db.add(question)
    db.flush()


# --------------------------------------------------------------------------- #
# 修订记录（只追加）
# --------------------------------------------------------------------------- #


def add_assessment_revision(
    db: Session,
    *,
    audit_id: str,
    audit_version: int,
    dimension: str | None = None,
    question_id: str | None = None,
    before: dict[str, Any],
    after: dict[str, Any],
    origin: Any = "human",
    source_message_ids: Iterable[str] = (),
    evidence_refs: Iterable[str] = (),
    rationale: str = "",
    now_iso: str,
) -> str:
    """追加一条评估修订记录，返回修订 ID（旧修订永不覆盖）。"""

    origin_value = _value(origin)
    if origin_value not in _enum_values(OriginKind):
        raise AppError(ErrorCode.INVALID_REQUEST, f"未知修订来源：{origin_value!r}")
    row = AuditAssessmentRevisionRow(
        id=new_id(),
        audit_id=audit_id,
        audit_version=int(audit_version),
        dimension=dimension,
        question_id=question_id,
        before_json=canonical_json(_plain(before or {})),
        after_json=canonical_json(_plain(after or {})),
        origin=str(origin_value),
        source_message_ids_json=canonical_json([str(item) for item in source_message_ids]),
        evidence_refs_json=canonical_json([str(item) for item in evidence_refs]),
        rationale=rationale or "",
        created_at=now_iso,
    )
    db.add(row)
    db.flush()
    return row.id


def save_report_revision(
    db: Session,
    *,
    audit_id: str,
    audit_version: int,
    report: dict[str, Any],
    generated_by: str = "model",
    now_iso: str,
) -> str:
    """追加一版审计报告，返回修订 ID（旧报告永不覆盖）。"""

    row = AuditReportRevisionRow(
        id=new_id(),
        audit_id=audit_id,
        audit_version=int(audit_version),
        report_json=canonical_json(_plain(report or {})),
        generated_by=generated_by,
        created_at=now_iso,
    )
    db.add(row)
    db.flush()
    return row.id


def list_report_revisions(db: Session, audit_id: str) -> list[AuditReportRevisionRow]:
    """按生成时间升序返回报告修订列表。"""

    stmt = (
        select(AuditReportRevisionRow)
        .where(AuditReportRevisionRow.audit_id == audit_id)
        .order_by(AuditReportRevisionRow.created_at.asc(), AuditReportRevisionRow.id.asc())
    )
    return list(db.execute(stmt).scalars())


# --------------------------------------------------------------------------- #
# 审计快照
# --------------------------------------------------------------------------- #


def audit_to_snapshot(db: Session, audit: AuditRow) -> AuditSnapshot:
    """把审计行组装为 ``AuditSnapshot``（dimension_states + 可选报告）。

    ``db`` 保留在签名中以匹配接口契约；组装只依赖审计行本身，避免额外查询。
    """

    dimensions: list[DimensionState] = []
    for raw in _load_list(audit.dimension_states_json):
        if not isinstance(raw, dict):
            logger.warning("审计维度状态不是对象，已跳过（audit=%s）", audit.id)
            continue
        try:
            dimensions.append(DimensionState.model_validate(raw))
        except ValueError:
            logger.warning("审计维度状态校验失败，已跳过（audit=%s）", audit.id)

    report: AuditReport | None = None
    raw_report = _load_dict(audit.report_json)
    if raw_report is not None:
        try:
            report = AuditReport.model_validate(raw_report)
        except ValueError:
            logger.warning("审计报告 JSON 校验失败，快照降级为无报告（audit=%s）", audit.id)

    return AuditSnapshot(
        id=audit.id,
        session_id=audit.session_id,
        product_version=audit.product_version,
        audit_version=audit.audit_version,
        status=AuditStatus(audit.status),
        question_count=audit.question_count,
        end_reason=EndReason(audit.end_reason) if audit.end_reason else None,
        active_question_id=audit.active_question_id,
        pending_continuation=bool(audit.pending_continuation),
        close_requested=bool(audit.close_requested),
        closing_control_id=audit.closing_control_id,
        dimension_states=dimensions,
        report=report,
        created_at=audit.created_at,
        updated_at=audit.updated_at,
    )


# --------------------------------------------------------------------------- #
# 控制意图（结束/取消审计）
# --------------------------------------------------------------------------- #


def create_control_intent(
    db: Session,
    *,
    session_id: str,
    audit_id: str,
    owner_email: str,
    request_id: str,
    action: Any,
    target_run_id: str | None = None,
    now_iso: str,
) -> ControlIntentRow:
    """登记结束/取消审计的控制意图；同 owner+request_id 幂等返回原行。"""

    action_value = _value(action)
    if action_value not in (Action.end_audit.value, Action.cancel_audit.value):
        raise AppError(ErrorCode.INVALID_REQUEST, f"未知控制动作：{action_value!r}")

    control_id_value = new_id()
    # (owner_email, request_id) 唯一：冲突时不插入，随后回读原行做幂等判断。
    stmt = (
        sqlite_insert(ControlIntentRow)
        .values(
            id=control_id_value,
            session_id=session_id,
            audit_id=audit_id,
            owner_email=owner_email,
            request_id=request_id,
            action=str(action_value),
            target_run_id=target_run_id,
            status=ControlStatus.accepted.value,
            related_run_id=None,
            error_code=None,
            created_at=now_iso,
            updated_at=now_iso,
        )
        .on_conflict_do_nothing(index_elements=["owner_email", "request_id"])
        .returning(ControlIntentRow.id)
    )
    inserted = db.execute(stmt).scalar_one_or_none()
    if inserted is not None:
        row = db.get(ControlIntentRow, inserted)
        if row is None:  # pragma: no cover - 插入成功后必然可读回
            raise AppError(
                ErrorCode.NOT_FOUND,
                "控制意图创建后无法读回",
                details={"control_id": inserted},
            )
        return row

    existing = get_control_by_request(db, owner_email, request_id)
    if existing is None:  # pragma: no cover - 唯一冲突后必然可读回
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            "控制意图创建失败：request_id 冲突",
            details={"request_id": request_id},
        )
    if existing.action != str(action_value):
        raise AppError(
            ErrorCode.IDEMPOTENCY_KEY_REUSED,
            "同一 request_id 已用于不同控制动作",
            details={"request_id": request_id, "original_action": existing.action},
        )
    return existing


def get_control(db: Session, control_id: str) -> ControlIntentRow | None:
    """按 ID 读取控制意图。"""

    if not control_id:
        return None
    return db.get(ControlIntentRow, control_id)


def get_control_by_request(db: Session, owner_email: str, request_id: str) -> ControlIntentRow | None:
    """按 owner+request_id 读取控制意图（幂等键）。"""

    normalized = (owner_email or "").strip().lower()
    stmt = select(ControlIntentRow).where(
        func.lower(ControlIntentRow.owner_email) == normalized,
        ControlIntentRow.request_id == request_id,
    )
    return db.execute(stmt).scalar_one_or_none()


def pending_end_intent(db: Session, audit_id: str) -> ControlIntentRow | None:
    """返回审计中尚未完成的 end_audit 意图（accepted/scheduled）。"""

    stmt = (
        select(ControlIntentRow)
        .where(
            ControlIntentRow.audit_id == audit_id,
            ControlIntentRow.action == Action.end_audit.value,
            ControlIntentRow.status.in_(_PENDING_END_STATUSES),
        )
        .order_by(ControlIntentRow.created_at.desc(), ControlIntentRow.id.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def update_control(
    db: Session,
    control: ControlIntentRow,
    *,
    status: Any,
    related_run_id: Any = UNSET,
    error_code: Any = UNSET,
    now_iso: str,
) -> None:
    """更新控制意图状态（可选关联 Run / 错误码）。"""

    value = _value(status)
    if value not in _enum_values(ControlStatus):
        raise AppError(ErrorCode.INVALID_REQUEST, f"未知控制状态：{value!r}")
    control.status = str(value)
    if related_run_id is not UNSET:
        control.related_run_id = None if related_run_id is None else str(related_run_id)
    if error_code is not UNSET:
        control.error_code = None if error_code is None else str(error_code)
    control.updated_at = now_iso
    db.add(control)
    db.flush()


__all__ = [
    "UNSET",
    "add_assessment_revision",
    "add_question",
    "answer_question",
    "audit_to_snapshot",
    "create_audit",
    "create_control_intent",
    "get_audit",
    "get_control",
    "get_control_by_request",
    "get_question",
    "latest_audit_for_session",
    "list_report_revisions",
    "open_audit_for_session",
    "pending_end_intent",
    "questions_for_audit",
    "record_assessment",
    "require_audit",
    "save_report_revision",
    "update_audit",
    "update_control",
]