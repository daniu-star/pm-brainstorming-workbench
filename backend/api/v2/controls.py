"""v2 控制意图端点：GET /controls/{cid}。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from api.v2 import common
from domain.errors import AppError, ErrorCode

router = APIRouter(tags=["v2-controls"])


@router.get("/controls/{control_id}")
def get_control(control_id: str, request: Request):
    owner_email = common.require_user(request)
    audit_repo = common.repo("audit_repo")
    with common.open_db() as db:
        control = audit_repo.get_control(db, control_id)
        if control is None:
            raise AppError(ErrorCode.NOT_FOUND, "控制意图不存在")
        if control.owner_email != owner_email:
            raise AppError(ErrorCode.FORBIDDEN, "无权访问该控制意图")
        receipt = common.control_receipt(db, control)
    return receipt.model_dump(mode="json")


__all__ = ["router"]