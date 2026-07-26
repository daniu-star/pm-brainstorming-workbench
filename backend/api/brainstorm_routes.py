import re
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from core.agent_loop import run_agent_turn, run_ask_all, run_coach
from core.pipeline.runner import run_pipeline
from db.session_store import session_store
from db.user_store import user_store
from rag.retriever import rag_retriever
from api.deps import get_current_user, get_user_llm_config, check_quota

router = APIRouter(prefix="/api/brainstorm", tags=["brainstorm"])


class BrainstormMessage(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    content: str = Field(min_length=1, max_length=12000)
    target_role: str


class PipelineRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)


def _owned_session(session_id: str, user_token: str) -> dict:
    session = session_store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话未找到")
    if session.get("user_token") and session.get("user_token") != user_token:
        raise HTTPException(status_code=403, detail="无权访问此会话")
    return session


async def _rag_context(query: str) -> str:
    if rag_retriever.is_empty():
        return ""
    chunks = await rag_retriever.search(query, n_results=4)
    return "\n\n".join(f"> {chunk[:300]}" for chunk in chunks)


@router.post("/message")
async def brainstorm_message(req: BrainstormMessage, request: Request):
    user = get_current_user(request)
    llm_config = get_user_llm_config(request)
    check_quota(user, llm_config)

    session = _owned_session(req.session_id, user["user_token"])
    if session.get("phase") == "clarify":
        raise HTTPException(status_code=409, detail="请先完成或跳过需求澄清")

    rag_context = await _rag_context(req.content)

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
                except (TypeError, ValueError):
                    pass
        if total_tokens > 0 and not llm_config["api_key"]:
            user_store.deduct_tokens(user["user_token"], total_tokens)

    return StreamingResponse(streaming_with_deduction(), media_type="text/event-stream")


class CoachRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    content: str = Field(min_length=1, max_length=6000)


class CoachSessionRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)


@router.post("/coach")
async def coach_clarify(req: CoachRequest, request: Request):
    user = get_current_user(request)
    llm_config = get_user_llm_config(request)
    check_quota(user, llm_config)

    _owned_session(req.session_id, user["user_token"])

    generator = run_coach(
        req.session_id,
        req.content,
        rag_context=await _rag_context(req.content),
        **llm_config,
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
                except (TypeError, ValueError):
                    pass
        if total_tokens > 0 and not llm_config["api_key"]:
            user_store.deduct_tokens(user["user_token"], total_tokens)

    return StreamingResponse(streaming_with_deduction(), media_type="text/event-stream")


@router.post("/coach/start")
async def coach_start(req: CoachSessionRequest, request: Request):
    user = get_current_user(request)
    llm_config = get_user_llm_config(request)
    check_quota(user, llm_config)
    session = _owned_session(req.session_id, user["user_token"])

    generator = run_coach(
        req.session_id,
        start_only=True,
        rag_context=await _rag_context(session.get("problem_statement", "")),
        **llm_config,
    )

    async def streaming_with_deduction():
        total_tokens = 0
        async for event in generator:
            yield event
            if "quota_deduct" in event:
                match = re.search(r'"tokens":\s*(\d+)', event)
                if match:
                    total_tokens += int(match.group(1))
        if total_tokens > 0 and not llm_config["api_key"]:
            user_store.deduct_tokens(user["user_token"], total_tokens)

    return StreamingResponse(streaming_with_deduction(), media_type="text/event-stream")


@router.post("/coach/skip")
async def coach_skip(req: CoachSessionRequest, request: Request):
    user = get_current_user(request)
    session = _owned_session(req.session_id, user["user_token"])
    state = session.get("clarification_state") or {}
    state["status"] = "skipped"
    state["current_field"] = None
    session_store.update(req.session_id, {"clarification_state": state})
    session_store.transition_phase(req.session_id, "brainstorm", "clarification_skipped")
    return {"phase": "brainstorm", "clarification_state": state}


@router.post("/coach/confirm")
async def coach_confirm(req: CoachSessionRequest, request: Request):
    user = get_current_user(request)
    session = _owned_session(req.session_id, user["user_token"])
    state = session.get("clarification_state") or {}
    if state.get("status") != "awaiting_confirmation":
        raise HTTPException(status_code=409, detail="需求澄清尚未完成")
    state["status"] = "confirmed"
    state["current_field"] = None
    session_store.update(
        req.session_id,
        {
            "clarification_state": state,
            "clarified_brief": state.get("fields", {}),
        },
    )
    session_store.transition_phase(req.session_id, "brainstorm", "clarification_confirmed")
    return {"phase": "brainstorm", "clarification_state": state}


@router.post("/pipeline")
async def run_brainstorm_pipeline(req: PipelineRequest, request: Request):
    """运行 LangGraph Pipeline：PM写PRD → CoT → 教练 → CTO → 设计师 → 运营 → 用户 → 画布 → 画像 → PM验收。"""
    user = get_current_user(request)
    llm_config = get_user_llm_config(request)
    check_quota(user, llm_config)

    session = _owned_session(req.session_id, user["user_token"])
    if session.get("phase") == "clarify":
        raise HTTPException(status_code=409, detail="请先完成或跳过需求澄清")

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
                except (TypeError, ValueError):
                    pass
        if total_tokens > 0 and not llm_config["api_key"]:
            user_store.deduct_tokens(user["user_token"], total_tokens)

    return StreamingResponse(streaming_with_deduction(), media_type="text/event-stream")
