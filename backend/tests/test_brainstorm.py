"""``workflows.brainstorm.run_brainstorm`` 的行为测试（FR-04B / FR-04C）。

覆盖点：
* 全员 / 指定角色 / 智能分配三种角色选择；
* 四角色首轮**互不可见**（每个角色的 prompt 里不含同轮其他角色的产物）；
* 严格校验 + 最多一次修复；修复失败 → 该角色 step failed、Run ``partial_succeeded``；
* 全部失败 → ``failed(MODEL_OUTPUT_INVALID)``、不带产品状态；
* staging（step + 候选登记）落库，归并复用登记的服务器 ID；
* 消息只含 ``display_text``，原始 JSON 不出现；
* 预算不足 → ``failed(BUDGET_EXCEEDED)``；其它 ``LLMError`` 向上抛给 runner 重试。

全部使用本地 fake LLM / fake ctx，不联网、不起 worker。
"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

from core.llm_client import LLMOutputInvalidError, LLMResult, LLMUnavailableError, LLMUsage
from domain.contracts import (
    RoleCode,
    RunStatus,
    TaskKind,
    canonical_json,
    new_id,
)
from domain.errors import ErrorCode
from repositories import run_repo, session_repo
from repositories.database import dispose_engine, new_session, reset_for_tests
from workflows.brainstorm import run_brainstorm
from workflows.runner import BudgetExceededError

_FOUR_ROLES = (RoleCode.cto, RoleCode.designer, RoleCode.ops, RoleCode.target_user)
_UNSET = object()


# --------------------------------------------------------------------------- #
# 假 LLM
# --------------------------------------------------------------------------- #


def role_payload(
    role: RoleCode,
    *,
    display: str | None = None,
    claims: list[dict[str, Any]] | None = None,
    options: list[dict[str, Any]] | None = None,
    experiments: list[dict[str, Any]] | None = None,
    issues: list[dict[str, Any]] | None = None,
    uncertainties: list[dict[str, Any]] | None = None,
    simulated: bool | None = None,
) -> dict[str, Any]:
    """构造一份合法的 ``RoleResult`` JSON（默认含一条 claim）。"""

    if claims is None:
        claims = [
            {
                "local_id": "c1",
                "kind": "fact",
                "text": f"{role.value} 的关键事实",
                "confidence_level": "medium",
            }
        ]
    return {
        "schema_version": 1,
        "role_code": role.value,
        "display_text": f"{role.value} 的评审意见" if display is None else display,
        "claims": claims,
        "option_proposals": options or [],
        "critical_issues": issues or [],
        "uncertainties": uncertainties or [],
        "suggested_experiments": experiments or [],
        "simulated_perspective": (
            role is RoleCode.target_user if simulated is None else simulated
        ),
    }


class FakeLLM:
    """按 ``purpose`` 里的角色名出队脚本化输出，并记录每次调用的 prompt。"""

    def __init__(self, script: dict[str, list[Any]] | None = None) -> None:
        self.script: dict[str, list[Any]] = {
            role: list(items) for role, items in (script or {}).items()
        }
        self.calls: list[dict[str, Any]] = []

    def _next_item(self, role: str) -> Any:
        queue = self.script.get(role) or []
        if not queue:
            raise AssertionError(f"测试没有为角色 {role} 准备模型输出")
        if len(queue) == 1:
            return queue[0]
        return queue.pop(0)

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
        role = purpose.rsplit(".", 1)[-1]
        prompt = "\n".join(str(item.get("content", "")) for item in messages)
        self.calls.append(
            {
                "role": role,
                "purpose": purpose,
                "prompt": prompt,
                "messages": list(messages),
                "json_schema": json_schema,
            }
        )
        item = self._next_item(role)
        if isinstance(item, BaseException):
            raise item
        return item, LLMResult(
            text=canonical_json(item),
            usage=LLMUsage(
                prompt_tokens=120,
                completion_tokens=60,
                total_tokens=180,
                usage_source="provider",
            ),
            model="fake-model",
            latency_ms=1.0,
            attempts=1,
            finish_reason="stop",
        )


class FakeBudget:
    """只实现 brainstorm 用到的预算接口。"""

    def __init__(
        self,
        *,
        fail: bool = False,
        remaining: int | None = None,
        max_context_input_tokens: int = 12_000,
    ) -> None:
        self.fail = fail
        self.remaining = remaining
        self.max_context_input_tokens = max_context_input_tokens
        self.checks: list[tuple[int, str]] = []

    def ensure_required_calls(self, required_calls: int, *, purpose: str = "") -> None:
        self.checks.append((required_calls, purpose))
        if self.fail or (
            self.remaining is not None and self.remaining < required_calls
        ):
            raise BudgetExceededError(ErrorCode.BUDGET_EXCEEDED, "预算不足")


class FakeCtx:
    """``StepContext`` 的鸭子类型替身（runner 未启动，测试自己构造）。"""

    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


# --------------------------------------------------------------------------- #
# 测试基类
# --------------------------------------------------------------------------- #


class BrainstormTestBase(unittest.IsolatedAsyncioTestCase):
    problem_statement = "面向独立设计师的灵感管理工具"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        db_path = Path(self._tmp.name, "brainstorm.db").as_posix()
        reset_for_tests(f"sqlite+pysqlite:///{db_path}")
        self.db = new_session()
        self.session = session_repo.create_session(
            self.db,
            owner_email="brain@example.com",
            problem_statement=self.problem_statement,
        )
        self.db.commit()
        self.run = run_repo.create_run(
            self.db,
            session_id=self.session.id,
            owner_email="brain@example.com",
            request_id=new_id(),
            action=TaskKind.brainstorm.value,
            input_hash="hash-brainstorm",
            base_session_version=self.session.session_version,
            base_product_version=self.session.current_product_version,
            budget={"max_upstream_calls": 10},
        )
        self.db.commit()
        self.state = session_repo.get_product_state(
            self.db, self.session.id, self.run.base_product_version
        )
        messages = session_repo.list_messages(self.db, self.session.id, limit=50)
        self.current = messages[-1]
        self.llm: FakeLLM | None = None

    def tearDown(self) -> None:
        self.db.close()
        dispose_engine()
        self._tmp.cleanup()

    # -- 构造 ------------------------------------------------------------- #

    def make_llm(self, script: dict[str, list[Any]] | None = None) -> FakeLLM:
        self.llm = FakeLLM(script)
        return self.llm

    def make_ctx(
        self,
        llm: Any = None,
        *,
        budget: Any = None,
        target: str | None = None,
        state: Any = _UNSET,
        run: Any = _UNSET,
        **overrides: Any,
    ) -> FakeCtx:
        fields: dict[str, Any] = {
            "db": self.db,
            "run": self.run if run is _UNSET else run,
            "session": self.session,
            "product_state": self.state if state is _UNSET else state,
            "current_message": self.current,
            "llm": llm,
            "budget": budget if budget is not None else FakeBudget(),
            "task_kind": TaskKind.brainstorm,
            "deadline_epoch": time.time() + 60.0,
        }
        if target is not None:
            fields["target"] = target
        fields.update(overrides)
        return FakeCtx(**fields)

    def script_for_all(self, **payload_kwargs: Any) -> dict[str, list[Any]]:
        return {role.value: [role_payload(role, **payload_kwargs)] for role in _FOUR_ROLES}

    def calls(self) -> list[dict[str, Any]]:
        assert self.llm is not None
        return self.llm.calls

    def roles_called(self) -> list[str]:
        return [call["role"] for call in self.calls()]

    # -- 读库（独立 Session，验证 staging 真的落盘） ------------------------ #

    def probe_steps(self) -> list[Any]:
        probe = new_session()
        try:
            return run_repo.list_steps(probe, self.run.id)
        finally:
            probe.close()

    def probe_candidates(self) -> list[Any]:
        probe = new_session()
        try:
            return run_repo.list_candidates(probe, self.run.id)
        finally:
            probe.close()

    def probe_state(self):
        probe = new_session()
        try:
            return session_repo.get_product_state(probe, self.session.id)
        finally:
            probe.close()

    def probe_message_count(self) -> int:
        probe = new_session()
        try:
            return len(session_repo.list_messages(probe, self.session.id, limit=100))
        finally:
            probe.close()


# --------------------------------------------------------------------------- #
# 角色选择
# --------------------------------------------------------------------------- #


class RoleSelectionTests(BrainstormTestBase):
    async def test_all_roles_run_once_each_and_are_isolated(self) -> None:
        sentinels = {
            RoleCode.cto: "SENTINEL-CTO-技术债清单",
            RoleCode.designer: "SENTINEL-DESIGNER-交互流程",
            RoleCode.ops: "SENTINEL-OPS-定价模型",
            RoleCode.target_user: "SENTINEL-USER-切换成本",
        }
        script = {
            role.value: [role_payload(role, display=sentinels[role])]
            for role in sentinels
        }
        llm = self.make_llm(script)
        outcome = await run_brainstorm(self.make_ctx(llm, target="all"))

        self.assertEqual(outcome.status, RunStatus.succeeded)
        self.assertEqual(len(outcome.messages), 4)
        self.assertEqual(sorted(self.roles_called()), sorted(role.value for role in sentinels))
        # 首轮互不可见：每个角色的 prompt 里不含同轮其他角色的产物
        for call in self.calls():
            for role, text in sentinels.items():
                if role.value != call["role"]:
                    self.assertNotIn(
                        text, call["prompt"], msg=f"{call['role']} 看到了同轮产物"
                    )

    async def test_specific_target_runs_only_that_role(self) -> None:
        llm = self.make_llm({"cto": [role_payload(RoleCode.cto)]})
        outcome = await run_brainstorm(self.make_ctx(llm, target="cto"))

        self.assertEqual(outcome.status, RunStatus.succeeded)
        self.assertEqual(len(outcome.messages), 1)
        self.assertEqual(outcome.messages[0].role_code, RoleCode.cto)
        self.assertEqual(len(self.calls()), 1)

    async def test_auto_target_uses_content_keywords(self) -> None:
        self.current.content = "我们想先讨论定价、收入模型和变现路径，商业上怎么走？"
        llm = self.make_llm({"ops": [role_payload(RoleCode.ops)]})
        outcome = await run_brainstorm(self.make_ctx(llm))

        self.assertEqual(outcome.status, RunStatus.succeeded)
        self.assertEqual([m.role_code for m in outcome.messages], [RoleCode.ops])
        self.assertIn("本轮角色分配", outcome.warnings[0])

    async def test_unknown_target_attribute_falls_back_to_auto(self) -> None:
        self.current.content = "我们需要先讨论技术架构和性能瓶颈"
        llm = self.make_llm({"cto": [role_payload(RoleCode.cto)]})
        outcome = await run_brainstorm(self.make_ctx(llm, target="not-a-role"))

        # 未知 target 不在 brainstorm 层报错，兜底 auto → 命中 cto
        self.assertEqual(outcome.status, RunStatus.succeeded)
        self.assertEqual([m.role_code for m in outcome.messages], [RoleCode.cto])


# --------------------------------------------------------------------------- #
# 产出、消息与事件
# --------------------------------------------------------------------------- #


class OutputTests(BrainstormTestBase):
    async def test_product_state_is_bumped_and_claims_merged(self) -> None:
        llm = self.make_llm(self.script_for_all())
        outcome = await run_brainstorm(self.make_ctx(llm, target="all"))

        self.assertEqual(outcome.status, RunStatus.succeeded)
        state = outcome.product_state
        assert state is not None
        self.assertEqual(state.version, self.state.version + 1)
        self.assertEqual(outcome.result_product_version, state.version)
        self.assertIsNone(outcome.brief_status)
        self.assertIsNone(outcome.session_phase)
        self.assertEqual(len(state.claims), 4)
        self.assertTrue(all(claim.status.value == "proposed" for claim in state.claims))
        self.assertEqual(outcome.usage["total_tokens"], 4 * 180)

    async def test_events_are_role_completed_then_product_state_updated(self) -> None:
        llm = self.make_llm(self.script_for_all())
        outcome = await run_brainstorm(self.make_ctx(llm, target="all"))

        types = [event["type"] for event in outcome.events]
        self.assertEqual(types[-1], "product_state.updated")
        self.assertEqual(types.count("role.completed"), 4)
        assert outcome.product_state is not None
        self.assertEqual(
            outcome.events[-1]["payload"]["product_version"],
            outcome.product_state.version,
        )

    async def test_messages_only_carry_display_text(self) -> None:
        script = {
            role.value: [role_payload(role, display=f"【{role.value}】意见正文")]
            for role in _FOUR_ROLES
        }
        llm = self.make_llm(script)
        outcome = await run_brainstorm(self.make_ctx(llm, target="all"))

        self.assertEqual(
            [message.content for message in outcome.messages],
            [f"【{message.role_code.value}】意见正文" for message in outcome.messages],
        )
        for message in outcome.messages:
            self.assertNotIn("local_id", message.content)
            self.assertNotIn("schema_version", message.content)
            self.assertNotIn("option_proposals", message.content)
            self.assertEqual(message.stage.value, "brainstorm")
            self.assertEqual(message.sender_kind.value, "agent")

    async def test_empty_display_text_skips_message_but_keeps_claims(self) -> None:
        script = {
            role.value: [
                role_payload(role, display="" if role is RoleCode.cto else None)
            ]
            for role in _FOUR_ROLES
        }
        llm = self.make_llm(script)
        outcome = await run_brainstorm(self.make_ctx(llm, target="all"))

        self.assertEqual(outcome.status, RunStatus.succeeded)
        self.assertEqual(len(outcome.messages), 3)
        self.assertEqual([m.role_code for m in outcome.messages], [
            RoleCode.designer, RoleCode.ops, RoleCode.target_user,
        ])
        assert outcome.product_state is not None
        self.assertEqual(len(outcome.product_state.claims), 4)
        self.assertTrue(any("display_text 为空" in warning for warning in outcome.warnings))

    async def test_workflow_does_not_commit_product_state(self) -> None:
        llm = self.make_llm(self.script_for_all())
        await run_brainstorm(self.make_ctx(llm, target="all"))

        persisted = self.probe_state()
        self.assertEqual(persisted.version, self.state.version)
        self.assertEqual(persisted.claims, [])
        self.assertEqual(self.probe_message_count(), 1)


# --------------------------------------------------------------------------- #
# staging
# --------------------------------------------------------------------------- #


class StagingTests(BrainstormTestBase):
    async def test_steps_and_candidates_are_staged(self) -> None:
        llm = self.make_llm(self.script_for_all())
        outcome = await run_brainstorm(self.make_ctx(llm, target="all"))

        role_steps = [step for step in self.probe_steps() if step.kind == "role_draft"]
        self.assertEqual(len(role_steps), 4)
        self.assertEqual(
            {step.logical_key for step in role_steps},
            {f"brainstorm.role.{role.value}" for role in _FOUR_ROLES},
        )
        self.assertTrue(all(step.status == "succeeded" for step in role_steps))

        candidates = self.probe_candidates()
        self.assertEqual(len(candidates), 4)
        self.assertEqual({candidate.kind for candidate in candidates}, {"claim"})
        assert outcome.product_state is not None
        self.assertEqual(
            {claim.id for claim in outcome.product_state.claims},
            {candidate.id for candidate in candidates},
        )

    async def test_step_output_records_candidate_id_map(self) -> None:
        llm = self.make_llm(self.script_for_all())
        await run_brainstorm(self.make_ctx(llm, target="all"))

        for step in self.probe_steps():
            if step.kind != "role_draft":
                continue
            output = json.loads(step.output_json)
            self.assertEqual(output["role_code"], step.role_code)
            self.assertIn("c1", output["candidates"])
            self.assertEqual(output["candidates"]["c1"]["kind"], "claim")
            self.assertEqual(output["counts"]["claims"], 1)

    async def test_options_and_experiments_are_staged_and_merged(self) -> None:
        script = {
            "cto": [
                role_payload(
                    RoleCode.cto,
                    options=[
                        {
                            "local_id": "o1",
                            "title": "增量式重构",
                            "claim_local_ids": ["c1"],
                        }
                    ],
                    experiments=[
                        {
                            "local_id": "x1",
                            "hypothesis_local_id": "c1",
                            "method": "灰度试点两周",
                            "success_criterion": "崩溃率下降 50%",
                        }
                    ],
                )
            ]
        }
        llm = self.make_llm(script)
        outcome = await run_brainstorm(self.make_ctx(llm, target="cto"))

        assert outcome.product_state is not None
        self.assertEqual(len(outcome.product_state.options), 1)
        self.assertEqual(len(outcome.product_state.experiments), 1)
        self.assertEqual(
            {candidate.kind for candidate in self.probe_candidates()},
            {"claim", "option", "experiment"},
        )


# --------------------------------------------------------------------------- #
# 校验与修复
# --------------------------------------------------------------------------- #


class ValidationAndRepairTests(BrainstormTestBase):
    async def test_role_code_mismatch_is_repaired_once(self) -> None:
        script = {
            "cto": [
                role_payload(RoleCode.designer),
                role_payload(RoleCode.cto, display="修复后的技术意见"),
            ]
        }
        llm = self.make_llm(script)
        outcome = await run_brainstorm(self.make_ctx(llm, target="cto"))

        self.assertEqual(outcome.status, RunStatus.succeeded)
        self.assertEqual(len(self.calls()), 2)
        self.assertTrue(any("修复一次" in warning for warning in outcome.warnings))
        self.assertEqual(outcome.messages[0].content, "修复后的技术意见")

    async def test_extra_field_is_rejected_then_repaired(self) -> None:
        invalid = dict(role_payload(RoleCode.ops))
        invalid["extra_field"] = "协议外字段"
        script = {"ops": [invalid, role_payload(RoleCode.ops)]}
        llm = self.make_llm(script)
        outcome = await run_brainstorm(self.make_ctx(llm, target="ops"))

        self.assertEqual(outcome.status, RunStatus.succeeded)
        self.assertEqual(len(self.calls()), 2)
        self.assertTrue(any("不符合协议" in warning for warning in outcome.warnings))

    async def test_broken_json_is_repaired_once(self) -> None:
        script = {
            "ops": [
                LLMOutputInvalidError("模型返回不是合法 JSON"),
                role_payload(RoleCode.ops),
            ]
        }
        llm = self.make_llm(script)
        outcome = await run_brainstorm(self.make_ctx(llm, target="ops"))

        self.assertEqual(outcome.status, RunStatus.succeeded)
        self.assertEqual(len(self.calls()), 2)
        self.assertTrue(any("不是合法 JSON" in warning for warning in outcome.warnings))

    async def test_repeated_invalid_output_fails_role_but_run_partial(self) -> None:
        script = {
            "cto": [LLMOutputInvalidError("坏 JSON"), LLMOutputInvalidError("还是坏 JSON")],
            **{
                role.value: [role_payload(role)]
                for role in (RoleCode.designer, RoleCode.ops, RoleCode.target_user)
            },
        }
        llm = self.make_llm(script)
        outcome = await run_brainstorm(self.make_ctx(llm, target="all"))

        self.assertEqual(outcome.status, RunStatus.partial_succeeded)
        cto_steps = [s for s in self.probe_steps() if s.logical_key == "brainstorm.role.cto"]
        self.assertEqual(len(cto_steps), 1)
        self.assertEqual(cto_steps[0].status, "failed")
        self.assertEqual(cto_steps[0].error_code, "MODEL_OUTPUT_INVALID")
        self.assertEqual(
            [m.role_code for m in outcome.messages],
            [RoleCode.designer, RoleCode.ops, RoleCode.target_user],
        )
        self.assertEqual(len(self.calls()), 5)
        self.assertTrue(any("本轮失败" in warning for warning in outcome.warnings))

    async def test_all_roles_invalid_returns_failed_without_state(self) -> None:
        script = {
            role.value: [LLMOutputInvalidError("坏"), LLMOutputInvalidError("仍坏")]
            for role in _FOUR_ROLES
        }
        llm = self.make_llm(script)
        outcome = await run_brainstorm(self.make_ctx(llm, target="all"))

        self.assertEqual(outcome.status, RunStatus.failed)
        self.assertEqual(outcome.error_code, ErrorCode.MODEL_OUTPUT_INVALID)
        self.assertIsNone(outcome.product_state)
        self.assertEqual(outcome.messages, [])
        self.assertEqual(len(outcome.events), 4)
        self.assertTrue(
            all(event["payload"]["status"] == "failed" for event in outcome.events)
        )

    async def test_target_user_simulated_perspective_is_coerced(self) -> None:
        script = {"target_user": [role_payload(RoleCode.target_user, simulated=False)]}
        llm = self.make_llm(script)
        outcome = await run_brainstorm(self.make_ctx(llm, target="target_user"))

        self.assertEqual(outcome.status, RunStatus.succeeded)
        self.assertTrue(
            any("simulated_perspective" in warning for warning in outcome.warnings)
        )


# --------------------------------------------------------------------------- #
# 失败与预算
# --------------------------------------------------------------------------- #


class FailureTests(BrainstormTestBase):
    async def test_budget_rejection_returns_failed_outcome(self) -> None:
        llm = self.make_llm(self.script_for_all())
        budget = FakeBudget(fail=True)
        outcome = await run_brainstorm(self.make_ctx(llm, budget=budget, target="all"))

        self.assertEqual(outcome.status, RunStatus.failed)
        self.assertEqual(outcome.error_code, ErrorCode.BUDGET_EXCEEDED)
        self.assertIsNone(outcome.product_state)
        self.assertEqual(budget.checks[0][0], 4)
        self.assertEqual(self.calls(), [])

    async def test_budget_exhausted_midway_stops_without_more_calls(self) -> None:
        script = {
            "cto": [role_payload(RoleCode.cto)],
            "designer": [BudgetExceededError(ErrorCode.BUDGET_EXCEEDED, "调用额度用尽")],
        }
        llm = self.make_llm(script)
        outcome = await run_brainstorm(
            self.make_ctx(llm, budget=FakeBudget(remaining=4), target="all")
        )

        self.assertEqual(outcome.status, RunStatus.failed)
        self.assertEqual(outcome.error_code, ErrorCode.BUDGET_EXCEEDED)
        self.assertEqual(self.roles_called(), ["cto", "designer"])

    async def test_llm_unavailable_propagates_for_runner_retry(self) -> None:
        llm = self.make_llm({"cto": [LLMUnavailableError("上游不可用")]})
        ctx = self.make_ctx(llm, target="cto")
        with self.assertRaises(LLMUnavailableError):
            await run_brainstorm(ctx)

    async def test_missing_product_state_returns_invalid_request(self) -> None:
        llm = self.make_llm({})
        outcome = await run_brainstorm(self.make_ctx(llm, state=None))

        self.assertEqual(outcome.status, RunStatus.failed)
        self.assertEqual(outcome.error_code, ErrorCode.INVALID_REQUEST)
        self.assertIn("产品状态快照", outcome.error_message)

    async def test_missing_run_returns_invalid_request(self) -> None:
        llm = self.make_llm({})
        outcome = await run_brainstorm(self.make_ctx(llm, run=None))

        self.assertEqual(outcome.status, RunStatus.failed)
        self.assertEqual(outcome.error_code, ErrorCode.INVALID_REQUEST)
        self.assertIn("运行上下文", outcome.error_message)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
