"""v2 任务端点：详情 / 快照 / SSE 事件 / 取消 / 重试失败任务。"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from api import sse
from api.v2 import common
from domain import contracts as C
from domain.errors import AppError, ErrorCode

logger = logging.getLogger(__name__)

router = APIRouter(tags=["v2-runs"])


class CancelBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str | None = None


class RetryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str | None = None
    expected_session_version: int | None = None


def _step_payload(row) -> dict[str, Any]:
    keys = (
        "id",
        "logical_key",
        "kind",
        "role_code",
        "status",
        "attempt_count",
        "error_code",
        "error_message",
        "started_at",
        "finished_at",
    )
    payload = {key: getattr(row, key, None) for key in keys}
    try:
        payload["output"] = json.loads(row.output_json) if row.output_json else None
    except ValueError:  # pragma: no cover - 数据损坏
        payload["output"] = None
    return payload


def _json_column(raw: str | None, fallback):
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except ValueError:  # pragma: no cover - 数据损坏
        return fallback


def _optional_enrichment(fn, *args, **kwargs):
    """可选增强（画布/审计）：能力未就绪时降级为 None，不拖垮快照。"""

    try:
        return fn(*args, **kwargs)
    except AppError as exc:
        if exc.code == ErrorCode.FEATURE_NOT_READY:
            logger.warning("快照增强能力未就绪：%s", exc.message)
            return None
        raise


@router.get("/runs/{run_id}")
def get_run(run_id: str, request: Request):
    owner_email = common.require_user(request)
    run_repo = common.repo("run_repo")
    with common.open_db() as db:
        run = common.require_owned_run(db, run_id, owner_email)
        payload = common.run_view(run).model_dump(mode="json")
        payload["latest_event_seq"] = run_repo.latest_event_seq(db, run.id)
        payload["steps"] = [_step_payload(row) for row in run_repo.list_steps(db, run.id)]
        payload["budget"] = _json_column(run.budget_json, {})
        payload["warnings"] = _json_column(run.warnings_json, [])
    return payload


@router.get("/runs/{run_id}/snapshot")
def get_run_snapshot(run_id: str, request: Request):
    """单事务读取：任务 + 步骤 + 已落库消息 + 产品版本 + 画布 + 审计。"""

    owner_email = common.require_user(request)
    run_repo = common.repo("run_repo")
    session_repo = common.repo("session_repo")
    with common.open_db() as db:
        run = common.require_owned_run(db, run_id, owner_email)
        session = session_repo.require_session(
            db, run.session_id, owner_email=owner_email
        )
        latest_event_seq = run_repo.latest_event_seq(db, run.id)
        steps = [_step_payload(row) for row in run_repo.list_steps(db, run.id)]
        message_rows = run_repo.message_snapshot_for_run(db, run.id)
        messages = [
            common.message_view(row).model_dump(mode="json") for row in message_rows
        ]
        product_state = session_repo.get_product_state(db, run.session_id)
        canvas = _optional_enrichment(common.project_canvas, product_state)
        audit = None
        if session.current_audit_id:
            audit_repo = common.repo("audit_repo")
            audit_row = audit_repo.get_audit(db, session.current_audit_id)
            if audit_row is not None:
                audit = audit_repo.audit_to_snapshot(db, audit_row)
        # Loop Studio：同一事务内把本轮闭环快照一并带回（run.loop_id 为空时不填充）。
        loop = None
        if run.loop_id:
            loop_repo = common.repo("loop_repo")
            loop_row = loop_repo.get_loop(db, run.loop_id)
            if loop_row is not None:
                if loop_row.owner_email != owner_email:
                    raise AppError(ErrorCode.FORBIDDEN, "无权访问该闭环")
                loop = loop_repo.loop_to_snapshot(db, loop_row)
        snapshot = C.RunSnapshot(
            run_id=run.id,
            session_id=run.session_id,
            action=C.Action(run.action),
            status=C.RunStatus(run.status),
            base_session_version=run.base_session_version or 0,
            base_product_version=run.base_product_version or 1,
            result_product_version=run.result_product_version,
            steps=steps,
            messages=messages,
            session_version=session.session_version,
            product_version=product_state.version,
            canvas=canvas,
            audit=audit,
            loop=loop,
            loop_id=run.loop_id,
            snapshot_event_seq=latest_event_seq,
            latest_event_seq=latest_event_seq,
        )
    return snapshot.model_dump(mode="json")


@router.get("/runs/{run_id}/events")
async def stream_run_events(run_id: str, request: Request, after_seq: str | None = None):
    """SSE（FR-06）：`after_seq` 与 `Last-Event-ID` 取较大值，终结事件后关闭。"""

    owner_email = common.require_user(request)

    def _prepare() -> int:
        with common.open_db() as db:
            run = common.require_owned_run(db, run_id, owner_email)
            return common.repo("run_repo").latest_event_seq(db, run.id)

    latest_event_seq = await asyncio.to_thread(_prepare)
    cursor = sse.resolve_start_cursor(request, after_seq, latest_event_seq=latest_event_seq)
    return sse.sse_response(sse.stream_run_events(run_id=run_id, after_seq=cursor))


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, request: Request, body: CancelBody | None = None):
    """取消任务：未终结 → 202；已终结 → 200 返回既有状态（幂等）。"""

    owner_email = common.require_user(request)
    if body is not None:
        common.optional_request_id(request, body.request_id)
    elif common.header_request_id(request):
        common.optional_request_id(request, None)

    now_iso = C.utc_now_iso()
    with common.open_db() as db:
        run = common.require_owned_run(db, run_id, owner_email)
        already_terminal = run.status in {status.value for status in C.TERMINAL_RUN_STATUSES}
        if already_terminal:
            payload = common.run_view(run).model_dump(mode="json")
            return JSONResponse(status_code=200, content=payload)
        view = common.service("command_service").cancel_run(
            db, owner_email=owner_email, run_id=run_id, now_iso=now_iso
        )
    return JSONResponse(status_code=202, content=view.model_dump(mode="json"))


@router.post("/runs/{run_id}/retry-failed", status_code=202)
def retry_failed(run_id: str, body: RetryBody, request: Request):
    owner_email = common.require_user(request)
    request_id = common.resolve_request_id(request, body.request_id)
    if body.expected_session_version is not None and body.expected_session_version < 1:
        raise AppError(ErrorCode.INVALID_REQUEST, "expected_session_version 必须大于等于 1")

    now_iso = C.utc_now_iso()
    now_epoch = int(C.utc_now().timestamp())
    with common.open_db() as db:
        receipt = common.service("command_service").retry_run(
            db,
            owner_email=owner_email,
            run_id=run_id,
            request_id=request_id,
            now_iso=now_iso,
            now_epoch=now_epoch,
        )
    return JSONResponse(status_code=202, content=receipt.model_dump(mode="json"))


__all__ = ["router"]