from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from core.canvas_parser import parse_conversation_to_map, parse_incremental_map
from db.session_store import session_store
from db.user_store import user_store
from api.deps import get_current_user, get_user_llm_config, check_quota

router = APIRouter(prefix="/api/canvas", tags=["canvas"])


class GenerateCanvasRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)


class UpdateCanvasRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    tree: dict


class NodeStatusRequest(BaseModel):
    status: str = Field(pattern="^(draft|confirmed)$")


def _owned_session(session_id: str, user_token: str) -> dict:
    session = session_store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话未找到")
    if session.get("user_token") and session.get("user_token") != user_token:
        raise HTTPException(status_code=403, detail="无权访问此会话")
    return session


@router.post("/generate")
async def generate_canvas(req: GenerateCanvasRequest, request: Request):
    user = get_current_user(request)
    llm_config = get_user_llm_config(request)
    check_quota(user, llm_config)

    session = _owned_session(req.session_id, user["user_token"])

    messages = session.get("messages", [])
    if not messages:
        raise HTTPException(status_code=400, detail="没有消息可供解析")

    session_store.update(req.session_id, {"canvas_status": "syncing"})
    try:
        tree, tokens = await parse_conversation_to_map(messages, **llm_config)
        version = int(session.get("canvas_version", 0)) + 1
        tree["version"] = version
        session_store.update(
            req.session_id,
            {
                "discussion_map": tree,
                "canvas_status": "ready",
                "canvas_version": version,
                "canvas_last_event_id": messages[-1].get("id"),
            },
        )
    except Exception:
        session_store.update(req.session_id, {"canvas_status": "error"})
        raise

    if tokens > 0 and not llm_config["api_key"]:
        user_store.deduct_tokens(user["user_token"], tokens)

    return tree


@router.post("/incremental")
async def incremental_canvas(req: GenerateCanvasRequest, request: Request):
    user = get_current_user(request)
    llm_config = get_user_llm_config(request)
    check_quota(user, llm_config)

    session = _owned_session(req.session_id, user["user_token"])

    messages = session.get("messages", [])
    existing = session.get("discussion_map")

    session_store.update(req.session_id, {"canvas_status": "syncing"})
    try:
        tree, tokens = await parse_incremental_map(messages, existing, **llm_config)
        if tree:
            version = int(session.get("canvas_version", 0)) + 1
            tree["version"] = version
            session_store.update(
                req.session_id,
                {
                    "discussion_map": tree,
                    "canvas_status": "ready",
                    "canvas_version": version,
                    "canvas_last_event_id": messages[-1].get("id") if messages else None,
                },
            )
        else:
            session_store.update(req.session_id, {"canvas_status": "stale"})
    except Exception:
        session_store.update(req.session_id, {"canvas_status": "error"})
        raise

    if tokens > 0 and not llm_config["api_key"]:
        user_store.deduct_tokens(user["user_token"], tokens)

    return tree or existing or {}


@router.get("/{session_id}")
async def get_canvas(session_id: str, request: Request):
    user = get_current_user(request)
    session = _owned_session(session_id, user["user_token"])
    return session.get("discussion_map") or {"topic": "", "timeline": []}


@router.put("/{session_id}")
async def update_canvas(session_id: str, req: UpdateCanvasRequest, request: Request):
    user = get_current_user(request)
    session = _owned_session(session_id, user["user_token"])
    session_store.update(session_id, {"discussion_map": req.tree})
    return {"status": "updated"}


@router.patch("/{session_id}/nodes/{node_id}")
async def update_canvas_node_status(
    session_id: str,
    node_id: str,
    req: NodeStatusRequest,
    request: Request,
):
    user = get_current_user(request)
    session = _owned_session(session_id, user["user_token"])
    graph = session.get("discussion_map") or {}
    timeline = graph.get("timeline", [])
    node = next((item for item in timeline if item.get("id") == node_id), None)
    if node is None:
        raise HTTPException(status_code=404, detail="决策节点未找到")
    node["status"] = req.status
    version = int(session.get("canvas_version", 0)) + 1
    graph["version"] = version
    session_store.update(
        session_id,
        {
            "discussion_map": graph,
            "canvas_version": version,
            "canvas_status": "ready",
        },
    )
    return graph
