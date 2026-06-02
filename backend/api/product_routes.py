from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from core.product_generator import generate_product_portrait
from db.session_store import session_store
from db.user_store import user_store
from api.deps import get_current_user, get_user_llm_config, check_quota

router = APIRouter(prefix="/api/product", tags=["product"])


class PortraitRequest(BaseModel):
    session_id: str


@router.post("/portrait")
async def create_portrait(req: PortraitRequest, request: Request):
    user = get_current_user(request)
    llm_config = get_user_llm_config(request)
    check_quota(user, llm_config)

    session = session_store.get(req.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话未找到")

    messages = session.get("messages", [])
    if not messages:
        raise HTTPException(status_code=400, detail="没有消息可供生成产品画像")

    try:
        portrait, tokens = await generate_product_portrait(
            req.session_id,
            api_key=llm_config["api_key"],
            base_url=llm_config["base_url"],
            model=llm_config["model"],
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    session_store.update(req.session_id, {"product_portrait": portrait})

    if tokens > 0 and not llm_config["api_key"]:
        user_store.deduct_tokens(user["user_token"], tokens)

    return portrait


@router.get("/{session_id}")
async def get_portrait(session_id: str, request: Request):
    get_current_user(request)
    session = session_store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话未找到")
    return session.get("product_portrait") or {}
