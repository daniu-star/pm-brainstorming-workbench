"""v2 消息端点：按会话 + 消息 ID 查询单条消息。

归属检查（AC-27）：消息必须属于调用者的会话，且会话未被删除；
否则一律 404（不泄漏消息是否存在）。
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from api.v2 import common
from domain.errors import AppError, ErrorCode

router = APIRouter(tags=["v2-messages"])


def _find_message_row(db, message_id: str, owner_email: str):
    """按消息 ID + owner 取行：优先仓储接口，缺失时 ORM 只读查询（带归属过滤）。"""

    session_repo = common.repo("session_repo")
    for name in ("get_message_by_id", "find_message", "get_message_row"):
        finder = getattr(session_repo, name, None)
        if callable(finder):
            row = finder(db, message_id)
            if row is None:
                return None
            common.require_owned_session(db, row.session_id, owner_email)
            return row

    from repositories import database as database_module

    message_row = getattr(database_module, "MessageRow", None)
    session_row = getattr(database_module, "SessionRow", None)
    if message_row is None or session_row is None:  # pragma: no cover - 契约保证存在
        raise AppError(ErrorCode.FEATURE_NOT_READY, "消息查询能力尚未就绪")

    from sqlalchemy import select

    statement = (
        select(message_row)
        .join(session_row, session_row.id == message_row.session_id)
        .where(
            message_row.id == message_id,
            session_row.owner_email == owner_email,
            session_row.deleted_at.is_(None),
        )
    )
    return db.scalar(statement)


def _get_message_by_session(session_id: str, message_id: str, request: Request):
    owner_email = common.require_user(request)
    with common.open_db() as db:
        session = common.require_owned_session(db, session_id, owner_email)
        row = common.repo("session_repo").get_message(db, session.id, message_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, "消息不存在")
        payload = common.message_view(row).model_dump(mode="json")
    return payload


@router.get("/sessions/{session_id}/messages/{message_id}")
def get_session_message(session_id: str, message_id: str, request: Request):
    return _get_message_by_session(session_id, message_id, request)


@router.get("/messages/{message_id}")
def get_message(message_id: str, request: Request, session_id: str | None = None):
    if session_id:
        return _get_message_by_session(session_id, message_id, request)
    owner_email = common.require_user(request)
    with common.open_db() as db:
        row = _find_message_row(db, message_id, owner_email)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, "消息不存在")
        payload = common.message_view(row).model_dump(mode="json")
    return payload


__all__ = ["router"]