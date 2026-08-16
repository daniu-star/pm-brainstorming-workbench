import logging
from asyncio import to_thread

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api.deps import require_session_owner
from core.canvas_parser import parse_conversation_to_tree, parse_incremental
from db.session_store import session_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/canvas", tags=["canvas"])


class CanvasLeaf(BaseModel):
    """功能树叶子节点（B072：Pydantic 校验）。"""

    name: str
    source_role: str
    type: str
    source_text: str | None = None


class CanvasBranch(BaseModel):
    name: str
    children: list[CanvasLeaf] = Field(default_factory=list)


class CanvasTree(BaseModel):
    root: str
    branches: list[CanvasBranch] = Field(default_factory=list)


class GenerateCanvasRequest(BaseModel):
    session_id: str


class UpdateCanvasRequest(BaseModel):
    session_id: str
    tree: CanvasTree


@router.post("/generate")
async def generate_canvas(req: GenerateCanvasRequest, request: Request):
    session = await require_session_owner(req.session_id, request)

    messages = session.get("messages", [])
    if not messages:
        raise HTTPException(status_code=400, detail="没有消息可供解析")

    tree = await parse_conversation_to_tree(messages)
    if tree is None:
        # 解析失败不覆盖已有 canvas_tree（B036）。
        raise HTTPException(status_code=502, detail="Canvas 解析失败，请稍后重试")
    await to_thread(session_store.update, req.session_id, {
        "canvas_tree": tree,
        "canvas_last_msg_index": len(messages),
    })
    return tree


@router.post("/incremental")
async def incremental_canvas(req: GenerateCanvasRequest, request: Request):
    session = await require_session_owner(req.session_id, request)

    messages = session.get("messages", [])
    existing = session.get("canvas_tree")
    # 增量游标：只把上次解析之后的新消息传给 LLM（B040）。
    last_idx = int(session.get("canvas_last_msg_index", 0))

    tree = await parse_incremental(messages, existing, last_idx)
    if tree is None:
        raise HTTPException(status_code=502, detail="Canvas 解析失败，请稍后重试")
    await to_thread(session_store.update, req.session_id, {
        "canvas_tree": tree,
        "canvas_last_msg_index": len(messages),
    })
    return tree


@router.get("/{session_id}")
async def get_canvas(session_id: str, request: Request):
    session = await require_session_owner(session_id, request)
    return session.get("canvas_tree") or {"root": "", "branches": []}


@router.put("/{session_id}")
async def update_canvas(session_id: str, req: UpdateCanvasRequest, request: Request):
    await require_session_owner(session_id, request)
    await to_thread(session_store.update, session_id, {"canvas_tree": req.tree.model_dump()})
    return {"status": "updated"}
