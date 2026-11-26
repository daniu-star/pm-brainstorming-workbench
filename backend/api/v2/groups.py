"""团队工作区：成员组管理 + 一次性把整组人共享到会话。

权限口径与会话共享一致：组的管理只有所有者能做；把组应用到会话等价于对每个
成员执行一次「添加共享」，因此被共享方拿到的仍是 viewer / editor 两种角色，
会话归属、Run 归属与模型 Key 都不变。
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from api.v2 import common
from domain.contracts import ApplyGroupPayload, CreateGroupPayload, GroupMemberPayload
from domain.errors import AppError, ErrorCode

router = APIRouter(tags=["v2-groups"])


def _repo():
    return common.repo("member_group_repo")


@router.get("/groups")
def list_groups(request: Request):
    user_email = common.require_user(request)
    with common.open_db() as db:
        repo = _repo()
        groups = repo.list_groups(db, user_email)
        return {"items": [repo.group_view(db, group) for group in groups]}


@router.post("/groups", status_code=201)
def create_group(body: CreateGroupPayload, request: Request):
    user_email = common.require_user(request)
    with common.open_db() as db:
        repo = _repo()
        row = repo.create_group(db, owner_email=user_email, name=body.name)
        payload = repo.group_view(db, row)
    return JSONResponse(status_code=201, content=payload.model_dump(mode="json"))


@router.delete("/groups/{group_id}")
def delete_group(group_id: str, request: Request):
    user_email = common.require_user(request)
    with common.open_db() as db:
        repo = _repo()
        group = repo.require_group(db, group_id=group_id, owner_email=user_email)
        repo.delete_group(db, group)
    return {"deleted": True}


@router.post("/groups/{group_id}/members", status_code=201)
def add_group_member(group_id: str, body: GroupMemberPayload, request: Request):
    user_email = common.require_user(request)
    with common.open_db() as db:
        repo = _repo()
        group = repo.require_group(db, group_id=group_id, owner_email=user_email)
        row = repo.add_member(db, group, email=body.email, role=body.role)
        payload = {
            "email": row.email,
            "role": row.role,
            "created_at": row.created_at,
        }
    return JSONResponse(status_code=201, content=payload)


@router.delete("/groups/{group_id}/members/{email}")
def remove_group_member(group_id: str, email: str, request: Request):
    user_email = common.require_user(request)
    with common.open_db() as db:
        repo = _repo()
        group = repo.require_group(db, group_id=group_id, owner_email=user_email)
        if not repo.remove_member(db, group.id, email):
            raise AppError(ErrorCode.NOT_FOUND, "该账号不在成员组里")
    return {"removed": True}


@router.post("/sessions/{session_id}/shares/from-group", status_code=201)
def share_session_from_group(session_id: str, body: ApplyGroupPayload, request: Request):
    """把成员组一次性应用到会话（幂等：已是成员的会被跳过）。"""

    user_email = common.require_user(request)
    with common.open_db() as db:
        session = common.require_session_owner(db, session_id, user_email)
        repo = _repo()
        group = repo.require_group(db, group_id=body.group_id, owner_email=user_email)
        added, already, total = repo.apply_group(
            db, session, group, created_by=user_email
        )
        payload = {
            "group_id": group.id,
            "added": added,
            "already_shared": already,
            "members_total": total,
        }
    return JSONResponse(status_code=201, content=payload)


__all__ = ["router"]
