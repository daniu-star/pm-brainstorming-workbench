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


class RoadmapItemCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    horizon: Literal["now", "next", "later"] = "next"
    quarter: str = Field(default="", max_length=24)
    objective: str = Field(default="", max_length=500)
    status: Literal["planned", "in_progress", "at_risk", "done"] = "planned"
    progress: int = Field(default=0, ge=0, le=100)
    initiative_id: str = Field(default="", max_length=80)
    risk_note: str = Field(default="", max_length=500)


class RoadmapItemUpdateRequest(BaseModel):
    horizon: Literal["now", "next", "later"] | None = None
    quarter: str | None = Field(default=None, max_length=24)
    objective: str | None = Field(default=None, max_length=500)
    status: Literal["planned", "in_progress", "at_risk", "done"] | None = None
    progress: int | None = Field(default=None, ge=0, le=100)
    risk_note: str | None = Field(default=None, max_length=500)


class PrdVersionCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    version_label: str = Field(min_length=1, max_length=32)
    change_reason: str = Field(min_length=1, max_length=500)
    content: str = Field(default="", max_length=12000)
    initiative_id: str = Field(default="", max_length=80)
    user_stories: list[str] = Field(default_factory=list, max_length=20)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=30)
    development_tasks: list[str] = Field(default_factory=list, max_length=30)
    parent_version_id: str = Field(default="", max_length=80)


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
        "roadmap_items": hub.get("roadmap_items", []),
        "prd_versions": hub.get("prd_versions", []),
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


@router.post("/{session_id}/decision-hub/roadmap", status_code=status.HTTP_201_CREATED)
async def create_roadmap_item(session_id: str, req: RoadmapItemCreateRequest, request: Request):
    session = _authorized_session(session_id, request)
    hub = _hub(session)
    initiative_ids = {item.get("id") for item in hub["initiatives"]}
    item = {
        "id": f"rm_{uuid.uuid4().hex[:10]}",
        "title": req.title.strip(),
        "horizon": req.horizon,
        "quarter": req.quarter.strip(),
        "objective": req.objective.strip(),
        "status": req.status,
        "progress": req.progress,
        "initiative_id": req.initiative_id if req.initiative_id in initiative_ids else "",
        "risk_note": req.risk_note.strip(),
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
    }
    hub["roadmap_items"].append(item)
    _save_hub(session_id, hub)
    return item


@router.patch("/{session_id}/decision-hub/roadmap/{item_id}")
async def update_roadmap_item(session_id: str, item_id: str, req: RoadmapItemUpdateRequest, request: Request):
    session = _authorized_session(session_id, request)
    hub = _hub(session)
    updates = req.model_dump(exclude_none=True)
    for item in hub["roadmap_items"]:
        if item.get("id") == item_id:
            item.update({key: value.strip() if isinstance(value, str) else value for key, value in updates.items()})
            item["updated_at"] = datetime.now().isoformat()
            _save_hub(session_id, hub)
            return item
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="路线图事项未找到")


@router.delete("/{session_id}/decision-hub/roadmap/{item_id}")
async def delete_roadmap_item(session_id: str, item_id: str, request: Request):
    session = _authorized_session(session_id, request)
    hub = _hub(session)
    before = len(hub["roadmap_items"])
    hub["roadmap_items"] = [item for item in hub["roadmap_items"] if item.get("id") != item_id]
    if len(hub["roadmap_items"]) == before:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="路线图事项未找到")
    _save_hub(session_id, hub)
    return {"status": "deleted"}


@router.post("/{session_id}/decision-hub/prd-versions", status_code=status.HTTP_201_CREATED)
async def create_prd_version(session_id: str, req: PrdVersionCreateRequest, request: Request):
    session = _authorized_session(session_id, request)
    hub = _hub(session)
    initiative_ids = {item.get("id") for item in hub["initiatives"]}
    version_ids = {item.get("id") for item in hub["prd_versions"]}

    def clean_list(values: list[str], limit: int) -> list[str]:
        return [value.strip() for value in values if value.strip()][:limit]

    version = {
        "id": f"prd_{uuid.uuid4().hex[:10]}",
        "title": req.title.strip(),
        "version_label": req.version_label.strip(),
        "change_reason": req.change_reason.strip(),
        "content": req.content.strip(),
        "initiative_id": req.initiative_id if req.initiative_id in initiative_ids else "",
        "user_stories": clean_list(req.user_stories, 20),
        "acceptance_criteria": clean_list(req.acceptance_criteria, 30),
        "development_tasks": clean_list(req.development_tasks, 30),
        "parent_version_id": req.parent_version_id if req.parent_version_id in version_ids else "",
        "created_at": datetime.now().isoformat(),
    }
    hub["prd_versions"].insert(0, version)
    _save_hub(session_id, hub)
    return version


@router.delete("/{session_id}/decision-hub/prd-versions/{version_id}")
async def delete_prd_version(session_id: str, version_id: str, request: Request):
    session = _authorized_session(session_id, request)
    hub = _hub(session)
    before = len(hub["prd_versions"])
    hub["prd_versions"] = [item for item in hub["prd_versions"] if item.get("id") != version_id]
    if len(hub["prd_versions"]) == before:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PRD 版本未找到")
    for version in hub["prd_versions"]:
        if version.get("parent_version_id") == version_id:
            version["parent_version_id"] = ""
    _save_hub(session_id, hub)
    return {"status": "deleted"}


@router.get("/{session_id}/decision-hub/prd-versions/{version_id}/diff/{base_version_id}")
async def get_prd_diff(session_id: str, version_id: str, base_version_id: str, request: Request):
    session = _authorized_session(session_id, request)
    versions = _hub(session)["prd_versions"]
    version = next((item for item in versions if item.get("id") == version_id), None)
    base = next((item for item in versions if item.get("id") == base_version_id), None)
    if version is None or base is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用于对比的 PRD 版本未找到")

    def added_values(key: str) -> list[str]:
        return [value for value in version.get(key, []) if value not in base.get(key, [])]

    def removed_values(key: str) -> list[str]:
        return [value for value in base.get(key, []) if value not in version.get(key, [])]

    return {
        "from_version": {"id": base["id"], "label": base["version_label"]},
        "to_version": {"id": version["id"], "label": version["version_label"]},
        "content_changed": base.get("content", "") != version.get("content", ""),
        "content": {"before": base.get("content", ""), "after": version.get("content", "")},
        "user_stories": {"added": added_values("user_stories"), "removed": removed_values("user_stories")},
        "acceptance_criteria": {"added": added_values("acceptance_criteria"), "removed": removed_values("acceptance_criteria")},
        "development_tasks": {"added": added_values("development_tasks"), "removed": removed_values("development_tasks")},
    }
