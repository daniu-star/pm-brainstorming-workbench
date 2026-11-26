"""v2 证据端点：按证据 ID 查询（归属检查沿用会话归属）。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from api.v2 import common
from domain.contracts import Evidence
from domain.errors import AppError, ErrorCode

router = APIRouter(tags=["v2-evidence"])


def _evidence_payload(row) -> dict:
    converter = getattr(common.repo("session_repo"), "evidence_to_model", None)
    if callable(converter):
        return converter(row).model_dump(mode="json")
    fields = set(Evidence.model_fields)
    data = {name: getattr(row, name) for name in fields if hasattr(row, name)}
    return Evidence.model_validate(data).model_dump(mode="json")


def _get_evidence(evidence_id: str, request: Request):
    owner_email = common.require_user(request)
    with common.open_db() as db:
        row = common.repo("session_repo").get_evidence(db, evidence_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, "证据不存在")
        if row.session_id:
            common.require_session_read(db, row.session_id, owner_email)
        payload = _evidence_payload(row)
    return payload


@router.get("/evidence/{evidence_id}")
def get_evidence(evidence_id: str, request: Request):
    return _get_evidence(evidence_id, request)


@router.get("/sessions/{session_id}/evidence/{evidence_id}")
def get_session_evidence(session_id: str, evidence_id: str, request: Request):
    owner_email = common.require_user(request)
    with common.open_db() as db:
        session = common.require_session_read(db, session_id, owner_email)
        row = common.repo("session_repo").get_evidence(db, evidence_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, "证据不存在")
        if row.session_id and row.session_id != session.id:
            raise AppError(ErrorCode.NOT_FOUND, "证据不存在")
        payload = _evidence_payload(row)
    return payload


__all__ = ["router"]