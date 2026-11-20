"""v2 会话端点：创建 / 列表 / 详情 / 软删除 / 消息分页 / 产品状态。

所有端点鉴权复用中间件写入的 ``request.state.user_email``，并逐条做归属检查：
非本人 403，不存在或已删除 404。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from api.v2 import common
from domain import contracts as C
from domain.errors import AppError, ErrorCode

logger = logging.getLogger(__name__)

router = APIRouter(tags=["v2-sessions"])


class CreateSessionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    problem_statement: str
    request_id: str | None = None


class DeleteSessionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str | None = None
    expected_session_version: int | None = None


@router.post("/sessions", status_code=201)
def create_session(body: CreateSessionBody, request: Request):
    owner_email = common.require_user(request)
    request_id = common.optional_request_id(request, body.request_id)
    now_iso = C.utc_now_iso()
    with common.open_db() as db:
        view, reused = common.create_session_core(
            db,
            owner_email=owner_email,
            problem_statement=body.problem_statement,
            request_id=request_id,
            now_iso=now_iso,
        )
    payload = view.model_dump(mode="json")
    payload["reused"] = reused
    return JSONResponse(status_code=201, content=payload)


@router.get("/sessions")
def list_sessions(request: Request, cursor: str | None = None, limit: int | None = None):
    owner_email = common.require_user(request)
    page_limit = common.clamp_limit(
        limit, default=C.SESSIONS_PAGE_DEFAULT, maximum=C.SESSIONS_PAGE_MAX
    )
    with common.open_db() as db:
        rows, next_cursor = common.repo("session_repo").list_sessions(
            db, owner_email, cursor=cursor, limit=page_limit
        )
        items = [common.session_view(db, row).model_dump(mode="json") for row in rows]
    return {"items": items, "next_cursor": next_cursor}


@router.get("/sessions/{session_id}/runs")
def list_session_runs(
    session_id: str,
    request: Request,
    status: str | None = None,
    limit: int | None = None,
):
    """列出会话任务（默认最近 10 条），供「上次任务失败」这类界面读取。"""

    owner_email = common.require_user(request)
    page_limit = common.clamp_limit(limit, default=10, maximum=50)
    statuses = [item.strip() for item in (status or "").split(",") if item.strip()]
    with common.open_db() as db:
        session = common.require_owned_session(db, session_id, owner_email)
        rows = common.repo("run_repo").list_session_runs(
            db, session.id, statuses=statuses or None, limit=page_limit
        )
        items = [common.run_view(row).model_dump(mode="json") for row in rows]
    return {"items": items, "next_cursor": None}


@router.get("/sessions/{session_id}")
def get_session(session_id: str, request: Request):
    owner_email = common.require_user(request)
    with common.open_db() as db:
        session = common.require_owned_session(db, session_id, owner_email)
        payload = common.session_view(db, session).model_dump(mode="json")
        state = common.get_product_state(db, session.id)
    payload["brief"] = state.brief.model_dump(mode="json")
    return payload


@router.delete("/sessions/{session_id}", status_code=202)
def delete_session(
    session_id: str,
    request: Request,
    body: DeleteSessionBody | None = None,
):
    """逻辑删除（幂等）。删除时顺带取消该会话的活动任务。"""

    owner_email = common.require_user(request)
    request_id = common.require_idempotency_key(request)
    now_iso = C.utc_now_iso()
    scope = f"session:{session_id}"
    with common.open_db() as db:
        session = common.require_owned_session(db, session_id, owner_email, include_deleted=True)
        stored = common.idempotent_body(
            db, owner_email, request_id, action="delete_session", scope=scope
        )
        if stored is not None:
            return JSONResponse(status_code=202, content=stored)

        if (
            body is not None
            and body.expected_session_version is not None
            and body.expected_session_version != session.session_version
        ):
            raise AppError(
                ErrorCode.VERSION_CONFLICT,
                "会话版本已变化，请刷新后重试",
                details={
                    "expected_session_version": body.expected_session_version,
                    "current_session_version": session.session_version,
                },
            )

        if session.active_run_id:
            try:
                common.service("command_service").cancel_run(
                    db, owner_email=owner_email, run_id=session.active_run_id, now_iso=now_iso
                )
            except AppError as exc:
                # 任务已终结/已被取消：删除会话不必因此失败。
                logger.info("删除会话时取消活动任务被跳过：%s", exc.code)

        session = common.require_owned_session(db, session_id, owner_email, include_deleted=True)
        if session.deleted_at is None:
            common.repo("session_repo").soft_delete_session(db, session, now_iso=now_iso)
            session = common.require_owned_session(
                db, session_id, owner_email, include_deleted=True
            )
        payload = {
            "session_id": session.id,
            "deleted_at": session.deleted_at or now_iso,
        }
        common.store_idempotent(
            db,
            owner_email,
            request_id,
            action="delete_session",
            scope=scope,
            body=payload,
            http_status=202,
            now_iso=now_iso,
        )
    return JSONResponse(status_code=202, content=payload)


@router.get("/sessions/{session_id}/messages")
def list_messages(
    session_id: str,
    request: Request,
    before_seq: int | None = None,
    limit: int | None = None,
    stage: str | None = None,
    audit_id: str | None = None,
):
    owner_email = common.require_user(request)
    page_limit = common.clamp_limit(
        limit, default=C.MESSAGES_PAGE_DEFAULT, maximum=C.MESSAGES_PAGE_MAX
    )
    if before_seq is not None and before_seq < 1:
        raise AppError(ErrorCode.INVALID_REQUEST, "before_seq 必须大于等于 1")
    stage_enum = common.iter_legacy_stage(stage)
    with common.open_db() as db:
        session = common.require_owned_session(db, session_id, owner_email)
        rows = common.repo("session_repo").list_messages(
            db,
            session.id,
            before_seq=before_seq,
            limit=page_limit,
            stage=stage_enum.value if stage_enum is not None else None,
            audit_id=audit_id,
        )
        items = [common.message_view(row).model_dump(mode="json") for row in rows]
    next_before_seq = rows[0].seq if rows and len(rows) >= page_limit else None
    return {"items": items, "next_before_seq": next_before_seq}


@router.get("/sessions/{session_id}/product-state")
def get_product_state(session_id: str, request: Request, version: int | None = None):
    owner_email = common.require_user(request)
    with common.open_db() as db:
        session = common.require_owned_session(db, session_id, owner_email)
        state = common.get_product_state(db, session.id, version)
    return state.model_dump(mode="json")


# 兼容 PRD §7.2 的别名路径（同一语义，避免前端两套写法）。
@router.get("/sessions/{session_id}/product-state/latest", include_in_schema=False)
def get_latest_product_state(session_id: str, request: Request):
    return get_product_state(session_id, request, version=None)


__all__ = ["router"]