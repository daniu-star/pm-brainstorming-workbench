from asyncio import to_thread

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api.deps import require_session_owner
from db.session_store import session_store

router = APIRouter(prefix="/api/session", tags=["session"])


class CreateSessionRequest(BaseModel):
    problem_statement: str


@router.post("")
async def create_session(req: CreateSessionRequest, request: Request):
    owner_email = getattr(request.state, "user_email", "")
    session = await to_thread(session_store.create, req.problem_statement, owner_email)
    return session


@router.get("/{session_id}")
async def get_session(session_id: str, request: Request):
    session = await require_session_owner(session_id, request)
    return session


@router.get("")
async def list_sessions(request: Request):
    owner_email = getattr(request.state, "user_email", "")
    return await to_thread(session_store.list_sessions, owner_email)


@router.delete("/{session_id}")
async def delete_session(session_id: str, request: Request):
    await require_session_owner(session_id, request)
    try:
        await to_thread(session_store.delete, session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="无效的会话 ID") from exc
    return {"status": "deleted", "session_id": session_id}
