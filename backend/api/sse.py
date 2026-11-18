"""SSE（PRD FR-06）：事件序列化、心跳、游标校验、终结关闭。

该模块只负责「把 RunEvent 变成 SSE 帧」与「轮询回放」，**不修改 Run 状态**：
客户端断开时生成器被取消，服务器上的任务继续执行。

帧格式::

    id: <event_seq>
    event: <type>
    data: <RunEvent.to_sse_data()>

    : keepalive          <!-- 每 15s，一条注释，不占 event_seq -->

游标规则：``Last-Event-ID`` 与 ``?after_seq=`` 同时存在取较大值；省略游标等价于
``after_seq=0``（从头回放，含 seq=1）；非整数 / 负数 → 422 ``INVALID_EVENT_CURSOR``；
早于保留水位（``after_seq < RETENTION_WATERMARK - 1``）→ 410 ``EVENT_CURSOR_EXPIRED``
（M1 不压缩事件、水位为 0，因此正常不会触发）；大于当前最新 seq → 422。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import AsyncIterator

from fastapi import Request
from fastapi.responses import StreamingResponse

from domain.contracts import TERMINAL_EVENT_TYPES, TERMINAL_RUN_STATUSES, RunEvent
from domain.errors import AppError, ErrorCode

logger = logging.getLogger(__name__)

#: 心跳间隔（秒）。
KEEPALIVE_SECONDS = 15.0
#: 空闲轮询间隔（秒）——事件到达后最迟这么久被推送。
POLL_INTERVAL_SECONDS = 0.25
#: 单次从库里取的事件条数上限。
EVENT_BATCH_LIMIT = 200
#: 已压缩（不可回放）的事件水位：``event_seq <= RETENTION_WATERMARK`` 视为已过期。
#: M1 不压缩历史事件，恒为 0 —— 从 seq=1 起都能回放，``after_seq=0`` 合法。
RETENTION_WATERMARK = 0

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def format_event(event: RunEvent) -> str:
    """一条完整 SSE 事件（id/event/data + 空行）。"""

    return (
        f"id: {event.event_seq}\n"
        f"event: {event.type}\n"
        f"data: {event.to_sse_data()}\n\n"
    )


def format_keepalive() -> str:
    """心跳注释帧，不占用序号。"""

    return ": keepalive\n\n"


def _parse_cursor(raw: object, *, source: str) -> int:
    if isinstance(raw, bool):  # bool 是 int 子类，单独拒绝
        raise AppError(
            ErrorCode.INVALID_EVENT_CURSOR,
            "事件游标必须是整数",
            details={"source": source, "value": str(raw)},
        )
    if isinstance(raw, int):
        value = raw
    else:
        text = str(raw).strip()
        if not text:
            raise AppError(
                ErrorCode.INVALID_EVENT_CURSOR,
                "事件游标不能为空",
                details={"source": source},
            )
        try:
            value = int(text, 10)
        except ValueError as exc:
            raise AppError(
                ErrorCode.INVALID_EVENT_CURSOR,
                "事件游标必须是整数",
                details={"source": source, "value": text},
            ) from exc
    if value < 0:
        raise AppError(
            ErrorCode.INVALID_EVENT_CURSOR,
            "事件游标不能为负数",
            details={"source": source, "value": value},
        )
    return value


def resolve_start_cursor(
    request: Request,
    after_seq: object | None,
    *,
    latest_event_seq: int,
) -> int:
    """合并两个游标来源并按 PRD 规则校验；返回 ``after_seq``（含）。"""

    candidates: list[int] = []
    header = request.headers.get("Last-Event-ID")
    if header is not None and header.strip():
        candidates.append(_parse_cursor(header, source="Last-Event-ID"))
    if after_seq is not None and str(after_seq).strip() != "":
        candidates.append(_parse_cursor(after_seq, source="after_seq"))

    cursor = max(candidates) if candidates else 0
    # 游标过期：水位之上的第一个可回放 seq 是 watermark+1，因此只允许
    # after_seq >= watermark（即 after_seq < watermark - 1 才过期）。
    # M1 水位为 0，任何合法游标都不会过期（显式 after_seq=0 = 从头回放）。
    if cursor < RETENTION_WATERMARK - 1:
        raise AppError(
            ErrorCode.EVENT_CURSOR_EXPIRED,
            "事件游标已过期，请重新获取任务快照",
            details={"after_seq": cursor, "retention_watermark": RETENTION_WATERMARK},
        )
    if cursor > latest_event_seq:
        raise AppError(
            ErrorCode.INVALID_EVENT_CURSOR,
            "事件游标超过任务最新序号",
            details={"after_seq": cursor, "latest_event_seq": latest_event_seq},
        )
    return cursor


def _poll_once(run_id: str, cursor: int) -> tuple[list[RunEvent], bool, bool]:
    """同步读取一批事件；返回 (事件, 任务是否已终结, 任务是否不存在)。"""

    from api.v2.common import open_db, repo, run_event

    run_repo = repo("run_repo")
    with open_db() as db:
        run = run_repo.get_run(db, run_id)
        if run is None:
            return [], True, True
        rows = run_repo.events_after(db, run_id, after_seq=cursor, limit=EVENT_BATCH_LIMIT)
        events = [run_event(row) for row in sorted(rows, key=lambda item: item.seq)]
        terminal = run.status in {status.value for status in TERMINAL_RUN_STATUSES}
        return events, terminal, False


async def iter_run_events(
    *,
    run_id: str,
    after_seq: int,
    keepalive_seconds: float = KEEPALIVE_SECONDS,
    poll_interval: float = POLL_INTERVAL_SECONDS,
) -> AsyncIterator[RunEvent | None]:
    """按 seq 回放并持续订阅；``None`` 表示「该发心跳了」。

    收到终结事件后立即结束迭代——终结事件每次订阅**至多一次**，
    重连时若游标已追平终结事件则直接关闭（不重复、不丢）。
    """

    cursor = max(0, after_seq)
    last_sent = time.monotonic()
    try:
        while True:
            events, terminal, missing = await asyncio.to_thread(_poll_once, run_id, cursor)
            if missing:
                logger.info("SSE 订阅的 run 不存在，关闭连接：%s", run_id)
                return
            for event in events:
                if event.event_seq <= cursor:
                    continue
                yield event
                cursor = event.event_seq
                last_sent = time.monotonic()
                if event.type in TERMINAL_EVENT_TYPES:
                    return
            if not events and terminal:
                # 任务已终结且没有更多事件：终结事件已在此前发出／或已被清理。
                return
            if keepalive_seconds and time.monotonic() - last_sent >= keepalive_seconds:
                yield None
                last_sent = time.monotonic()
            await asyncio.sleep(poll_interval)
    except asyncio.CancelledError:
        # 客户端断开：只停止订阅，不取消服务器 Run。
        logger.info("SSE 客户端断开：run=%s after_seq=%s", run_id, cursor)
        return


async def stream_run_events(
    *,
    run_id: str,
    after_seq: int,
    keepalive_seconds: float = KEEPALIVE_SECONDS,
    poll_interval: float = POLL_INTERVAL_SECONDS,
) -> AsyncIterator[str]:
    """SSE 字节流：事件帧 + 心跳注释帧。"""

    async for item in iter_run_events(
        run_id=run_id,
        after_seq=after_seq,
        keepalive_seconds=keepalive_seconds,
        poll_interval=poll_interval,
    ):
        yield format_keepalive() if item is None else format_event(item)


def sse_response(events: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(
        events,
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


__all__ = [
    "EVENT_BATCH_LIMIT",
    "KEEPALIVE_SECONDS",
    "POLL_INTERVAL_SECONDS",
    "RETENTION_WATERMARK",
    "SSE_HEADERS",
    "format_event",
    "format_keepalive",
    "iter_run_events",
    "resolve_start_cursor",
    "sse_response",
    "stream_run_events",
]