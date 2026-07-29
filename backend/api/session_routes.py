from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from db.session_store import session_store
from db.team_store import team_store
from api.deps import get_current_user
from core.team_access import ensure_active_team, require_session_access

router = APIRouter(prefix="/api/session", tags=["session"])


class CreateSessionRequest(BaseModel):
    problem_statement: str = Field(min_length=3, max_length=12000)


@router.post("")
async def create_session(req: CreateSessionRequest, request: Request):
    user = get_current_user(request)
    team = ensure_active_team(user)
    session = session_store.create(
        req.problem_statement,
        user_token=user["user_token"],
        team_id=team["id"],
    )
    return session


@router.get("/{session_id}")
async def get_session(session_id: str, request: Request):
    user = get_current_user(request)
    return require_session_access(session_id, user)


@router.get("")
async def list_sessions(request: Request):
    user = get_current_user(request)
    team_ids = {team["id"] for team in team_store.list_for_user(user["user_token"])}
    return session_store.list_sessions(
        user_token=user["user_token"],
        team_ids=team_ids,
    )


@router.delete("/{session_id}")
async def delete_session(session_id: str, request: Request):
    user = get_current_user(request)
    session = require_session_access(session_id, user)
    is_owner = session.get("user_token") == user["user_token"]
    team_role = team_store.member_role(session.get("team_id", ""), user["user_token"])
    if not is_owner and team_role not in {"owner", "admin"}:
        raise HTTPException(status_code=403, detail="仅会话创建者或团队管理员可以删除")
    session_store.delete(session_id)
    return {"status": "deleted", "session_id": session_id}
