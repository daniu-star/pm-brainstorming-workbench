"""v2 审计端点：GET /audits/{aid}（审计快照 + 问题列表）。"""
from __future__ import annotations

from fastapi import APIRouter, Request

from api.v2 import common
from domain.contracts import AuditQuestion

router = APIRouter(tags=["v2-audits"])


def _question_payload(row) -> dict:
    fields = set(AuditQuestion.model_fields)
    data = {name: getattr(row, name) for name in fields if hasattr(row, name)}
    return AuditQuestion.model_validate(data).model_dump(mode="json")


@router.get("/audits/{audit_id}")
def get_audit(audit_id: str, request: Request):
    owner_email = common.require_user(request)
    audit_repo = common.repo("audit_repo")
    with common.open_db() as db:
        audit = audit_repo.require_audit(db, audit_id, owner_email=owner_email)
        snapshot = audit_repo.audit_to_snapshot(db, audit)
        questions = audit_repo.questions_for_audit(db, audit.id)
        payload = snapshot.model_dump(mode="json")
        payload["questions"] = [_question_payload(row) for row in questions]
    return payload


__all__ = ["router"]