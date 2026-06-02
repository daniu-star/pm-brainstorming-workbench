import re
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from core.interviewer import run_interview_start, run_interview_respond
from db.session_store import session_store
from db.user_store import user_store
from api.deps import get_current_user, get_user_llm_config, check_quota

router = APIRouter(prefix="/api/interview", tags=["interview"])


class InterviewStartRequest(BaseModel):
    session_id: str


class InterviewRespondRequest(BaseModel):
    session_id: str
    answer: str


@router.post("/start")
async def interview_start(req: InterviewStartRequest, request: Request):
    user = get_current_user(request)
    llm_config = get_user_llm_config(request)
    check_quota(user, llm_config)

    session = session_store.get(req.session_id)
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
                except:
                    pass
        if total_tokens > 0 and not llm_config["api_key"]:
            user_store.deduct_tokens(user["user_token"], total_tokens)

    return StreamingResponse(streaming_with_deduction(), media_type="text/event-stream")


@router.post("/respond")
async def interview_respond(req: InterviewRespondRequest, request: Request):
    user = get_current_user(request)
    llm_config = get_user_llm_config(request)
    check_quota(user, llm_config)

    session = session_store.get(req.session_id)
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
                except:
                    pass
        if total_tokens > 0 and not llm_config["api_key"]:
            user_store.deduct_tokens(user["user_token"], total_tokens)

    return StreamingResponse(streaming_with_deduction(), media_type="text/event-stream")
