from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.deps import require_session_owner
from core.interviewer import run_interview_respond, run_interview_start

router = APIRouter(prefix="/api/interview", tags=["interview"])

# 允许开始面试的会话阶段（B073）。
STARTABLE_PHASES = ("define", "coach", "brainstorm")


class InterviewStartRequest(BaseModel):
    session_id: str


class InterviewRespondRequest(BaseModel):
    session_id: str
    answer: str = Field(min_length=1)


@router.post("/start")
async def interview_start(req: InterviewStartRequest, request: Request):
    session = await require_session_owner(req.session_id, request)
    if session.get("phase") not in STARTABLE_PHASES:
        raise HTTPException(status_code=409, detail="当前阶段无法开始面试")
    return StreamingResponse(run_interview_start(req.session_id), media_type="text/event-stream")


@router.post("/respond")
async def interview_respond(req: InterviewRespondRequest, request: Request):
    answer = req.answer.strip()
    if not answer:
        raise HTTPException(status_code=400, detail="回答内容不能为空")
    session = await require_session_owner(req.session_id, request)
    if session.get("phase") != "interview":
        raise HTTPException(status_code=409, detail="当前会话不在面试阶段")
    return StreamingResponse(
        run_interview_respond(req.session_id, answer), media_type="text/event-stream"
    )
