"""旧脑暴路由（/api/brainstorm）→ v2 命令 + SSE 翻译（PRD §7.3）。

* 写路径唯一：``command_service.submit_command``（不再调用 core.agent_loop，那里会写 JSON 存储）；
* 事件翻译：v2 RunEvent → v1 事件名 role_start / token / role_done / error / done；
* ``done`` 恰好发送一次；先落库（提交命令）再开流，因此首帧前消息已可见；
* 客户端断开只停止订阅，不会取消服务器上的 Run。
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api import sse as sse_module
from api.v2 import common
from core.sse import sse_event
from domain import contracts as C
from domain.errors import AppError, ErrorCode

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/brainstorm", tags=["brainstorm"])

#: 旧 target_role → v2 TargetRole。
LEGACY_TARGET = {
    "all": C.TargetRole.all,
    "cto": C.TargetRole.cto,
    "designer": C.TargetRole.designer,
    "ops": C.TargetRole.ops,
    "user": C.TargetRole.target_user,
}

STREAM_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
}


class BrainstormMessage(BaseModel):
    session_id: str
    content: str
    target_role: str  # "cto" | "designer" | "ops" | "user" | "all"


class CoachRequest(BaseModel):
    session_id: str
    content: str


# --------------------------------------------------------------------------- #
# v2 事件 → v1 事件
# --------------------------------------------------------------------------- #


@dataclass
class LegacyStreamState:
    done: bool = False
    started: set[str] = field(default_factory=set)
    texts: dict[str, str] = field(default_factory=dict)


def _role_of(payload: dict) -> str | None:
    for key in ("role_code", "role", "target_role"):
        value = payload.get(key)
        if value:
            return str(value)
    return None


def _role_start(role: str) -> str:
    return sse_event(
        "role_start",
        role=common.legacy_role(role),
        role_name=common.legacy_role_name(role),
    )


def _text_delta(payload: dict, state: LegacyStreamState, role: str) -> str:
    previous = state.texts.get(role, "")
    for key in ("delta", "token", "text_delta"):
        if payload.get(key) is not None:
            chunk = str(payload[key])
            state.texts[role] = previous + chunk
            return chunk
    full = payload.get("text", payload.get("content"))
    if full is None:
        return ""
    full = str(full)
    chunk = full[len(previous):] if full.startswith(previous) else full
    state.texts[role] = full
    return chunk


def translate_event(event: C.RunEvent, state: LegacyStreamState) -> list[str]:
    """把一条 RunEvent 翻译成若干条 v1 SSE 帧（纯函数，便于测试）。"""

    if state.done:
        return []
    payload = event.payload or {}
    event_type = event.type.value if hasattr(event.type, "value") else str(event.type)
    frames: list[str] = []

    if event_type == C.EventType.step_started.value:
        role = _role_of(payload)
        if role and role not in state.started:
            state.started.add(role)
            frames.append(_role_start(role))

    elif event_type == C.EventType.role_output_updated.value:
        role = _role_of(payload) or str(C.RoleCode.coach.value)
        if role not in state.started:
            state.started.add(role)
            frames.append(_role_start(role))
        chunk = _text_delta(payload, state, role)
        if chunk:
            frames.append(
                sse_event("token", role=common.legacy_role(role), token=chunk)
            )

    elif event_type == C.EventType.role_completed.value:
        role = _role_of(payload) or str(C.RoleCode.coach.value)
        if role not in state.started:
            state.started.add(role)
            frames.append(_role_start(role))
        content = payload.get("content")
        if content is None:
            content = state.texts.get(role, "")
        content = str(content)
        frames.append(
            sse_event(
                "role_done",
                role=common.legacy_role(role),
                role_name=common.legacy_role_name(role),
                content=content,
            )
        )

    elif event_type == C.EventType.run_completed.value:
        state.done = True
        frames.append(sse_event("done"))

    elif event_type == C.EventType.run_failed.value:
        message = (
            payload.get("error_message")
            or payload.get("message")
            or "任务执行失败，请稍后重试"
        )
        frames.append(sse_event("error", message=str(message)))
        state.done = True
        frames.append(sse_event("done"))

    elif event_type == C.EventType.run_cancelled.value:
        state.done = True
        frames.append(sse_event("done"))

    # run.queued / run.started / product_state.updated / audit.updated / run.recovered
    # 在 v1 协议里没有对应事件，直接忽略。
    return frames


async def legacy_stream(run_id: str, phase_label: str) -> AsyncIterator[str]:
    """先发 phase_change，再翻译事件；确保 done 恰好一次。"""

    state = LegacyStreamState()
    yield sse_event("phase_change", phase=phase_label)
    async for event in sse_module.iter_run_events(
        run_id=run_id, after_seq=0, keepalive_seconds=0.0
    ):
        for frame in translate_event(event, state):
            yield frame
        if state.done:
            return
    if not state.done:
        # 订阅意外结束（例如 Run 被清理）：仍补一次 done，避免旧前端挂死。
        logger.info("旧 SSE 订阅提前结束，补发 done：run=%s", run_id)
        yield sse_event("done")


def _submit(action: C.Action, session_id: str, payload: dict, owner_email: str):
    """同步提交命令并返回 (run_id, 旧 phase 值)。"""

    command = C.CommandRequest(
        request_id=C.new_request_id(),
        action=action,
        expected_session_version=None,
        payload=payload,
    )
    with common.open_db() as db:
        receipt = common.service("command_service").submit_command(
            db, owner_email=owner_email, session_id=session_id, command=command
        )
        session = common.repo("session_repo").get_session(db, session_id)
        phase_label = common.legacy_phase(session.phase) if session is not None else "coach"
    run_id = getattr(receipt, "run_id", None)
    if not run_id:
        raise AppError(ErrorCode.INTERNAL, "命令未产生可订阅的任务")
    return str(run_id), phase_label


def _sse_response(generator: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(generator, media_type="text/event-stream", headers=STREAM_HEADERS)


@router.post("/message")
async def brainstorm_message(req: BrainstormMessage, request: Request):
    owner_email = common.require_user(request)
    target = LEGACY_TARGET.get(req.target_role)
    if target is None:
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            f"无效角色: {req.target_role}",
            details={"target_role": req.target_role},
        )
    payload = C.BrainstormPayload(content=req.content, target=target).model_dump(mode="json")
    run_id, phase_label = await asyncio.to_thread(
        _submit, C.Action.brainstorm, req.session_id, payload, owner_email
    )
    return _sse_response(legacy_stream(run_id, phase_label))


@router.post("/coach")
async def coach_clarify(req: CoachRequest, request: Request):
    owner_email = common.require_user(request)
    payload = C.ClarifyPayload(content=req.content).model_dump(mode="json")
    run_id, phase_label = await asyncio.to_thread(
        _submit, C.Action.clarify, req.session_id, payload, owner_email
    )
    return _sse_response(legacy_stream(run_id, phase_label))