import re
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from core.agent_loop import run_agent_turn, run_ask_all, run_coach
from core.pipeline.runner import run_pipeline
from db.session_store import session_store
from db.user_store import user_store
from rag.retriever import rag_retriever
from api.deps import get_current_user, get_user_llm_config, check_quota

router = APIRouter(prefix="/api/brainstorm", tags=["brainstorm"])


class BrainstormMessage(BaseModel):
    session_id: str
    content: str
    target_role: str


class PipelineRequest(BaseModel):
    session_id: str


@router.post("/message")
async def brainstorm_message(req: BrainstormMessage, request: Request):
    user = get_current_user(request)
    llm_config = get_user_llm_config(request)
    check_quota(user, llm_config)

    session = session_store.get(req.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话未找到")

    rag_context = ""
    if not rag_retriever.is_empty():
        chunks = await rag_retriever.search(req.content, n_results=4)
        if chunks:
            rag_context = "\n\n".join(f"> {c[:300]}" for c in chunks)

    if req.target_role == "all":
        generator = run_ask_all(req.session_id, req.content, rag_context, **llm_config)
    elif req.target_role in ("cto", "designer", "ops", "user"):
        generator = run_agent_turn(req.session_id, req.content, req.target_role, rag_context, **llm_config)
    else:
        raise HTTPException(status_code=400, detail=f"无效角色: {req.target_role}")

    async def streaming_with_deduction():
        total_tokens = 0
        async for event in generator:
            yield event
            if "quota_deduct" in event:
                try:
                    match = re.search(r'"tokens":\s*(\d+)', event)
                    if match:
                        total_tokens += int(match.group(1))
                except:
                    pass
        if total_tokens > 0 and not llm_config["api_key"]:
            user_store.deduct_tokens(user["user_token"], total_tokens)

    return StreamingResponse(streaming_with_deduction(), media_type="text/event-stream")


class CoachRequest(BaseModel):
    session_id: str
    content: str


@router.post("/coach")
async def coach_clarify(req: CoachRequest, request: Request):
    user = get_current_user(request)
    llm_config = get_user_llm_config(request)
    check_quota(user, llm_config)

    session = session_store.get(req.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话未找到")

    generator = run_coach(req.session_id, req.content, **llm_config)

    async def streaming_with_deduction():
        total_tokens = 0
        async for event in generator:
            yield event
            if "quota_deduct" in event:
                try:
                    match = re.search(r'"tokens":\s*(\d+)', event)
                    if match:
                        total_tokens += int(match.group(1))
                except:
                    pass
        if total_tokens > 0 and not llm_config["api_key"]:
            user_store.deduct_tokens(user["user_token"], total_tokens)

    return StreamingResponse(streaming_with_deduction(), media_type="text/event-stream")


@router.post("/pipeline")
async def run_brainstorm_pipeline(req: PipelineRequest, request: Request):
    """运行 LangGraph Pipeline：PM写PRD → CoT → 教练 → CTO → 设计师 → 运营 → 用户 → 画布 → 画像 → PM验收。"""
    user = get_current_user(request)
    llm_config = get_user_llm_config(request)
    check_quota(user, llm_config)

    session = session_store.get(req.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话未找到")

    problem = session.get("problem_statement", "")
    if not problem.strip():
        raise HTTPException(status_code=400, detail="会话缺少问题描述，无法启动 Pipeline")

    generator = run_pipeline(
        session_id=req.session_id,
        problem_statement=problem,
        api_key=llm_config.get("api_key", ""),
        base_url=llm_config.get("base_url", ""),
        model=llm_config.get("model", ""),
    )

    async def streaming_with_deduction():
        total_tokens = 0
        async for event in generator:
            yield event
            if "quota_deduct" in event:
                try:
                    match = re.search(r'"tokens":\s*(\d+)', event)
                    if match:
                        total_tokens += int(match.group(1))
                except:
                    pass
        if total_tokens > 0 and not llm_config["api_key"]:
            user_store.deduct_tokens(user["user_token"], total_tokens)

    return StreamingResponse(streaming_with_deduction(), media_type="text/event-stream")
