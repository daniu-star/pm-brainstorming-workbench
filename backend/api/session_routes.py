from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from db.session_store import session_store
from api.deps import get_current_user

router = APIRouter(prefix="/api/session", tags=["session"])


class CreateSessionRequest(BaseModel):
    problem_statement: str


@router.post("")
async def create_session(req: CreateSessionRequest, request: Request):
    user = get_current_user(request)
    session = session_store.create(req.problem_statement, user_token=user["user_token"])
    return session


@router.get("/{session_id}")
async def get_session(session_id: str, request: Request):
    user = get_current_user(request)
    session = session_store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话未找到")
    if session.get("user_token") and session.get("user_token") != user["user_token"]:
        raise HTTPException(status_code=403, detail="无权访问此会话")
    return session


@router.get("")
async def list_sessions(request: Request):
    user = get_current_user(request)
    return session_store.list_sessions(user_token=user["user_token"])


@router.delete("/{session_id}")
async def delete_session(session_id: str, request: Request):
    user = get_current_user(request)
    session = session_store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话未找到")
    if session.get("user_token") and session.get("user_token") != user["user_token"]:
        raise HTTPException(status_code=403, detail="无权删除此会话")
    session_store.delete(session_id)
    return {"status": "deleted", "session_id": session_id}
