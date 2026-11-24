"""一次性探针：复现 audit.report 落回 fallback 的真实原因（不写库、不改代码）。

用法：cd backend && ../.venv/Scripts/python.exe ../.tmp_probe_report.py
"""

import asyncio
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent / "backend" if (Path(__file__).resolve().parent / "backend").exists() else Path(__file__).resolve().parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from core.llm_client import LLMClient, LLMError  # noqa: E402
from domain import contracts as C  # noqa: E402
from repositories import audit_repo, database  # noqa: E402
from workflows import audit as audit_workflow  # noqa: E402


def build_states(audit):
    raw = json.loads(audit.dimension_states_json or "[]")
    states = []
    for item in raw:
        try:
            states.append(C.DimensionState.model_validate(item))
        except Exception as exc:  # noqa: BLE001
            print("state parse failed", exc)
    return states


def pick_audit(db):
    rows = db.execute(
        __import__("sqlalchemy").text(
            "select id from audits where dimension_states_json is not null "
            "and dimension_states_json not in ('', '[]') order by created_at desc limit 5"
        )
    ).scalars().all()
    return [audit_repo.get_audit(db, row) for row in rows]


async def main() -> int:
    database.configure(str(database.default_database_url()), force=True)
    with database.session_scope() as db:
        audits = [a for a in pick_audit(db) if a is not None]
    print("candidate audits", [(a.id, a.status, a.question_count) for a in audits])

    class _FakeAudit:
        product_version = 2
        question_count = 2
        end_reason = "completed"

        def __init__(self):
            self.id = "fake-audit"
            self.status = "completed"

    target = audits[0] if audits else _FakeAudit()
    states = build_states(target) if audits else [
        C.DimensionState(
            key="problem_validity",
            assessment=C.Assessment.supported,
            rationale="12 家访谈里 9 家确认每周对账 6 小时以上，问题成立。",
            answered_question_ids=["q1"],
        ),
        C.DimensionState(
            key="business_loop",
            assessment=C.Assessment.gap,
            rationale="付费意愿集中在 300 元/月，但采购周期与决策链未验证。",
            answered_question_ids=["q2"],
        ),
    ]
    print("states", len(states), [s.key for s in states])
    instruction = audit_workflow._report_instruction(
        target, states=states, question_count=int(target.question_count or 0)
    )
    schema = audit_workflow.ReportDraft.model_json_schema()
    client = LLMClient()
    messages = [
        {"role": "system", "content": "你是产品审计主持人，输出严格符合给定 schema 的 JSON 对象。"},
        {"role": "user", "content": instruction},
    ]
    try:
        draft, result = await client.complete_json(
            messages=messages,
            json_schema=schema,
            max_output_tokens=audit_workflow.REPORT_MAX_OUTPUT_TOKENS,
            timeout_seconds=120,
            purpose="audit.report",
        )
        print("OK draft keys", sorted(draft.keys()) if isinstance(draft, dict) else type(draft))
        print("usage", result.usage)
    except (LLMError, ValueError) as exc:
        print("FAILED", type(exc).__name__, str(exc)[:500])
        raw, result = await client.complete(
            messages=messages,
            max_output_tokens=audit_workflow.REPORT_MAX_OUTPUT_TOKENS,
            timeout_seconds=120,
            purpose="audit.report.raw",
        )
        print("usage", result.usage)
        print("RAW_LEN", len(raw))
        print("RAW_HEAD", raw[:1200].replace("\n", "\\n"))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
