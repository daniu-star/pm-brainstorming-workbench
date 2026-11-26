"""v2 Loop Studio 端点：闭环列表 / 快照 / 轮次 / 产物（docs/loop-studio-interfaces.md §6）。

端点只做「鉴权 → 仓储读取 → 组装视图」：

* 会话相关的读取先过 :func:`api.v2.common.require_owned_session`（owner + 软删过滤）；
* 闭环归属以 ``loop.owner_email`` 校验，非本人 → 403 ``FORBIDDEN``；
* 错误体统一交给 ``api/errors.py``（``AppError`` → 统一响应），本模块不手写错误响应。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Request

from api.v2 import common
from domain import contracts as C
from domain.errors import AppError, ErrorCode

logger = logging.getLogger(__name__)

router = APIRouter(tags=["v2-loops"])

#: 闭环列表默认 / 最大页大小（契约 §6：默认最新 20 条）。
LOOPS_PAGE_DEFAULT = 20
LOOPS_PAGE_MAX = 50


def _artifact_view(row) -> C.LoopArtifactView:
    """与 ``loop_repo`` 内部的产物视图保持一致（size_bytes 用负载 JSON 字符数）。"""

    return C.LoopArtifactView(
        id=row.id,
        kind=C.LoopArtifactKind(row.kind),
        title=row.title or "",
        size_bytes=len(row.payload_json or ""),
        created_at=row.created_at or "",
    )


def _artifact_payload(row) -> dict[str, Any]:
    """产物负载：只返回 JSON 对象，损坏数据降级为空对象（不回显原始文本）。"""

    try:
        payload = json.loads(row.payload_json or "{}")
    except (TypeError, ValueError):  # pragma: no cover - 数据损坏时降级
        logger.warning("闭环产物负载不是合法 JSON（artifact=%s）", row.id)
        return {}
    return payload if isinstance(payload, dict) else {}


def _owned_loop(db, *, loop_id: str, owner_email: str):
    """闭环 + 所属会话双重归属校验：非本人 403，会话不存在/已软删 404。"""

    loop_repo = common.repo("loop_repo")
    loop = loop_repo.require_loop(db, loop_id=loop_id, owner_email=owner_email)
    common.require_owned_session(db, loop.session_id, owner_email)
    return loop


@router.get("/sessions/{session_id}/loops")
def list_session_loops(session_id: str, request: Request, limit: int | None = None):
    """会话下的闭环快照列表（最新在前）；``next_cursor`` 预留，当前不分页。"""

    owner_email = common.require_user(request)
    page_size = common.clamp_limit(
        limit, default=LOOPS_PAGE_DEFAULT, maximum=LOOPS_PAGE_MAX, field="limit"
    )
    loop_repo = common.repo("loop_repo")
    with common.open_db() as db:
        common.require_session_read(db, session_id, owner_email)
        rows = loop_repo.list_loops(db, session_id, limit=page_size)
        items = [
            loop_repo.loop_to_snapshot(db, row).model_dump(mode="json") for row in rows
        ]
    return {"items": items, "next_cursor": None}


@router.get("/loops/{loop_id}")
def get_loop_snapshot(loop_id: str, request: Request):
    """闭环快照：状态 + 全部轮次 + 五类产物（从最新产物还原）。"""

    owner_email = common.require_user(request)
    loop_repo = common.repo("loop_repo")
    with common.open_db() as db:
        loop = _owned_loop(db, loop_id=loop_id, owner_email=owner_email)
        payload = loop_repo.loop_to_snapshot(db, loop).model_dump(mode="json")
    return payload


@router.get("/loops/{loop_id}/rounds/{ordinal}")
def get_loop_round(loop_id: str, ordinal: int, request: Request):
    """单轮详情：五阶段状态 + 该轮产物索引；越界 → 404。"""

    owner_email = common.require_user(request)
    loop_repo = common.repo("loop_repo")
    with common.open_db() as db:
        loop = _owned_loop(db, loop_id=loop_id, owner_email=owner_email)
        row = loop_repo.get_round(db, loop_id=loop.id, ordinal=ordinal)
        if row is None:
            raise AppError(
                ErrorCode.NOT_FOUND,
                "闭环轮次不存在",
                details={"loop_id": loop.id, "ordinal": ordinal},
            )
        payload = loop_repo.loop_round_to_view(db, row).model_dump(mode="json")
    return payload


@router.get("/loops/{loop_id}/artifacts/{artifact_id}")
def get_loop_artifact(loop_id: str, artifact_id: str, request: Request):
    """产物详情：索引视图 + 原始负载；产物不属于该闭环 → 404。"""

    owner_email = common.require_user(request)
    loop_repo = common.repo("loop_repo")
    with common.open_db() as db:
        loop = _owned_loop(db, loop_id=loop_id, owner_email=owner_email)
        row = loop_repo.get_artifact(db, artifact_id)
        if row is None or row.loop_id != loop.id:
            raise AppError(
                ErrorCode.NOT_FOUND,
                "闭环产物不存在",
                details={"loop_id": loop.id, "artifact_id": artifact_id},
            )
        artifact = _artifact_view(row).model_dump(mode="json")
        payload = _artifact_payload(row)
    return {"artifact": artifact, "payload": payload}


__all__ = ["router"]