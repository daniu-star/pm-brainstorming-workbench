from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.deps import require_session_owner
from core.agent_loop import run_agent_turn, run_ask_all, run_coach
from rag.retriever import rag_retriever

router = APIRouter(prefix="/api/brainstorm", tags=["brainstorm"])


class BrainstormMessage(BaseModel):
    session_id: str
    content: str
    target_role: str  # "cto" | "designer" | "ops" | "user" | "all"


@router.post("/message")
async def brainstorm_message(req: BrainstormMessage, request: Request):
    await require_session_owner(req.session_id, request)

    rag_context = ""
    if not rag_retriever.is_empty():
        chunks = await rag_retriever.search(req.content, n_results=4)
        if chunks:
            rag_context = "\n\n".join(f"> {c[:300]}" for c in chunks)

    if req.target_role == "all":
        generator = run_ask_all(req.session_id, req.content, rag_context)
    elif req.target_role in ("cto", "designer", "ops", "user"):
        generator = run_agent_turn(req.session_id, req.content, req.target_role, rag_context)
    else:
        raise HTTPException(status_code=400, detail=f"无效角色: {req.target_role}")

    return StreamingResponse(generator, media_type="text/event-stream")


class CoachRequest(BaseModel):
    session_id: str
    content: str


@router.post("/coach")
async def coach_clarify(req: CoachRequest, request: Request):
    await require_session_owner(req.session_id, request)
    generator = run_coach(req.session_id, req.content)
    return StreamingResponse(generator, media_type="text/event-stream")
