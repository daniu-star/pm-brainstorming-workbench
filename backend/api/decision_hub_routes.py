from datetime import datetime
from typing import Literal
import uuid

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from api.deps import get_current_user
from db.session_store import session_store


router = APIRouter(prefix="/api/session", tags=["decision-hub"])


class EvidenceCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    source_type: Literal["interview", "feedback", "metric", "competitor", "document", "manual"] = "manual"
    summary: str = Field(min_length=1, max_length=2000)
    source_url: str = Field(default="", max_length=500)
    tags: list[str] = Field(default_factory=list, max_length=8)
    confidence: int = Field(default=70, ge=0, le=100)


class InitiativeCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    reach: int = Field(default=100, ge=0, le=1_000_000)
    impact: float = Field(default=2.0, ge=0.25, le=5)
    confidence: int = Field(default=70, ge=0, le=100)
    effort: float = Field(default=2.0, ge=0.25, le=100)
    risk: int = Field(default=2, ge=1, le=5)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)


class ExperimentCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    hypothesis: str = Field(min_length=1, max_length=1200)
    primary_metric: str = Field(min_length=1, max_length=160)
    success_criteria: str = Field(default="", max_length=500)
    initiative_id: str = Field(default="", max_length=80)


class ExperimentUpdateRequest(BaseModel):
    status: Literal["planned", "running", "validated", "invalidated"]
    learning: str = Field(default="", max_length=1200)


def _authorized_session(session_id: str, request: Request) -> dict:
    user = get_current_user(request)
    session = session_store.get(session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话未找到")
    owner = session.get("user_token", "")
    if owner and owner != user["user_token"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="无权访问此决策空间")
    return session


def _hub(session: dict) -> dict:
    hub = session.get("decision_hub") or {}
    return {
        "evidence": hub.get("evidence", []),
        "initiatives": hub.get("initiatives", []),
        "experiments": hub.get("experiments", []),
        "updated_at": hub.get("updated_at") or session.get("created_at"),
    }


def _save_hub(session_id: str, hub: dict) -> dict:
    hub["updated_at"] = datetime.now().isoformat()
    session_store.update(session_id, {"decision_hub": hub})
    return hub


@router.get("/{session_id}/decision-hub")
async def get_decision_hub(session_id: str, request: Request):
    session = _authorized_session(session_id, request)
    return _hub(session)


@router.post("/{session_id}/decision-hub/evidence", status_code=status.HTTP_201_CREATED)
async def create_evidence(session_id: str, req: EvidenceCreateRequest, request: Request):
    session = _authorized_session(session_id, request)
    hub = _hub(session)
    evidence = {
        "id": f"ev_{uuid.uuid4().hex[:10]}",
        "title": req.title.strip(),
        "source_type": req.source_type,
        "summary": req.summary.strip(),
        "source_url": req.source_url.strip(),
        "tags": [tag.strip() for tag in req.tags if tag.strip()][:8],
        "confidence": req.confidence,
        "created_at": datetime.now().isoformat(),
    }
    hub["evidence"].insert(0, evidence)
    _save_hub(session_id, hub)
    return evidence


@router.delete("/{session_id}/decision-hub/evidence/{evidence_id}")
async def delete_evidence(session_id: str, evidence_id: str, request: Request):
    session = _authorized_session(session_id, request)
    hub = _hub(session)
    before = len(hub["evidence"])
    hub["evidence"] = [item for item in hub["evidence"] if item.get("id") != evidence_id]
    if len(hub["evidence"]) == before:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="证据未找到")
    for initiative in hub["initiatives"]:
        initiative["evidence_ids"] = [item_id for item_id in initiative.get("evidence_ids", []) if item_id != evidence_id]
    _save_hub(session_id, hub)
    return {"status": "deleted"}


@router.post("/{session_id}/decision-hub/initiatives", status_code=status.HTTP_201_CREATED)
async def create_initiative(session_id: str, req: InitiativeCreateRequest, request: Request):
    session = _authorized_session(session_id, request)
    hub = _hub(session)
    evidence_ids = {item.get("id") for item in hub["evidence"]}
    linked_evidence = [item_id for item_id in req.evidence_ids if item_id in evidence_ids]
    score = round((req.reach * req.impact * (req.confidence / 100)) / req.effort, 1)
    initiative = {
        "id": f"op_{uuid.uuid4().hex[:10]}",
        "title": req.title.strip(),
        "description": req.description.strip(),
        "reach": req.reach,
        "impact": req.impact,
        "confidence": req.confidence,
        "effort": req.effort,
        "risk": req.risk,
        "priority_score": score,
        "evidence_ids": linked_evidence,
        "created_at": datetime.now().isoformat(),
    }
    hub["initiatives"].append(initiative)
    hub["initiatives"].sort(key=lambda item: item.get("priority_score", 0), reverse=True)
    _save_hub(session_id, hub)
    return initiative


@router.delete("/{session_id}/decision-hub/initiatives/{initiative_id}")
async def delete_initiative(session_id: str, initiative_id: str, request: Request):
    session = _authorized_session(session_id, request)
    hub = _hub(session)
    before = len(hub["initiatives"])
    hub["initiatives"] = [item for item in hub["initiatives"] if item.get("id") != initiative_id]
    if len(hub["initiatives"]) == before:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="决策项未找到")
    for experiment in hub["experiments"]:
        if experiment.get("initiative_id") == initiative_id:
            experiment["initiative_id"] = ""
    _save_hub(session_id, hub)
    return {"status": "deleted"}


@router.post("/{session_id}/decision-hub/experiments", status_code=status.HTTP_201_CREATED)
async def create_experiment(session_id: str, req: ExperimentCreateRequest, request: Request):
    session = _authorized_session(session_id, request)
    hub = _hub(session)
    initiative_ids = {item.get("id") for item in hub["initiatives"]}
    experiment = {
        "id": f"ex_{uuid.uuid4().hex[:10]}",
        "title": req.title.strip(),
        "hypothesis": req.hypothesis.strip(),
        "primary_metric": req.primary_metric.strip(),
        "success_criteria": req.success_criteria.strip(),
        "initiative_id": req.initiative_id if req.initiative_id in initiative_ids else "",
        "status": "planned",
        "learning": "",
        "created_at": datetime.now().isoformat(),
    }
    hub["experiments"].insert(0, experiment)
    _save_hub(session_id, hub)
    return experiment


@router.patch("/{session_id}/decision-hub/experiments/{experiment_id}")
async def update_experiment(session_id: str, experiment_id: str, req: ExperimentUpdateRequest, request: Request):
    session = _authorized_session(session_id, request)
    hub = _hub(session)
    for experiment in hub["experiments"]:
        if experiment.get("id") == experiment_id:
            experiment["status"] = req.status
            experiment["learning"] = req.learning.strip()
            experiment["updated_at"] = datetime.now().isoformat()
            _save_hub(session_id, hub)
            return experiment
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="实验未找到")
