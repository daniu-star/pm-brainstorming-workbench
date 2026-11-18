"""v2 画布端点：确定性投影（FR-05，M1 不调用模型）。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from api.v2 import common
from domain.errors import AppError, ErrorCode

router = APIRouter(tags=["v2-canvas"])


def canvas_payload(db, session_id: str, *, product_version: int | None = None) -> dict:
    session_repo = common.repo("session_repo")
    state = common.get_product_state(db, session_id, product_version)
    projection = common.project_canvas(state)
    if projection.session_id != session_id:  # 防御：投影必须属于该会话
        raise AppError(ErrorCode.INTERNAL, "画布投影归属不一致")
    return projection.model_dump(mode="json")


@router.get("/sessions/{session_id}/canvas")
def get_canvas(session_id: str, request: Request, product_version: int | None = None):
    owner_email = common.require_user(request)
    with common.open_db() as db:
        session = common.require_owned_session(db, session_id, owner_email)
        payload = canvas_payload(db, session.id, product_version=product_version)
    return payload


__all__ = ["canvas_payload", "router"]