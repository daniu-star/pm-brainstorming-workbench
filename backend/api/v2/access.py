"""统一权限页接口（第 39 批）。

* ``GET /api/v2/access``：一次返回「我创建的会话（含共享给谁）/ 别人共享给我的
  会话 / 我的成员组 / 汇总数字」，权限页据此渲染，避免前端三次往返；
* ``DELETE /api/v2/sessions/{session_id}/share``：**成员主动退出**共享。路径用单数
  ``share``，与所有者管理用的 ``/shares/{email}`` 区分开，避免把 ``me`` 当成邮箱。

所有查询都以当前登录账号为准，不接受前端传入的身份参数。
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from api.v2 import common
from domain import contracts as C
from repositories import member_group_repo

router = APIRouter(tags=["v2-access"])

#: 统一权限页每类列表的上限（超出部分靠「最近更新」排序自然截断，本批不做分页）。
ACCESS_PAGE_LIMIT = 50


def _title(session) -> str:
    """会话标题：取问题陈述首行，过长截断（权限页只需可辨认）。"""

    text = " ".join((session.problem_statement or "").split())
    if not text:
        return "（未填写问题陈述）"
    return text if len(text) <= 60 else text[:60] + "…"


@router.get("/access")
def get_access_overview(request: Request):
    email = common.require_user(request)
    with common.open_db() as db:
        session_repo = common.repo("session_repo")
        owned_rows = session_repo.list_owned_sessions(db, email, limit=ACCESS_PAGE_LIMIT)
        incoming_rows = session_repo.list_incoming_shares(db, email, limit=ACCESS_PAGE_LIMIT)
        shares = session_repo.shares_by_session(db, [row.id for row in owned_rows])

        owned = [
            C.AccessOwnedSessionView(
                session_id=row.id,
                title=_title(row),
                phase=C.SessionPhase(row.phase),
                updated_at=row.updated_at,
                shares=[
                    C.AccessShareView(
                        email=share.shared_with_email,
                        role=share.role,
                        created_at=share.created_at,
                    )
                    for share in shares.get(row.id, [])
                ],
            )
            for row in owned_rows
        ]
        incoming = [
            C.AccessIncomingSessionView(
                session_id=row.id,
                title=_title(row),
                phase=C.SessionPhase(row.phase),
                owner_email=row.owner_email,
                role=role,  # type: ignore[arg-type]
                updated_at=row.updated_at,
            )
            for row, role in incoming_rows
        ]
        groups = [
            member_group_repo.group_view(db, group)
            for group in member_group_repo.list_groups(db, email)
        ]
        overview = C.AccessOverview(
            owned=owned,
            incoming=incoming,
            groups=groups,
            totals=C.AccessOverviewTotals(
                owned=len(owned),
                # 共享出去的「会话数」而非成员数：权限页的主要问题是
                # 「哪些会话已经外发了」，成员明细在每张卡片里。
                shared_out=sum(1 for item in owned if item.shares),
                incoming=len(incoming),
                groups=len(groups),
            ),
        )
        return overview.model_dump(mode="json")


@router.delete("/sessions/{session_id}/share")
def leave_shared_session(session_id: str, request: Request):
    """成员主动退出共享：删掉自己的共享行，会话随即从「共享给我」消失。"""

    email = common.require_user(request)
    with common.open_db() as db:
        session = common.require_session_read(db, session_id, email)
        if session.owner_email == email:
            raise common.AppError(
                common.ErrorCode.INVALID_REQUEST,
                "你是该会话的所有者，退出共享没有意义；如需删除请使用删除会话",
            )
        removed = common.repo("session_repo").remove_session_share(db, session.id, email)
        if not removed:
            raise common.AppError(
                common.ErrorCode.NOT_FOUND, "你不在该会话的共享列表里"
            )
        return {"status": "left", "session_id": session.id}
