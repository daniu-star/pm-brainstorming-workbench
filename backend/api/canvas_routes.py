from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from core.canvas_parser import parse_conversation_to_map, parse_incremental_map
from db.session_store import session_store
from db.user_store import user_store
from api.deps import get_current_user, get_user_llm_config, check_quota

router = APIRouter(prefix="/api/canvas", tags=["canvas"])


class GenerateCanvasRequest(BaseModel):
    session_id: str


class UpdateCanvasRequest(BaseModel):
    session_id: str
    tree: dict


@router.post("/generate")
async def generate_canvas(req: GenerateCanvasRequest, request: Request):
    user = get_current_user(request)
    llm_config = get_user_llm_config(request)
    check_quota(user, llm_config)

    session = session_store.get(req.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话未找到")

    messages = session.get("messages", [])
    if not messages:
        raise HTTPException(status_code=400, detail="没有消息可供解析")

    tree, tokens = await parse_conversation_to_map(messages, **llm_config)
    session_store.update(req.session_id, {"discussion_map": tree})

    if tokens > 0 and not llm_config["api_key"]:
        user_store.deduct_tokens(user["user_token"], tokens)

    return tree


@router.post("/incremental")
async def incremental_canvas(req: GenerateCanvasRequest, request: Request):
    user = get_current_user(request)
    llm_config = get_user_llm_config(request)
    check_quota(user, llm_config)

    session = session_store.get(req.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话未找到")

    messages = session.get("messages", [])
    existing = session.get("discussion_map")

    tree, tokens = await parse_incremental_map(messages, existing, **llm_config)
    if tree:
        session_store.update(req.session_id, {"discussion_map": tree})

    if tokens > 0 and not llm_config["api_key"]:
        user_store.deduct_tokens(user["user_token"], tokens)

    return tree or existing or {}


@router.get("/{session_id}")
async def get_canvas(session_id: str, request: Request):
    user = get_current_user(request)
    session = session_store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话未找到")
    return session.get("discussion_map") or {"topic": "", "timeline": []}


@router.put("/{session_id}")
async def update_canvas(session_id: str, req: UpdateCanvasRequest, request: Request):
    user = get_current_user(request)
    session = session_store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话未找到")
    session_store.update(session_id, {"discussion_map": req.tree})
    return {"status": "updated"}
