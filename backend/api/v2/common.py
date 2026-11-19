"""v2 API 共享工具（鉴权、短事务、视图映射、分页、幂等头、旧字段映射）。

约定（docs/v4-m1-interfaces.md §2/§5）：
* 端点只做「解析 → 调用仓储/服务 → 组装视图」，不直接写 SQL；
* 仓储与服务模块一律**函数内延迟 import**：并行开发期某模块尚未落地时返回
  503 ``FEATURE_NOT_READY``，而不是让整个 app 无法启动；
* 事务使用 ``repositories.database.session_scope()``，块内不做模型/HTTP 调用。
"""
from __future__ import annotations

import importlib
import json
import logging
from typing import Any

from fastapi import Request

from domain import contracts as C
from domain.contracts import (
    AuditSnapshot,
    CanvasProjection,
    ControlReceipt,
    ControlStatus,
    MessageView,
    ProductState,
    RunEvent,
    RunView,
    SessionPhase,
    SessionView,
)
from domain.errors import AppError, ErrorCode

logger = logging.getLogger(__name__)

IDEMPOTENCY_HEADER = "Idempotency-Key"
LAST_EVENT_ID_HEADER = "Last-Event-ID"

#: 旧前端使用的 v1 phase 值（PRD §5.4 兼容映射）。
V1_PHASE = {
    SessionPhase.clarifying.value: "coach",
    SessionPhase.brainstorming.value: "brainstorm",
    SessionPhase.auditing.value: "interview",
    SessionPhase.audit_completed.value: "interview",
}

#: 旧前端的角色 key（target_user 在 v1 里叫 user）。
V1_ROLE = {"target_user": "user"}

ROLE_DISPLAY_NAMES = {
    "coach": "产品教练",
    "cto": "技术负责人",
    "designer": "设计师",
    "ops": "运营负责人",
    "target_user": "目标用户",
    "user": "目标用户",
    "moderator": "主持人",
    "interviewer": "AI 面试官",
}

_MODULE_CACHE: dict[str, Any] = {}


# --------------------------------------------------------------------------- #
# 延迟 import
# --------------------------------------------------------------------------- #


def _module(dotted: str):
    cached = _MODULE_CACHE.get(dotted)
    if cached is not None:
        return cached
    try:
        module = importlib.import_module(dotted)
    except ImportError as exc:  # 并行开发期：模块未落地
        logger.warning("模块 %s 不可用：%s", dotted, exc)
        raise AppError(
            ErrorCode.FEATURE_NOT_READY,
            "该功能尚未就绪",
            details={"module": dotted},
        ) from exc
    _MODULE_CACHE[dotted] = module
    return module


def repo(name: str):
    """取仓储模块，例如 ``repo("session_repo")``。"""

    return _module(f"repositories.{name}")


def service(name: str):
    """取服务模块，例如 ``service("command_service")``。"""

    return _module(f"services.{name}")


def reset_module_cache() -> None:
    """测试/热重载用：清掉延迟 import 缓存。"""

    _MODULE_CACHE.clear()


def open_db():
    """一个短事务上下文（`with open_db() as db:`）。"""

    from repositories.database import session_scope

    return session_scope()


# --------------------------------------------------------------------------- #
# 鉴权与请求元数据
# --------------------------------------------------------------------------- #


def require_user(request: Request) -> str:
    email = getattr(request.state, "user_email", None)
    if not email:
        raise AppError(ErrorCode.UNAUTHENTICATED, "请先使用邮箱登录")
    return str(email)


def header_request_id(request: Request) -> str:
    return (request.headers.get(IDEMPOTENCY_HEADER) or "").strip()


def _validate_request_id(value: str) -> str:
    if not (8 <= len(value) <= 64):
        raise AppError(ErrorCode.INVALID_REQUEST, "request_id 长度必须在 8 到 64 之间")
    return value


def resolve_request_id(request: Request, body_request_id: str | None) -> str:
    """合并 ``Idempotency-Key`` 头与 body.request_id；两者不一致 → 422。"""

    header = header_request_id(request)
    body = (body_request_id or "").strip()
    if header and body and header != body:
        raise AppError(
            ErrorCode.IDEMPOTENCY_KEY_MISMATCH,
            "Idempotency-Key 与 request_id 不一致",
            details={"idempotency_key": header, "request_id": body},
        )
    value = header or body
    if not value:
        raise AppError(ErrorCode.INVALID_REQUEST, "缺少 request_id 或 Idempotency-Key 头")
    return _validate_request_id(value)


def optional_request_id(request: Request, body_request_id: str | None) -> str | None:
    header = header_request_id(request)
    body = (body_request_id or "").strip()
    if header and body and header != body:
        raise AppError(
            ErrorCode.IDEMPOTENCY_KEY_MISMATCH,
            "Idempotency-Key 与 request_id 不一致",
            details={"idempotency_key": header, "request_id": body},
        )
    value = header or body
    return _validate_request_id(value) if value else None


def require_idempotency_key(request: Request) -> str:
    value = header_request_id(request)
    if not value:
        raise AppError(ErrorCode.INVALID_REQUEST, "该请求必须携带 Idempotency-Key 头")
    return _validate_request_id(value)


# --------------------------------------------------------------------------- #
# 分页
# --------------------------------------------------------------------------- #


def clamp_limit(value: int | None, *, default: int, maximum: int, field: str = "limit") -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:  # pragma: no cover - FastAPI 已保证 int
        raise AppError(ErrorCode.INVALID_REQUEST, f"{field} 必须是整数") from exc
    if parsed < 1:
        raise AppError(ErrorCode.INVALID_REQUEST, f"{field} 必须大于 0")
    return min(parsed, maximum)


# --------------------------------------------------------------------------- #
# 会话 / 消息 / 任务视图
# --------------------------------------------------------------------------- #


def message_count(db, session_id: str) -> int:
    session_repo = repo("session_repo")
    counter = getattr(session_repo, "count_messages", None)
    if callable(counter):
        return int(counter(db, session_id))
    return len(session_repo.list_messages(db, session_id, limit=C.MESSAGES_PAGE_MAX))


def session_view(db, session) -> SessionView:
    return SessionView(
        id=session.id,
        owner_email=session.owner_email,
        phase=SessionPhase(session.phase),
        session_version=session.session_version,
        current_product_version=session.current_product_version,
        current_audit_id=session.current_audit_id,
        active_run_id=session.active_run_id,
        problem_statement=session.problem_statement or "",
        brief_status=C.BriefStatus(session.brief_status),
        created_at=session.created_at,
        updated_at=session.updated_at,
        deleted_at=session.deleted_at,
        message_count=message_count(db, session.id),
    )


def message_view(row) -> MessageView:
    return MessageView(
        id=row.id,
        session_id=row.session_id,
        seq=row.seq,
        sender_kind=C.SenderKind(row.sender_kind),
        role_code=C.RoleCode(row.role_code) if row.role_code else None,
        stage=C.MessageStage(row.stage),
        content=row.content or "",
        status=C.MessageStatus(row.status),
        run_id=row.run_id,
        audit_id=row.audit_id,
        created_at=row.created_at,
    )


def run_view(row) -> RunView:
    return RunView(
        id=row.id,
        session_id=row.session_id,
        action=C.Action(row.action),
        status=C.RunStatus(row.status),
        request_id=row.request_id,
        base_session_version=row.base_session_version or 0,
        base_product_version=row.base_product_version or 1,
        result_product_version=row.result_product_version,
        lease_owner=row.lease_owner,
        lease_epoch=row.lease_epoch or 0,
        lease_expires_at=row.lease_expires_at,
        parent_run_id=row.parent_run_id,
        audit_id=row.audit_id,
        error_code=row.error_code,
        error_message=row.error_message,
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        deadline_at=row.deadline_at,
    )


def run_event(row) -> RunEvent:
    try:
        payload = json.loads(row.payload_json or "{}")
    except ValueError:  # pragma: no cover - 数据损坏时降级为空负载
        logger.warning("事件负载不是合法 JSON（run=%s seq=%s）", row.run_id, row.seq)
        payload = {}
    return RunEvent(
        session_id=row.session_id,
        run_id=row.run_id,
        event_seq=row.seq,
        type=row.type,
        occurred_at=row.created_at,
        payload=payload,
    )


# --------------------------------------------------------------------------- #
# 会话/任务归属
# --------------------------------------------------------------------------- #


def require_owned_session(db, session_id: str, owner_email: str, *, include_deleted: bool = False):
    """非本人 403、不存在（或已删除）404；ID 格式非法按不存在处理。"""

    if not C.is_session_id(session_id):
        raise AppError(ErrorCode.NOT_FOUND, "会话不存在")
    return repo("session_repo").require_session(
        db, session_id, owner_email=owner_email, include_deleted=include_deleted
    )


def require_owned_run(db, run_id: str, owner_email: str):
    return repo("run_repo").require_run(db, run_id, owner_email=owner_email)


# --------------------------------------------------------------------------- #
# 产品状态 / 画布 / 审计
# --------------------------------------------------------------------------- #


def get_product_state(db, session_id: str, version: int | None = None) -> ProductState:
    if version is not None and version < 1:
        raise AppError(ErrorCode.INVALID_REQUEST, "product_version 必须大于等于 1")
    state = repo("session_repo").get_product_state(db, session_id, version)
    if version is not None and state.version != version:
        raise AppError(
            ErrorCode.NOT_FOUND,
            f"产品版本 v{version} 不存在",
            details={"requested_version": version, "latest_version": state.version},
        )
    return state


def project_canvas(state: ProductState) -> CanvasProjection:
    """确定性画布投影（M1 无模型调用，PRD FR-05）。"""

    projector = service("canvas_projector")
    fn = getattr(projector, "project_canvas", None)
    if not callable(fn):
        raise AppError(ErrorCode.FEATURE_NOT_READY, "画布投影尚未就绪")
    return fn(state)


def audit_snapshot(db, audit) -> AuditSnapshot:
    return repo("audit_repo").audit_to_snapshot(db, audit)


# --------------------------------------------------------------------------- #
# 幂等（创建/删除/取消等专用端点复用 IdempotencyRecord 表）
# --------------------------------------------------------------------------- #


def idempotent_body(db, owner_email: str, request_id: str, *, action: str, scope: str) -> dict | None:
    """命中时返回上次保存的响应体；同键不同请求 → 409。"""

    record = repo("run_repo").get_idempotency(db, owner_email, request_id)
    if record is None:
        return None
    if record.action != action or (record.resource_scope and record.resource_scope != scope):
        raise AppError(
            ErrorCode.IDEMPOTENCY_KEY_REUSED,
            "同一 request_id 已用于不同请求",
            details={
                "request_id": request_id,
                "original_action": record.action,
                "requested_action": action,
            },
        )
    try:
        stored = json.loads(record.response_json or "{}")
    except ValueError:  # pragma: no cover - 数据损坏
        stored = {}
    body = stored.get("body")
    return body if isinstance(body, dict) else None


def store_idempotent(
    db,
    owner_email: str,
    request_id: str,
    *,
    action: str,
    scope: str,
    body: dict,
    http_status: int,
    now_iso: str,
    related_run_id: str | None = None,
    related_control_id: str | None = None,
) -> None:
    repo("run_repo").save_idempotency(
        db,
        owner_email=owner_email,
        request_id=request_id,
        action=action,
        resource_scope=scope,
        input_hash=C.sha256_text(C.canonical_json({"action": action, "scope": scope})),
        response_status=http_status,
        response={"kind": action, "body": body},
        now_iso=now_iso,
        related_run_id=related_run_id,
        related_control_id=related_control_id,
    )


# --------------------------------------------------------------------------- #
# 会话创建（v2 与旧 /api/session 共用同一条写入路径）
# --------------------------------------------------------------------------- #


def create_session_core(
    db,
    *,
    owner_email: str,
    problem_statement: str,
    request_id: str | None,
    now_iso: str,
) -> tuple[SessionView, bool]:
    """创建会话；返回 (视图, 是否幂等重放)。

    同一事务内完成「会话 + 首条 user 消息 + 首个 clarify Run」：消息由
    ``session_repo.create_session`` 写入，Run 由 ``command_service.start_initial_run``
    写入，因此会话一创建就处于「正在澄清」且 ``active_run_id`` 非空，前端
    订阅 ``active_run_id`` / SSE 即可拿到教练的问题，无需再发一次命令。
    """

    text = (problem_statement or "").strip()
    if not text:
        raise AppError(ErrorCode.INVALID_REQUEST, "问题描述不能为空")
    if len(text) > C.MAX_USER_INPUT_CHARS:
        raise AppError(
            ErrorCode.INPUT_TOO_LONG,
            f"问题描述不能超过 {C.MAX_USER_INPUT_CHARS} 字",
            details={"length": len(text), "limit": C.MAX_USER_INPUT_CHARS},
        )

    if request_id:
        stored = idempotent_body(
            db, owner_email, request_id, action="create_session", scope="session:new"
        )
        if stored is not None:
            return SessionView.model_validate(stored), True

    session = repo("session_repo").create_session(
        db,
        session_id=C.new_id(),
        owner_email=owner_email,
        problem_statement=text,
        now_iso=now_iso,
    )
    service("command_service").start_initial_run(
        db, session=session, owner_email=owner_email, now_iso=now_iso
    )
    view = session_view(db, session)
    if request_id:
        store_idempotent(
            db,
            owner_email,
            request_id,
            action="create_session",
            scope="session:new",
            body=view.model_dump(mode="json"),
            http_status=201,
            now_iso=now_iso,
        )
    return view, False


# --------------------------------------------------------------------------- #
# 旧（v1）字段映射
# --------------------------------------------------------------------------- #


def legacy_phase(phase: str | SessionPhase) -> str:
    value = phase.value if isinstance(phase, SessionPhase) else str(phase)
    return V1_PHASE.get(value, "coach")


def legacy_role(role_code: str | None) -> str:
    if not role_code:
        return "system"
    return V1_ROLE.get(role_code, role_code)


def legacy_role_name(role_code: str | None) -> str:
    if not role_code:
        return "系统"
    return ROLE_DISPLAY_NAMES.get(role_code, role_code)


def legacy_message(row) -> dict:
    """旧前端消息结构：{role, content, timestamp, role_name?}。"""

    payload = {
        "role": "user" if row.sender_kind == C.SenderKind.human.value else "assistant",
        "content": row.content or "",
        "timestamp": row.created_at,
    }
    if row.role_code:
        payload["role_name"] = legacy_role_name(row.role_code)
    return payload


def legacy_session_payload(db, session, *, messages: list | None = None) -> dict:
    """旧后端 /api/session 返回结构（phase 使用 v1 值）。"""

    rows = messages
    if rows is None:
        rows = repo("session_repo").list_messages(db, session.id, limit=C.MESSAGES_PAGE_MAX)
    return {
        "id": session.id,
        "problem_statement": session.problem_statement or "",
        "phase": legacy_phase(session.phase),
        "owner_email": session.owner_email,
        "messages": [legacy_message(row) for row in rows],
        "canvas_tree": None,
        "canvas_last_msg_index": 0,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
    }


#: CanvasNode.kind → 旧功能树叶子 type。
_V1_LEAF_TYPE = {
    "risk": "risk",
    "question": "question",
    "assumption": "question",
    "option": "feature",
    "decision": "feature",
    "experiment": "insight",
    "claim": "insight",
    "user": "insight",
}


def legacy_canvas_tree(projection: CanvasProjection) -> dict:
    """把确定性投影翻译成旧功能树结构（旧前端只读）。"""

    root = projection.root
    branches: list[dict] = []
    for branch in projection.branches:
        children = []
        for node in branch.nodes:
            children.append(
                {
                    "name": node.title or node.kind,
                    "source_role": "coach",
                    "type": _V1_LEAF_TYPE.get(node.kind, "feature"),
                    "source_text": node.detail or "",
                }
            )
        branches.append({"name": branch.title or branch.key, "children": children})
    return {"root": (root.title if root is not None else "") or "", "branches": branches}


def control_receipt(db, control) -> ControlReceipt:
    session_repo = repo("session_repo")
    session = session_repo.get_session(db, control.session_id)
    version = session.session_version if session is not None else 0
    return ControlReceipt(
        control_id=control.id,
        audit_id=control.audit_id,
        status=ControlStatus(control.status),
        session_version=version,
        related_run_id=control.related_run_id,
        action=C.Action(control.action),
    )


def iter_legacy_stage(stage: str | None):
    """校验旧端点的 stage 参数（可选）。"""

    if stage is None:
        return None
    try:
        return C.MessageStage(stage)
    except ValueError as exc:
        raise AppError(ErrorCode.INVALID_REQUEST, f"未知的消息阶段：{stage}") from exc


__all__: list[str] = [
    "IDEMPOTENCY_HEADER",
    "LAST_EVENT_ID_HEADER",
    "ROLE_DISPLAY_NAMES",
    "V1_PHASE",
    "audit_snapshot",
    "clamp_limit",
    "control_receipt",
    "create_session_core",
    "get_product_state",
    "header_request_id",
    "idempotent_body",
    "iter_legacy_stage",
    "legacy_canvas_tree",
    "legacy_message",
    "legacy_phase",
    "legacy_role",
    "legacy_role_name",
    "legacy_session_payload",
    "message_count",
    "message_view",
    "open_db",
    "optional_request_id",
    "project_canvas",
    "repo",
    "require_idempotency_key",
    "require_owned_run",
    "require_owned_session",
    "require_user",
    "reset_module_cache",
    "resolve_request_id",
    "run_event",
    "run_view",
    "service",
    "session_view",
    "store_idempotent",
]

