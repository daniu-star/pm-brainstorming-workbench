"""旧会话路由（/api/session）→ v2 适配器（PRD §7.3、AC-27）。

* 查询：读 v2 仓储（SQLite），**不再触碰** ``db.session_store`` 的 JSON 文件；
* 写入：建会话走 ``session_repo.create_session``（与 v2 同一实现），删除走软删；
* 已删除会话：所有旧路由 404；
* 响应体保持 v1 结构（phase 用旧值 define/coach/brainstorm/interview）。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel

from api.v2 import common
from domain import contracts as C

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/session", tags=["session"])


class CreateSessionRequest(BaseModel):
    problem_statement: str
    request_id: str | None = None


@router.post("")
def create_session(req: CreateSessionRequest, request: Request):
    owner_email = common.require_user(request)
    request_id = common.optional_request_id(request, req.request_id)
    with common.open_db() as db:
        view, _reused = common.create_session_core(
            db,
            owner_email=owner_email,
            problem_statement=req.problem_statement,
            request_id=request_id,
            now_iso=C.utc_now_iso(),
        )
        session = common.require_owned_session(db, view.id, owner_email)
        # 首条 user 消息在创建事务内已落库，旧接口直接返回真实消息列表。
        return common.legacy_session_payload(db, session)


@router.get("/{session_id}")
def get_session(session_id: str, request: Request):
    owner_email = common.require_user(request)
    with common.open_db() as db:
        session = common.require_owned_session(db, session_id, owner_email)
        return common.legacy_session_payload(db, session)


@router.get("")
def list_sessions(request: Request):
    owner_email = common.require_user(request)
    with common.open_db() as db:
        rows, _next_cursor = common.repo("session_repo").list_sessions(
            db, owner_email, limit=C.SESSIONS_PAGE_MAX
        )
        return [common.legacy_session_payload(db, row) for row in rows]


@router.delete("/{session_id}")
def delete_session(session_id: str, request: Request):
    """旧删除接口：软删（幂等语义保持旧行为——删除后再访问 404）。"""

    owner_email = common.require_user(request)
    now_iso = C.utc_now_iso()
    with common.open_db() as db:
        session = common.require_owned_session(db, session_id, owner_email)
        if session.active_run_id:
            try:
                common.service("command_service").cancel_run(
                    db, owner_email=owner_email, run_id=session.active_run_id, now_iso=now_iso
                )
            except Exception:  # noqa: BLE001 - 取消失败不应阻断删除，但必须留痕
                logger.exception("旧删除接口取消活动任务失败：session=%s", session_id)
        session = common.require_owned_session(db, session_id, owner_email)
        if session.deleted_at is None:
            common.repo("session_repo").soft_delete_session(db, session, now_iso=now_iso)
    return {"status": "deleted", "session_id": session_id}