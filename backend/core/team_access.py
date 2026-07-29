from fastapi import HTTPException

from db.session_store import session_store
from db.team_store import team_store
from db.user_store import user_store


def ensure_active_team(user: dict) -> dict:
    user_token = user["user_token"]
    active_team_id = user.get("active_team_id", "")
    if active_team_id:
        active_team = team_store.get(active_team_id)
        if active_team and team_store.is_member(active_team_id, user_token):
            return active_team

    teams = team_store.list_for_user(user_token)
    team = teams[0] if teams else team_store.create(user)
    user_store.set_active_team(user_token, team["id"])
    return team


def require_team_member(team_id: str, user: dict) -> dict:
    team = team_store.get(team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="团队不存在")
    if not team_store.is_member(team_id, user["user_token"]):
        raise HTTPException(status_code=403, detail="无权访问该团队")
    return team


def require_team_manager(team_id: str, user: dict) -> dict:
    team = require_team_member(team_id, user)
    role = team_store.member_role(team_id, user["user_token"])
    if role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="仅团队负责人或管理员可以执行此操作")
    return team


def require_session_access(session_id: str, user: dict) -> dict:
    session = session_store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话未找到")
    if session.get("user_token") == user["user_token"]:
        return session
    team_id = session.get("team_id", "")
    if team_id and team_store.is_member(team_id, user["user_token"]):
        return session
    raise HTTPException(status_code=403, detail="无权访问此团队会话")
