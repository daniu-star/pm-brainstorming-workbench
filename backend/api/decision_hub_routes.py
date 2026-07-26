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


class ReviewCommentCreateRequest(BaseModel):
    target_type: Literal["initiative", "roadmap", "prd", "general"] = "general"
    target_id: str = Field(default="", max_length=80)
    author_name: str = Field(default="", min_length=1, max_length=48)
    content: str = Field(min_length=1, max_length=1500)


class ReviewVoteRequest(BaseModel):
    target_type: Literal["initiative", "roadmap", "prd", "general"] = "general"
    target_id: str = Field(default="", max_length=80)
    author_name: str = Field(default="", min_length=1, max_length=48)
    stance: Literal["support", "concern"]


class ReviewApprovalRequest(BaseModel):
    target_type: Literal["initiative", "roadmap", "prd", "general"] = "general"
    target_id: str = Field(default="", max_length=80)
    author_name: str = Field(default="", min_length=1, max_length=48)
    status: Literal["approved", "needs_work"]
    note: str = Field(default="", max_length=500)


class AgentConfigUpdateRequest(BaseModel):
    template: Literal["saas", "fintech", "ecommerce", "consumer"] = "saas"
    company_knowledge: str = Field(default="", max_length=6000)
    audit_rules: list[str] = Field(default_factory=list, max_length=20)
    agents: list[dict] = Field(default_factory=list, max_length=8)


class MetricReviewCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    unit: str = Field(default="%", max_length=24)
    baseline: float = Field(default=0)
    target: float = Field(default=0)
    actual: float = Field(default=0)
    period: str = Field(default="", max_length=80)
    hypothesis: str = Field(default="", max_length=800)
    initiative_id: str = Field(default="", max_length=80)


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
        "review_space": hub.get("review_space", {"comments": [], "votes": [], "approvals": [], "audit_log": [], "share_token": "", "share_enabled": False}),
        "agent_config": hub.get("agent_config", {"template": "saas", "company_knowledge": "", "audit_rules": [], "agents": []}),
        "metric_reviews": hub.get("metric_reviews", []),
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


def _review_space(hub: dict) -> dict:
    space = hub.get("review_space") or {}
    return {
        "comments": space.get("comments", []),
        "votes": space.get("votes", []),
        "approvals": space.get("approvals", []),
        "audit_log": space.get("audit_log", []),
        "share_token": space.get("share_token", ""),
        "share_enabled": space.get("share_enabled", False),
    }


def _write_audit(space: dict, action: str, author: str, summary: str) -> None:
    space["audit_log"].insert(0, {"id": f"audit_{uuid.uuid4().hex[:10]}", "action": action, "author_name": author, "summary": summary, "created_at": datetime.now().isoformat()})
    del space["audit_log"][80:]


@router.get("/{session_id}/decision-hub/review")
async def get_review_space(session_id: str, request: Request):
    session = _authorized_session(session_id, request)
    return _review_space(_hub(session))


@router.post("/{session_id}/decision-hub/review/comments", status_code=status.HTTP_201_CREATED)
async def create_review_comment(session_id: str, req: ReviewCommentCreateRequest, request: Request):
    session = _authorized_session(session_id, request)
    hub = _hub(session)
    space = _review_space(hub)
    author = req.author_name.strip() or "团队成员"
    comment = {"id": f"cm_{uuid.uuid4().hex[:10]}", "target_type": req.target_type, "target_id": req.target_id, "author_name": author, "content": req.content.strip(), "created_at": datetime.now().isoformat()}
    space["comments"].insert(0, comment)
    _write_audit(space, "commented", author, f"评论了 {req.target_type or '项目'}")
    hub["review_space"] = space
    _save_hub(session_id, hub)
    return comment


@router.post("/{session_id}/decision-hub/review/votes")
async def create_review_vote(session_id: str, req: ReviewVoteRequest, request: Request):
    session = _authorized_session(session_id, request)
    hub = _hub(session)
    space = _review_space(hub)
    author = req.author_name.strip() or "团队成员"
    space["votes"] = [vote for vote in space["votes"] if not (vote.get("target_type") == req.target_type and vote.get("target_id") == req.target_id and vote.get("author_name") == author)]
    vote = {"id": f"vote_{uuid.uuid4().hex[:10]}", "target_type": req.target_type, "target_id": req.target_id, "author_name": author, "stance": req.stance, "created_at": datetime.now().isoformat()}
    space["votes"].append(vote)
    _write_audit(space, "voted", author, "支持" if req.stance == "support" else "提出关注")
    hub["review_space"] = space
    _save_hub(session_id, hub)
    return vote


@router.post("/{session_id}/decision-hub/review/approvals")
async def create_review_approval(session_id: str, req: ReviewApprovalRequest, request: Request):
    session = _authorized_session(session_id, request)
    hub = _hub(session)
    space = _review_space(hub)
    author = req.author_name.strip() or "团队成员"
    space["approvals"] = [item for item in space["approvals"] if not (item.get("target_type") == req.target_type and item.get("target_id") == req.target_id and item.get("author_name") == author)]
    approval = {"id": f"ap_{uuid.uuid4().hex[:10]}", "target_type": req.target_type, "target_id": req.target_id, "author_name": author, "status": req.status, "note": req.note.strip(), "created_at": datetime.now().isoformat()}
    space["approvals"].insert(0, approval)
    _write_audit(space, "approved" if req.status == "approved" else "requested_changes", author, "已批准" if req.status == "approved" else "请求修改")
    hub["review_space"] = space
    _save_hub(session_id, hub)
    return approval


@router.post("/{session_id}/decision-hub/review/share")
async def enable_review_share(session_id: str, request: Request):
    session = _authorized_session(session_id, request)
    hub = _hub(session)
    space = _review_space(hub)
    if not space["share_token"]:
        space["share_token"] = uuid.uuid4().hex
    space["share_enabled"] = True
    _write_audit(space, "shared", "系统", "已生成只读评审链接")
    hub["review_space"] = space
    _save_hub(session_id, hub)
    return {"share_token": space["share_token"]}


@router.get("/shared/review/{share_token}")
async def get_shared_review(share_token: str):
    for session in session_store.list_all_raw():
        space = _review_space(_hub(session))
        if space.get("share_enabled") and space.get("share_token") == share_token:
            return {"problem_statement": session.get("problem_statement", "产品决策评审"), "review_space": {key: space[key] for key in ("comments", "votes", "approvals", "audit_log")}}
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="分享链接不存在或已失效")


@router.get("/{session_id}/decision-hub/agent-config")
async def get_agent_config(session_id: str, request: Request):
    session = _authorized_session(session_id, request)
    return _hub(session)["agent_config"]


@router.put("/{session_id}/decision-hub/agent-config")
async def update_agent_config(session_id: str, req: AgentConfigUpdateRequest, request: Request):
    session = _authorized_session(session_id, request)
    hub = _hub(session)
    safe_agents = []
    for agent in req.agents[:8]:
        name = str(agent.get("name", "")).strip()[:48]
        role = str(agent.get("role", "")).strip()[:80]
        focus = str(agent.get("focus", "")).strip()[:400]
        if name and role:
            safe_agents.append({"id": str(agent.get("id") or f"agent_{uuid.uuid4().hex[:8]}")[:48], "name": name, "role": role, "focus": focus})
    hub["agent_config"] = {"template": req.template, "company_knowledge": req.company_knowledge.strip(), "audit_rules": [rule.strip() for rule in req.audit_rules if rule.strip()][:20], "agents": safe_agents}
    _save_hub(session_id, hub)
    return hub["agent_config"]


@router.post("/{session_id}/decision-hub/metrics", status_code=status.HTTP_201_CREATED)
async def create_metric_review(session_id: str, req: MetricReviewCreateRequest, request: Request):
    session = _authorized_session(session_id, request)
    hub = _hub(session)
    initiative_ids = {item.get("id") for item in hub["initiatives"]}
    delta_to_target = req.actual - req.target
    delta_to_baseline = req.actual - req.baseline
    if req.target > req.baseline:
        outcome = "above_target" if req.actual >= req.target else ("improving" if req.actual > req.baseline else "below_baseline")
    else:
        outcome = "above_target" if req.actual <= req.target else ("improving" if req.actual < req.baseline else "below_baseline")
    review = {"id": f"metric_{uuid.uuid4().hex[:10]}", "name": req.name.strip(), "unit": req.unit.strip(), "baseline": req.baseline, "target": req.target, "actual": req.actual, "period": req.period.strip(), "hypothesis": req.hypothesis.strip(), "initiative_id": req.initiative_id if req.initiative_id in initiative_ids else "", "outcome": outcome, "delta_to_target": round(delta_to_target, 2), "delta_to_baseline": round(delta_to_baseline, 2), "created_at": datetime.now().isoformat()}
    hub["metric_reviews"].insert(0, review)
    _save_hub(session_id, hub)
    return review


@router.delete("/{session_id}/decision-hub/metrics/{metric_id}")
async def delete_metric_review(session_id: str, metric_id: str, request: Request):
    session = _authorized_session(session_id, request)
    hub = _hub(session)
    before = len(hub["metric_reviews"])
    hub["metric_reviews"] = [item for item in hub["metric_reviews"] if item.get("id") != metric_id]
    if len(hub["metric_reviews"]) == before:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="指标记录未找到")
    _save_hub(session_id, hub)
    return {"status": "deleted"}
