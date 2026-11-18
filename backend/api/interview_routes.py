"""旧面试路由（/api/interview）→ 审计命令 + SSE 翻译（PRD §7.3）。

* ``/start`` → ``start_audit``，``/respond`` → ``answer_audit``；
* 服务返回 ``FEATURE_NOT_READY`` 时统一 503（审计工作流尚未就绪时的既定行为）；
* 事件翻译沿用 v1 事件名（phase_change / interview_start / token / role_done /
  dimensions_update / error / done），``done`` 恰好一次。
"""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from api import sse as sse_module
from api.brainstorm_routes import (
    LegacyStreamState,
    _sse_response,
    translate_event,
)
from api.v2 import common
from core.sse import sse_event
from domain import contracts as C
from domain.errors import AppError, ErrorCode

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/interview", tags=["interview"])

#: v1 允许开始面试的旧 phase 值（映射到 v2：clarifying / brainstorming 均可开始）。
STARTABLE_PHASES = ("define", "coach", "brainstorm")


class InterviewStartRequest(BaseModel):
    session_id: str


class InterviewRespondRequest(BaseModel):
    session_id: str
    answer: str = Field(min_length=1)


def _submit_start(session_id: str, owner_email: str, *, expected_product_version=None):
    command = C.CommandRequest(
        request_id=C.new_request_id(),
        action=C.Action.start_audit,
        expected_session_version=None,
        payload={"expected_product_version": expected_product_version},
    )
    with common.open_db() as db:
        receipt = common.service("command_service").submit_command(
            db, owner_email=owner_email, session_id=session_id, command=command
        )
        session = common.repo("session_repo").get_session(db, session_id)
        phase_label = common.legacy_phase(session.phase) if session is not None else "interview"
    run_id = getattr(receipt, "run_id", None)
    if not run_id:
        # StartAuditRecovery：已有审计在跑，直接返回当前审计快照供旧前端展示。
        raise AppError(
            ErrorCode.AUDIT_ACTIVE,
            "已有审计任务在进行中",
            http_status=409,
            details={"audit_id": getattr(receipt, "audit_id", None)},
        )
    return str(run_id), phase_label


def _answer_context(db, session) -> tuple[str, str]:
    audit_repo = common.repo("audit_repo")
    audit = audit_repo.get_audit(db, session.current_audit_id) if session.current_audit_id else None
    if audit is None:
        audit = audit_repo.latest_audit_for_session(db, session.id)
    if audit is None:
        raise AppError(ErrorCode.FEATURE_NOT_READY, "该会话还没有进行中的审计")
    question_id = audit.active_question_id
    if not question_id:
        pending = [
            row
            for row in audit_repo.questions_for_audit(db, audit.id)
            if not row.answer_message_id
        ]
        if pending:
            question_id = pending[0].id
    if not question_id:
        raise AppError(
            ErrorCode.QUESTION_NOT_ACTIVE, "当前没有待回答的问题", http_status=409
        )
    return audit.id, question_id


def _submit_answer(session_id: str, owner_email: str, answer: str):
    with common.open_db() as db:
        session = common.repo("session_repo").require_session(
            db, session_id, owner_email=owner_email
        )
        if session.phase != C.SessionPhase.auditing.value:
            raise AppError(ErrorCode.AUDIT_CLOSED, "当前会话不在面试阶段", http_status=409)
        audit_id, question_id = _answer_context(db, session)
        command = C.CommandRequest(
            request_id=C.new_request_id(),
            action=C.Action.answer_audit,
            expected_session_version=None,
            payload={
                "audit_id": audit_id,
                "question_id": question_id,
                "answer": answer,
            },
        )
        receipt = common.service("command_service").submit_command(
            db, owner_email=owner_email, session_id=session_id, command=command
        )
        session = common.repo("session_repo").get_session(db, session_id)
        phase_label = common.legacy_phase(session.phase) if session is not None else "interview"
    run_id = getattr(receipt, "run_id", None)
    if not run_id:
        raise AppError(ErrorCode.INTERNAL, "命令未产生可订阅的任务")
    return str(run_id), phase_label


def _dimension_counts(payload: dict) -> tuple[int, int] | None:
    covered = payload.get("covered")
    total = payload.get("total")
    if covered is not None and total is not None:
        return int(covered), int(total)
    states = payload.get("dimension_states")
    if isinstance(states, list) and states:
        total = len(states)
        covered = sum(
            1
            for state in states
            if isinstance(state, dict) and state.get("assessment") not in (None, "not_discussed")
        )
        return covered, total
    return None


async def _interview_stream(run_id: str, phase_label: str, *, announce_start: bool) -> AsyncIterator[str]:
    state = LegacyStreamState()
    if announce_start:
        yield sse_event("interview_start")
    async for event in sse_module.iter_run_events(
        run_id=run_id, after_seq=0, keepalive_seconds=0.0
    ):
        payload = event.payload or {}
        event_type = event.type.value if hasattr(event.type, "value") else str(event.type)
        if event_type == C.EventType.audit_updated.value:
            counts = _dimension_counts(payload)
            if counts is not None:
                yield sse_event("dimensions_update", covered=counts[0], total=counts[1])
            continue
        if event_type == C.EventType.run_completed.value:
            counts = _dimension_counts(payload)
            if counts is not None:
                yield sse_event("dimensions_update", covered=counts[0], total=counts[1])
        for frame in translate_event(event, state):
            yield frame
        if state.done:
            return
    if not state.done:
        logger.info("旧面试 SSE 订阅提前结束，补发 done：run=%s", run_id)
        yield sse_event("done")


async def _start(req: InterviewStartRequest, request: Request):
    owner_email = common.require_user(request)
    try:
        run_id, phase_label = await asyncio.to_thread(
            _submit_start, req.session_id, owner_email
        )
    except AppError as exc:
        if exc.code == ErrorCode.FEATURE_NOT_READY:
            raise
        if exc.code in {
            ErrorCode.INVALID_REQUEST,
            ErrorCode.AUDIT_ACTIVE,
            ErrorCode.AUDIT_CLOSED,
        }:
            # 保留旧接口的 409 语义（旧前端按 409 提示「当前阶段无法开始面试」）。
            raise AppError(
                ErrorCode.AUDIT_ACTIVE,
                "当前阶段无法开始面试",
                http_status=409,
                details=exc.details,
            ) from exc
        raise
    return _sse_response(
        _interview_stream(run_id, phase_label, announce_start=True)
    )


@router.post("/start")
async def interview_start(req: InterviewStartRequest, request: Request):
    return await _start(req, request)


@router.post("/respond")
async def interview_respond(req: InterviewRespondRequest, request: Request):
    answer = req.answer.strip()
    if not answer:
        raise AppError(ErrorCode.INVALID_REQUEST, "回答内容不能为空", http_status=400)
    owner_email = common.require_user(request)
    run_id, phase_label = await asyncio.to_thread(
        _submit_answer, req.session_id, owner_email, answer
    )
    return _sse_response(_interview_stream(run_id, phase_label, announce_start=False))