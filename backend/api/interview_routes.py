import re
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from core.interviewer import (
    run_interview_start,
    run_interview_respond,
    run_interview_start_space,
    run_interview_respond_space,
)
from db.session_store import session_store, interview_session_store
from db.user_store import user_store
from api.deps import get_current_user, get_user_llm_config, check_quota
from rag.retriever import rag_retriever

router = APIRouter(prefix="/api/interview", tags=["interview"])


async def _rag_context(query: str) -> str:
    if rag_retriever.is_empty():
        return ""
    chunks = await rag_retriever.search(query, n_results=4)
    return "\n\n".join(f"> {chunk[:300]}" for chunk in chunks)


def _governance_context(session: dict | None) -> str:
    config = (session or {}).get("decision_hub", {}).get("agent_config", {})
    knowledge = str(config.get("company_knowledge", "")).strip()
    rules = [str(item).strip() for item in config.get("audit_rules", []) if str(item).strip()]
    if not knowledge and not rules:
        return ""
    return (
        "\n\n用户配置的公司上下文：\n"
        + (knowledge or "未配置")
        + "\n用户配置的审计规则：\n"
        + ("\n".join(f"- {rule}" for rule in rules) or "- 未配置")
    )


class InterviewStartRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)


class InterviewRespondRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    answer: str = Field(min_length=1, max_length=8000)


class CreateSpaceRequest(BaseModel):
    parent_session_id: str = Field(min_length=1, max_length=64)
    restart: bool = False


class SpaceRespondRequest(BaseModel):
    answer: str = Field(min_length=1, max_length=8000)


@router.post("/start")
async def interview_start(req: InterviewStartRequest, request: Request):
    user = get_current_user(request)
    llm_config = get_user_llm_config(request)
    check_quota(user, llm_config)

    session = session_store.get(req.session_id, user_token=user["user_token"])
    if session is None:
        raise HTTPException(status_code=404, detail="会话未找到")

    generator = run_interview_start(req.session_id, **llm_config)

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


@router.post("/respond")
async def interview_respond(req: InterviewRespondRequest, request: Request):
    user = get_current_user(request)
    llm_config = get_user_llm_config(request)
    check_quota(user, llm_config)

    session = session_store.get(req.session_id, user_token=user["user_token"])
    if session is None:
        raise HTTPException(status_code=404, detail="会话未找到")

    generator = run_interview_respond(req.session_id, req.answer, **llm_config)

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


# ==================== 独立面试空间 API ====================


@router.post("/create-space")
async def create_interview_space(req: CreateSpaceRequest, request: Request):
    """创建独立面试空间，从父会话继承 problem_statement 和 canvas_tree。"""
    user = get_current_user(request)

    parent = session_store.get(req.parent_session_id, user_token=user["user_token"])
    if parent is None:
        raise HTTPException(status_code=404, detail="父会话未找到")

    existing = interview_session_store.get_latest_by_parent(
        req.parent_session_id,
        user_token=user["user_token"],
    )
    if existing and not req.restart:
        space = existing
    else:
        if existing and req.restart and existing.get("status") in ("not_started", "active"):
            interview_session_store.update(existing["id"], {"status": "superseded"})
        space = interview_session_store.create(
            parent_session_id=req.parent_session_id,
            problem_statement=parent.get("problem_statement", ""),
            canvas_tree=parent.get("discussion_map") or parent.get("canvas_tree"),
            user_token=user.get("user_token", ""),
        )
    return {
        "interview_id": space["id"],
        "parent_session_id": space["parent_session_id"],
        "status": space["status"],
        "messages": space.get("messages", []),
        "dimensions_covered": space.get("dimensions_covered", []),
        "current_dimension": space.get("current_dimension"),
        "question_count": space.get("question_count", 0),
        "report": space.get("report"),
        "resumed": bool(existing and not req.restart),
        "created_at": space["created_at"],
    }


@router.get("/space/{interview_id}")
async def get_interview_space(interview_id: str, request: Request):
    """获取面试空间完整状态。"""
    user = get_current_user(request)
    space = interview_session_store.get(interview_id, user_token=user.get("user_token"))
    if space is None:
        raise HTTPException(status_code=404, detail="面试空间未找到")
    return {
        "id": space["id"],
        "parent_session_id": space.get("parent_session_id"),
        "problem_statement": space.get("problem_statement", ""),
        "messages": space.get("messages", []),
        "dimensions_covered": space.get("dimensions_covered", []),
        "question_count": space.get("question_count", 0),
        "current_dimension": space.get("current_dimension"),
        "status": space.get("status", "not_started"),
        "report": space.get("report"),
        "created_at": space.get("created_at", ""),
    }


@router.post("/space/{interview_id}/start")
async def start_interview_space(interview_id: str, request: Request):
    """启动面试空间的第一轮提问（SSE 流）。"""
    user = get_current_user(request)
    llm_config = get_user_llm_config(request)
    check_quota(user, llm_config)

    space = interview_session_store.get(interview_id, user_token=user.get("user_token"))
    if space is None:
        raise HTTPException(status_code=404, detail="面试空间未找到")

    parent = session_store.get(
        space.get("parent_session_id", ""),
        user_token=user["user_token"],
    )
    generator = run_interview_start_space(
        interview_id,
        rag_context=(
            await _rag_context(space.get("problem_statement", ""))
            + _governance_context(parent)
        ),
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


@router.post("/space/{interview_id}/respond")
async def respond_interview_space(interview_id: str, req: SpaceRespondRequest, request: Request):
    """面试空间的后续问答（SSE 流）。"""
    user = get_current_user(request)
    llm_config = get_user_llm_config(request)
    check_quota(user, llm_config)

    space = interview_session_store.get(interview_id, user_token=user.get("user_token"))
    if space is None:
        raise HTTPException(status_code=404, detail="面试空间未找到")

    parent = session_store.get(
        space.get("parent_session_id", ""),
        user_token=user["user_token"],
    )
    generator = run_interview_respond_space(
        interview_id,
        req.answer,
        rag_context=await _rag_context(req.answer) + _governance_context(parent),
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
