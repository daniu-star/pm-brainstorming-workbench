"""会话共享：所有者把会话授予其它账号「查看」或「可提交命令」。

* 共享不改变会话归属，删除共享行即撤销；
* 列表对所有者与共享成员可见（便于协作者知道还有谁在看）；
* 增删只有所有者可以做。
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from api.v2 import common
from domain.contracts import SessionSharePayload
from domain.errors import AppError, ErrorCode

router = APIRouter(tags=["v2-shares"])


def _share_view(row) -> dict:
    return {
        "email": row.shared_with_email,
        "role": row.role,
        "created_at": row.created_at,
    }


@router.get("/sessions/{session_id}/shares")
def list_shares(session_id: str, request: Request):
    user_email = common.require_user(request)
    with common.open_db() as db:
        session = common.require_session_read(db, session_id, user_email)
        rows = common.repo("session_repo").list_session_shares(db, session.id)
        return {"items": [_share_view(row) for row in rows]}


@router.post("/sessions/{session_id}/shares", status_code=201)
def add_share(session_id: str, body: SessionSharePayload, request: Request):
    user_email = common.require_user(request)
    with common.open_db() as db:
        session = common.require_session_owner(db, session_id, user_email)
        row = common.repo("session_repo").add_session_share(
            db,
            session,
            email=body.email,
            role=body.role,
            created_by=user_email,
        )
        payload = _share_view(row)
    return JSONResponse(status_code=201, content=payload)


@router.delete("/sessions/{session_id}/shares/{email}")
def remove_share(session_id: str, email: str, request: Request):
    user_email = common.require_user(request)
    with common.open_db() as db:
        session = common.require_session_owner(db, session_id, user_email)
        removed = common.repo("session_repo").remove_session_share(db, session.id, email)
        if not removed:
            raise AppError(ErrorCode.NOT_FOUND, "该账号不在共享列表里")
    return {"removed": True}


__all__ = ["router"]
