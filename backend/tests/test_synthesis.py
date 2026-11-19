"""``workflows.synthesis`` 主持人归并工作流的单元测试（FR-04D / AC-16）。

覆盖：
* 完整路径：主持人 1 次 + 复核 2 次；写出 ``DecisionItem(proposed)``、
  ``conflict`` 与 ``gap`` 未决事项，产品状态只推进一版；
* 2 位复核人之一反对 → ``needs_review=True``，且复核意见进入主持人消息；
* 主持人连续非法输出 → 确定性降级（``degraded=True``），不调用复核、仍写缺口；
* 预算不足 → ``failed(BUDGET_EXCEEDED)``，不写产品状态；
* ``runner.WORKFLOWS`` 注册与 ``LOGICAL_KEYS`` 一致（父 Run 自动排入的 action 可解析）。

直接调用 :func:`run_synthesis` 并用手工组装的 ``StepContext`` 替身，
不经过 Worker；staging 走真实 sqlite 短事务。
"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from core.llm_client import LLMResult, LLMUsage  # noqa: E402
from domain.contracts import (  # noqa: E402
    Action,
    DecisionStatus,
    EventType,
    IssueStatus,
    OptionItem,
    ProductState,
    RoleCode,
    RunStatus,
    StepStatus,
    TaskKind,
    initial_product_state,
    new_id,
    utc_now_iso,
)
from repositories import database, run_repo, session_repo  # noqa: E402
from repositories.database import ProductStateRow  # noqa: E402
from tests.test_runner import OWNER, RunnerTestCase  # noqa: E402
from workflows import runner, synthesis  # noqa: E402


def _llm_result(*, prompt: int = 100, completion: int = 50) -> LLMResult:
    return LLMResult(
        text="ok",
        usage=LLMUsage(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=prompt + completion,
            usage_source="provider",  # type: ignore[arg-type]
        ),
        model="fake-model",
        latency_ms=1,
        attempts=1,
        finish_reason="stop",
    )


def _moderator_payload() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "consensus_claim_ids": ["c1"],
        "conflicts": [
            {
                "topic": "先做灵感采集还是先做整理",
                "claim_ids": ["c1", "c2"],
                "impact": "决定首版信息架构",
                "needed_evidence": "需要一次 5 人可用性测试",
                "role_codes": ["cto", "designer"],
            }
        ],
        "recommended_option_id": "o1",
        "alternative_option_ids": ["o2"],
        "decision_proposals": [
            {
                "option_id": "o1",
                "rationale": "引用主张最多的方案，先采集后整理",
                "evidence_refs": ["c1"],
            }
        ],
        "unresolved_issue_ids": [],
        "next_experiment_ids": [],
        "limitations": "样本量小，结论需验证",
        "degraded": False,
    }


class _SynthesisLLM:
    """按 ``purpose`` 返回主持人 / 复核 JSON；可令主持人始终非法。"""

    def __init__(self, *, invalid_moderator: bool = False, disagree: tuple[str, ...] = ()) -> None:
        self.invalid_moderator = invalid_moderator
        self.disagree = set(disagree)
        self.calls: list[str] = []

    async def complete_json(
        self,
        *,
        messages: list[dict[str, Any]],
        max_output_tokens: int | None = None,
        timeout_seconds: float | None = None,
        purpose: str = "",
        json_schema: dict[str, Any] | None = None,
        temperature: float = 0.2,
    ) -> tuple[Any, LLMResult]:
        self.calls.append(purpose)
        if purpose == "synthesis.moderator":
            if self.invalid_moderator:
                # 结构非法：conflicts 不是数组 → 触发一次修复；第二次仍失败 → 降级。
                return {"schema_version": 1, "conflicts": "not-a-list"}, _llm_result()
            return _moderator_payload(), _llm_result()
        reviewer = purpose.rsplit(".", 1)[-1]
        agree = reviewer not in self.disagree
        return {"agree": agree, "concerns": [] if agree else [f"{reviewer} 认为证据不足"]}, _llm_result()


class _Budget:
    """预检替身：``ensure_required_calls`` 超过上限即抛预算错误。"""

    def __init__(self, *, max_upstream_calls: int = 10, max_context_input_tokens: int = 8000) -> None:
        self.max_upstream_calls = max_upstream_calls
        self.max_context_input_tokens = max_context_input_tokens
        self.purpose: list[str] = []

    def ensure_required_calls(self, required: int, *, purpose: str) -> None:
        self.purpose.append(purpose)
        if required > self.max_upstream_calls:
            raise runner.BudgetExceededError(
                "BUDGET_EXCEEDED", f"上游调用次数预算不足（还需 {required}）"
            )


class _Ctx:
    def __init__(self, *, db: Any, run: Any, session: Any, state: ProductState, llm: Any, budget: _Budget) -> None:
        self.db = db
        self.run = run
        self.session = session
        self.product_state = state
        self.current_message = None
        self.task_kind = TaskKind.synthesis
        self.llm = llm
        self.budget = budget
        self.worker_id = "test-worker"
        self.lease_epoch = 1
        self.deadline_epoch = None


def _seed_state(session_id: str) -> ProductState:
    """seeded v2：两条来自不同角色的主张 + 两个方案（o1 引用两条主张）。"""

    from domain.contracts import Claim, ClaimKind

    base = initial_product_state(session_id)
    claims = [
        Claim(id="c1", kind=ClaimKind.fact, text="目标用户需要低摩擦采集", role_code=RoleCode.cto, created_product_version=2),
        Claim(id="c2", kind=ClaimKind.fact, text="整理比采集更能留存", role_code=RoleCode.designer, created_product_version=2),
    ]
    options = [
        OptionItem(id="o1", title="先采集后整理", summary="一键采集 + 定时整理", claim_ids=["c1", "c2"]),
        OptionItem(id="o2", title="先整理后采集", summary="从模板开始的整理视图", claim_ids=["c1"]),
    ]
    payload = base.model_dump(mode="json")
    payload.update({"version": 2, "claims": [c.model_dump(mode="json") for c in claims], "options": [o.model_dump(mode="json") for o in options]})
    return ProductState.model_validate(payload)


class SynthesisWorkflowTests(RunnerTestCase):
    def _prepare(self, *, budget: _Budget | None = None) -> tuple[_Ctx, Any]:
        with database.session_scope() as db:
            session = session_repo.create_session(db, owner_email=OWNER, problem_statement="灵感管理工具")
            session_id = session.id
            state = _seed_state(session_id)
            session_repo.save_product_state(db, session, state, now_iso=utc_now_iso())
            run = run_repo.create_run(
                db,
                session_id=session_id,
                owner_email=OWNER,
                request_id=new_id(),
                action=TaskKind.synthesis.value,
                input_hash="input-hash",
                base_session_version=int(session.session_version),
                base_product_version=state.version,
                budget={"max_upstream_calls": 10},
                now_iso=utc_now_iso(),
                deadline_at=None,
                queue_deadline_at=None,
                run_id=new_id(),
            )
            self.assertIsNotNone(run)
            session_row = session
            run_row = run

        # 工作流的 ctx.db 是只读长事务：单独开一条会话，测试结束关闭。
        workflow_db = database.new_session()
        self.addCleanup(workflow_db.close)
        ctx = _Ctx(
            db=workflow_db,
            run=run_row,
            session=session_row,
            state=state,
            llm=_SynthesisLLM(),
            budget=budget or _Budget(),
        )
        return ctx, run_row.id

    def _product_versions(self, session_id: str) -> list[int]:
        with database.session_scope() as db:
            return sorted(
                int(v)
                for v in db.execute(
                    select(ProductStateRow.version).where(ProductStateRow.session_id == session_id)
                ).scalars()
            )

    def test_moderator_and_two_reviewers_write_decision_and_gaps(self) -> None:
        ctx, run_id = self._prepare()
        ctx.llm = _SynthesisLLM(disagree=("designer",))
        outcome = asyncio.run(synthesis.run_synthesis(ctx))

        self.assertEqual(outcome.status, RunStatus.succeeded, outcome.error_message)
        self.assertEqual(ctx.llm.calls, ["synthesis.moderator", "synthesis.review.cto", "synthesis.review.designer"])
        self.assertEqual(ctx.budget.purpose[0], "synthesis")

        new_state = outcome.product_state
        self.assertIsNotNone(new_state)
        assert new_state is not None
        self.assertEqual(new_state.version, 3)
        # 工作流本身不提交：产品状态由 apply_outcome 在同一事务里落库（见 brainstorm 端到端用例）。
        self.assertEqual(self._product_versions(ctx.session.id), [1, 2])

        decisions = [item for item in new_state.decisions if item.origin.value == "model"]
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].option_id, "o1")
        self.assertEqual(decisions[0].status, DecisionStatus.proposed)
        self.assertTrue(decisions[0].needs_review, decisions[0])
        self.assertEqual(decisions[0].evidence_refs, ["c1"])

        conflicts = [item for item in new_state.unresolved_issues if item.kind == "conflict"]
        gaps = [item for item in new_state.unresolved_issues if item.kind == "gap"]
        self.assertEqual(len(conflicts), 1)
        self.assertIn("先做灵感采集还是先做整理", conflicts[0].text)
        self.assertEqual(conflicts[0].status, IssueStatus.open)
        self.assertEqual(len(gaps), 1)
        self.assertIn("5 人可用性测试", gaps[0].text)

        message = outcome.messages[0]
        self.assertEqual(message.role_code, RoleCode.moderator)
        self.assertIn("决策建议", message.content)
        self.assertIn("主要分歧", message.content)
        self.assertIn("复核提示", message.content)

        types = [event["type"] for event in outcome.events]
        self.assertEqual(types.count(EventType.role_completed.value), 1)
        self.assertEqual(types.count(EventType.product_state_updated.value), 1)
        self.assertEqual(outcome.result_product_version, 3)
        self.assertEqual(outcome.usage["total_tokens"], 150 * 3)

        step = self.get_step(run_id, synthesis.SYNTHESIS_LOGICAL_KEY)
        self.assertIsNotNone(step)
        self.assertEqual(step.status, StepStatus.succeeded.value)

    def test_invalid_moderator_output_falls_back_degraded(self) -> None:
        ctx, _ = self._prepare()
        ctx.llm = _SynthesisLLM(invalid_moderator=True)
        outcome = asyncio.run(synthesis.run_synthesis(ctx))

        self.assertEqual(outcome.status, RunStatus.succeeded, outcome.error_message)
        # 首轮 + 一次修复，全部非法 → 不再调用复核。
        self.assertEqual(ctx.llm.calls, ["synthesis.moderator", "synthesis.moderator"])

        new_state = outcome.product_state
        assert new_state is not None
        self.assertEqual(new_state.version, 3)
        gaps = [item.text for item in new_state.unresolved_issues if item.kind == "gap"]
        self.assertTrue(any("主持人不可用" in text for text in gaps), gaps)
        # 降级仍写出可确认的 proposed 决策（按引用主张数排序）。
        self.assertEqual(len(new_state.decisions), 1)
        self.assertEqual(new_state.decisions[0].option_id, "o1")
        self.assertTrue(new_state.decisions[0].needs_review)
        self.assertTrue(any("降级" in item for item in outcome.warnings), outcome.warnings)
        self.assertEqual(new_state.version, 3)

    def test_budget_exceeded_fails_without_product_state(self) -> None:
        ctx, _ = self._prepare(budget=_Budget(max_upstream_calls=2))
        outcome = asyncio.run(synthesis.run_synthesis(ctx))

        self.assertEqual(outcome.status, RunStatus.failed)
        self.assertEqual(outcome.error_code, "BUDGET_EXCEEDED")
        self.assertIsNone(outcome.product_state)
        self.assertEqual(ctx.llm.calls, [])
        self.assertEqual(self._product_versions(ctx.session.id), [1, 2])


class SynthesisHelperTests(unittest.TestCase):
    """确定性辅助函数（不依赖数据库）：排序、去重、降级。"""

    def test_fallback_ranks_options_by_claim_coverage_and_marks_degraded(self) -> None:
        state = _seed_state(new_id())
        warnings: list[str] = []
        result = synthesis._fallback_synthesis(state, warnings)

        self.assertTrue(result.degraded)
        self.assertEqual(result.recommended_option_id, "o1")
        self.assertEqual(result.alternative_option_ids, ["o2"])
        self.assertEqual(result.consensus_claim_ids, ["c1", "c2"])
        self.assertTrue(warnings)

    def test_decisions_are_proposed_and_deduplicated(self) -> None:
        state = _seed_state(new_id())
        synthesis_result = synthesis.SynthesisResult.model_validate(_moderator_payload())
        warnings: list[str] = []

        first = synthesis._decisions_from(state, synthesis_result, needs_review=False, warnings=warnings)
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0].status, DecisionStatus.proposed)
        self.assertFalse(first[0].needs_review)

        second = synthesis._decisions_from(
            state.model_copy(update={"decisions": first}), synthesis_result, needs_review=True, warnings=warnings
        )
        self.assertEqual(len(second), 1, "同一决策不得重复写入")
        self.assertEqual(warnings, [])

    def test_issues_map_conflicts_and_needed_evidence_to_open_issues(self) -> None:
        state = _seed_state(new_id())
        synthesis_result = synthesis.SynthesisResult.model_validate(_moderator_payload())
        warnings: list[str] = []

        issues = synthesis._issues_from(state, synthesis_result, degraded=False, warnings=warnings)

        self.assertEqual(len(issues), 2)
        self.assertEqual({issue.kind for issue in issues}, {"conflict", "gap"})
        for issue in issues:
            self.assertEqual(issue.status, IssueStatus.open)

    def test_reviewer_roles_follow_claims_then_fill(self) -> None:
        state = _seed_state(new_id())
        self.assertEqual(synthesis._reviewer_roles(state), [RoleCode.cto, RoleCode.designer])

        empty = state.model_copy(update={"claims": []})
        self.assertEqual(synthesis._reviewer_roles(empty), [RoleCode.cto, RoleCode.designer])


class SynthesisRegistryTests(unittest.TestCase):
    def test_synthesis_workflow_registered_and_action_mapped(self) -> None:
        from workflows.runner import ACTION_TASK_KIND, LOGICAL_KEYS, WORKFLOWS

        self.assertEqual(WORKFLOWS[TaskKind.synthesis], ("workflows.synthesis", "run_synthesis"))
        self.assertEqual(LOGICAL_KEYS[TaskKind.synthesis], synthesis.SYNTHESIS_LOGICAL_KEY)
        self.assertEqual(ACTION_TASK_KIND[Action.synthesis], TaskKind.synthesis)


if __name__ == "__main__":
    unittest.main()
