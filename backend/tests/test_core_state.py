import asyncio

import core.interviewer as interviewer
from api.deps import get_current_user
from core.config import settings
from fastapi import HTTPException
from starlette.requests import Request
from core.agent_loop import _next_clarification_field
from core.canvas_parser import _enrich_decision_graph
from db.session_store import InterviewSessionStore, SessionStore


def make_store(tmp_path):
    store = SessionStore()
    store.data_dir = str(tmp_path / "sessions")
    (tmp_path / "sessions").mkdir()
    return store


def test_new_session_starts_with_persisted_clarification(tmp_path):
    store = make_store(tmp_path)
    session = store.create("为企业产品经理提供 AI 决策审计", user_token="user-a")

    assert session["phase"] == "clarify"
    assert session["clarification_state"]["status"] == "collecting"
    assert session["clarification_state"]["fields"]["core_problem"] == "为企业产品经理提供 AI 决策审计"
    assert session["clarification_state"]["current_field"] == "target_user"


def test_phase_transition_and_message_metadata_are_persisted(tmp_path):
    store = make_store(tmp_path)
    session = store.create("测试产品问题", user_token="user-a")
    store.transition_phase(session["id"], "brainstorm", "clarification_confirmed")
    message = store.add_message(
        session["id"],
        "assistant",
        "独立评审结论",
        role_name="cto",
        stage="brainstorm",
        round_id="round-1",
        agent_role="cto",
    )

    reloaded = store.get(session["id"], user_token="user-a")
    assert reloaded["phase"] == "brainstorm"
    assert reloaded["phase_history"][-1]["reason"] == "clarification_confirmed"
    assert message["round_id"] == "round-1"
    assert reloaded["messages"][-1]["agent_role"] == "cto"


def test_clarification_never_reasks_answered_fields():
    state = {
        "fields": {
            "target_user": "企业产品经理",
            "core_problem": "决策缺乏审计",
            "current_alternative": "人工评审会",
            "product_form": "",
            "success_metric": "",
            "constraints": "",
        }
    }
    assert _next_clarification_field(state) == "product_form"


def test_decision_graph_sources_only_reference_real_messages():
    messages = [
        {"id": "m1", "role": "user", "content": "需要降低评审遗漏"},
        {"id": "m2", "role": "assistant", "role_name": "cto", "content": "先验证数据边界"},
    ]
    graph = _enrich_decision_graph(
        {
            "topic": "评审流程",
            "timeline": [
                {
                    "id": "n1",
                    "type": "consensus",
                    "content": "先验证数据边界",
                    "roles": ["cto"],
                    "source_refs": ["m2", "invented"],
                }
            ],
        },
        messages,
    )

    assert graph["timeline"][0]["source_refs"] == ["m2"]
    assert {source["id"] for source in graph["sources"]} == {"m1", "m2"}


def test_audit_start_resumes_without_resetting_progress(tmp_path, monkeypatch):
    store = InterviewSessionStore()
    store.data_dir = str(tmp_path / "audits")
    (tmp_path / "audits").mkdir()
    audit = store.create("session-1", "测试审计恢复", user_token="user-a")
    store.update(
        audit["id"],
        {
            "status": "active",
            "question_count": 3,
            "dimensions_covered": ["problem_validity", "solution_effectiveness"],
            "current_dimension": "technical_risk",
        },
    )
    monkeypatch.setattr(interviewer, "interview_session_store", store)

    async def collect_events():
        return [
            event
            async for event in interviewer.run_interview_start_space(audit["id"])
        ]

    events = asyncio.run(collect_events())
    reloaded = store.get(audit["id"], user_token="user-a")

    assert reloaded["question_count"] == 3
    assert reloaded["current_dimension"] == "technical_risk"
    assert any('"type": "audit_state"' in event for event in events)


def test_data_endpoints_require_authenticated_identity(monkeypatch):
    monkeypatch.setattr(settings, "allow_anonymous_tokens", False)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/session",
            "headers": [(b"x-user-token", b"legacy-token")],
        }
    )
    try:
        get_current_user(request)
    except HTTPException as error:
        assert error.status_code == 401
    else:
        raise AssertionError("anonymous token unexpectedly authenticated")
