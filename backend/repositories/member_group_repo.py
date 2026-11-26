"""成员组（团队工作区）：所有者维护的「常用协作名单」。

为什么单独一层而不是直接共享给多人：

* 会话共享是「一次一条」，多人协作时要逐会话重复点；成员组把名单沉淀下来，
  一次应用即可把整组人写进 ``session_shares``；
* 组本身不授予任何权限——真正的权限判断只看会话级共享行，删除组不会回收
  已经授予的访问，删除共享行才会。这一点在 UI 与文档里都写清楚，避免误解。
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import delete, select

from domain import contracts as C
from domain.errors import AppError, ErrorCode
from repositories.database import MemberGroupMemberRow, MemberGroupRow
from repositories import session_repo

MAX_GROUP_NAME = 80
MAX_GROUPS_PER_OWNER = 50
MAX_MEMBERS_PER_GROUP = 50


def _normalize_email(raw: object) -> str:
    from core.auth import normalize_email

    return normalize_email(raw)


def _clean_name(raw: object) -> str:
    name = " ".join(str(raw or "").split())
    if not name:
        raise AppError(ErrorCode.INVALID_REQUEST, "成员组名称不能为空")
    if len(name) > MAX_GROUP_NAME:
        raise AppError(ErrorCode.INVALID_REQUEST, f"成员组名称不能超过 {MAX_GROUP_NAME} 个字符")
    return name


def create_group(db, *, owner_email: str, name: object, now_iso: str | None = None) -> MemberGroupRow:
    """新建成员组：同一所有者下名称唯一。"""

    from domain.contracts import utc_now_iso

    owner = _normalize_email(owner_email)
    clean = _clean_name(name)
    existing = db.execute(
        select(MemberGroupRow).where(
            MemberGroupRow.owner_email == owner, MemberGroupRow.name == clean
        )
    ).scalars().first()
    if existing is not None:
        raise AppError(ErrorCode.INVALID_REQUEST, f"已有同名成员组：{clean}")
    total = db.execute(
        select(MemberGroupRow.id).where(MemberGroupRow.owner_email == owner)
    ).scalars().all()
    if len(total) >= MAX_GROUPS_PER_OWNER:
        raise AppError(
            ErrorCode.INVALID_REQUEST, f"成员组最多 {MAX_GROUPS_PER_OWNER} 个，请先删掉不再使用的"
        )
    row = MemberGroupRow(
        id=uuid4().hex,
        owner_email=owner,
        name=clean,
        created_at=now_iso or utc_now_iso(),
    )
    db.add(row)
    db.flush()
    return row


def list_groups(db, owner_email: str) -> list[MemberGroupRow]:
    owner = _normalize_email(owner_email)
    return list(
        db.execute(
            select(MemberGroupRow)
            .where(MemberGroupRow.owner_email == owner)
            .order_by(MemberGroupRow.created_at.desc())
        ).scalars()
    )


def get_group(db, group_id: str) -> MemberGroupRow | None:
    return db.execute(
        select(MemberGroupRow).where(MemberGroupRow.id == (group_id or "").strip())
    ).scalars().first()


def require_group(db, *, group_id: str, owner_email: str) -> MemberGroupRow:
    row = get_group(db, group_id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "成员组不存在")
    if row.owner_email != _normalize_email(owner_email):
        raise AppError(ErrorCode.FORBIDDEN, "只有成员组的所有者可以管理它")
    return row


def list_members(db, group_id: str) -> list[MemberGroupMemberRow]:
    return list(
        db.execute(
            select(MemberGroupMemberRow)
            .where(MemberGroupMemberRow.group_id == group_id)
            .order_by(MemberGroupMemberRow.created_at)
        ).scalars()
    )


def add_member(
    db,
    group: MemberGroupRow,
    *,
    email: object,
    role: str = "viewer",
    now_iso: str | None = None,
) -> MemberGroupMemberRow:
    """加入（或改角色）一个成员。重复加入不会报错，只更新角色。"""

    from domain.contracts import utc_now_iso

    clean = _normalize_email(email)
    if len(clean) < 3 or "@" not in clean:
        raise AppError(ErrorCode.INVALID_REQUEST, "请填写合法的邮箱地址")
    if role not in session_repo.SESSION_SHARE_ROLES:
        raise AppError(ErrorCode.INVALID_REQUEST, "角色必须是 viewer 或 editor")
    if clean == group.owner_email:
        raise AppError(ErrorCode.INVALID_REQUEST, "所有者本人不需要加入成员组")
    members = list_members(db, group.id)
    if len(members) >= MAX_MEMBERS_PER_GROUP and all(m.email != clean for m in members):
        raise AppError(
            ErrorCode.INVALID_REQUEST, f"一个成员组最多 {MAX_MEMBERS_PER_GROUP} 人"
        )
    for row in members:
        if row.email == clean:
            row.role = role
            db.flush()
            return row
    row = MemberGroupMemberRow(
        group_id=group.id,
        email=clean,
        role=role,
        created_at=now_iso or utc_now_iso(),
    )
    db.add(row)
    db.flush()
    return row


def remove_member(db, group_id: str, email: object) -> bool:
    clean = _normalize_email(email)
    result = db.execute(
        delete(MemberGroupMemberRow).where(
            MemberGroupMemberRow.group_id == group_id, MemberGroupMemberRow.email == clean
        )
    )
    return bool(result.rowcount)


def delete_group(db, group: MemberGroupRow) -> None:
    db.execute(delete(MemberGroupMemberRow).where(MemberGroupMemberRow.group_id == group.id))
    db.execute(delete(MemberGroupRow).where(MemberGroupRow.id == group.id))


def apply_group(db, session, group: MemberGroupRow, *, created_by: str) -> tuple[int, int, int]:
    """把组内成员一次性写进该会话的共享表。

    返回 ``(新增, 已经是成员, 组内总数)``。跳过所有者本人与已在共享列表里的账号，
    这样重复点「应用到会话」是幂等的。
    """

    members = list_members(db, group.id)
    existing = {row.shared_with_email: row for row in session_repo.list_session_shares(db, session.id)}
    added = 0
    already = 0
    for member in members:
        if member.email == session.owner_email:
            continue
        if member.email in existing:
            already += 1
            continue
        session_repo.add_session_share(
            db,
            session,
            email=member.email,
            role=member.role,
            created_by=created_by,
        )
        added += 1
    return added, already, len(members)


def group_view(db, group: MemberGroupRow) -> C.MemberGroupView:
    return C.MemberGroupView(
        id=group.id,
        name=group.name,
        owner_email=group.owner_email,
        members=[
            C.MemberGroupMemberView(email=row.email, role=row.role, created_at=row.created_at)
            for row in list_members(db, group.id)
        ],
        created_at=group.created_at,
    )


__all__ = [
    "MAX_GROUPS_PER_OWNER",
    "MAX_GROUP_NAME",
    "MAX_MEMBERS_PER_GROUP",
    "add_member",
    "apply_group",
    "create_group",
    "delete_group",
    "get_group",
    "group_view",
    "list_groups",
    "list_members",
    "remove_member",
    "require_group",
]
