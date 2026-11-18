"""``workflows.runner`` 执行 brainstorm 工作流的端到端测试（FR-04B / FR-04C / FR-08）。

覆盖：
* ``runner.WORKFLOWS`` 注册后，``TaskKind.brainstorm`` 真实走
  ``workflows.brainstorm.run_brainstorm``（不注入 dispatch）；
* 从 ``command_service.submit_command`` 提交 ``BrainstormPayload`` 开始，
  Worker 领取 → 执行 → 提交，四角色全流程（step / 消息 / 产品状态 / 事件 / usage）；
* ``target=all`` 与 ``target=cto`` 的角色分配差异，以及 ``runs.budget_json["target"]``
  与提交时一致（跨进程重放依据）；
* 单角色两次非法输出 → 角色 step failed、Run ``partial_succeeded``，其余角色照常合并；
* 预算中途耗尽 / 默认上限下第 11 次调用 → ``failed(BUDGET_EXCEEDED)`` 且不写产品状态；
* 成功路径 ``runs.usage_json`` 含 ``calls`` 与 ``usage_source``。

全部使用临时 sqlite 与假 LLM，不联网、不起真实 Worker 进程。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from core.llm_client import LLMResult, LLMUsage  # noqa: E402
from domain import contracts as C  # noqa: E402
from domain.contracts import (  # noqa: E402
    Action,
    BrainstormPayload,
    CommandRequest,
    EventType,
    MessageStage,
    RoleCode,
    RunStatus,
    SenderKind,
    StepStatus,
    TaskKind,
    new_id,
)
from domain.errors import ErrorCode  # noqa: E402
from repositories import database, session_repo  # noqa: E402
from repositories.database import MessageRow, ProductStateRow, StepRow  # noqa: E402
from services import command_service  # noqa: E402
from services.reducer import role_logical_key  # noqa: E402
from tests.test_runner import OWNER, RunnerTestCase  # noqa: E402
from workflows import runner  # noqa: E402

FOUR_ROLES = (RoleCode.cto, RoleCode.designer, RoleCode.ops, RoleCode.target_user)
TERMINAL_EVENT_TYPES = {event.value for event in C.TERMINAL_EVENT_TYPES}
TOP_STEP_KEY = "brainstorm.generate"
#: brainstorm 每个角色的原生 usage（与 coach 的测试替身保持一致的量级）。
PROMPT_TOKENS = 120
COMPLETION_TOKENS = 60


def _role_payload(role: RoleCode, *, display: str | None = None) -> dict[str, Any]:
    """构造一份合法的 ``RoleResult`` JSON（默认含一条 claim）。"""

    return {
        "schema_version": 1,
        "role_code": role.value,
        "display_text": f"{role.value} 的评审意见" if display is None else display,
        "claims": [
            {
                "local_id": "c1",
                "kind": "fact",
                "text": f"{role.value} 的关键事实",
                "confidence_level": "medium",
            }
        ],
        "option_proposals": [],
        "critical_issues": [],
        "uncertainties": [],
        "suggested_experiments": [],
        "simulated_perspective": role is RoleCode.target_user,
    }


def _llm_result(
    *,
    prompt: int = PROMPT_TOKENS,
    completion: int = COMPLETION_TOKENS,
    usage_source: str = "provider",
) -> LLMResult:
    return LLMResult(
        text="ok",
        usage=LLMUsage(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=prompt + completion,
            usage_source=usage_source,  # type: ignore[arg-type]
        ),
        model="fake-model",
        latency_ms=1,
        attempts=1,
        finish_reason="stop",
    )


class _BrainstormLLM:
    """按 ``purpose`` 末段的角色名返回 ``RoleResult``；可令某些角色始终非法。"""

    def __init__(self, *, invalid_roles: tuple[RoleCode, ...] = ()) -> None:
        self.invalid_roles = set(invalid_roles)
        self.calls: list[dict[str, Any]] = []

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
        self.calls.append(
            {
                "role": role,
                "purpose": purpose,
                "messages": list(messages),
                "max_output_tokens": max_output_tokens,
                "json_schema": json_schema,
            }
        )
        if role in self.invalid_roles:
            # 角色码非法 → pydantic 校验失败 → 触发一次修复；第二次仍失败则角色 step failed。
            return {"role_code": "not-a-role"}, _llm_result()
        return _role_payload(RoleCode(role)), _llm_result()


class _ElevenCallLLM:
    """记录调用；供“第 11 次被拒”的端到端用例。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def complete(self, **kwargs: Any) -> LLMResult:
        self.calls.append(kwargs)
        return _llm_result()


class BrainstormRunnerTests(RunnerTestCase):
    """从命令提交到结果提交的 brainstorm 全链路。"""

    # -- 数据准备 ---------------------------------------------------------- #

    def _prepare_brainstorm_session(self) -> str:
        """建会话并跳过澄清，使 phase 进入 brainstorming。"""

        session_id = self.create_session()
        self.session_id = session_id
        receipt = self._submit(
            Action.skip_clarification, {"reason": "测试跳过澄清"}
        )
        self.assertEqual(str(receipt.status), RunStatus.succeeded.value)
        return session_id

    def _submit(self, action: Action, payload: dict[str, Any]):
        with database.session_scope() as db:
            session = session_repo.get_session(db, self.session_id)
            self.assertIsNotNone(session)
            command = CommandRequest(
                request_id=new_id(),
                action=action,
                payload=payload,
                expected_session_version=int(session.session_version),
            )
            return command_service.submit_command(
                db,
                owner_email=OWNER,
                session_id=self.session_id,
                command=command,
            )

    def _submit_brainstorm(self, target: str, content: str = "给独立设计师做灵感管理工具"):
        payload = BrainstormPayload(content=content, target=target).model_dump(mode="json")
        return self._submit(Action.brainstorm, payload)

    # -- 查询 -------------------------------------------------------------- #

    def _product_versions(self, session_id: str) -> list[int]:
        with database.session_scope() as db:
            versions = db.execute(
                select(ProductStateRow.version).where(ProductStateRow.session_id == session_id)
            ).scalars()
            return sorted(int(v) for v in versions)

    def _agent_messages(self, session_id: str) -> list[MessageRow]:
        with database.session_scope() as db:
            rows = db.execute(
                select(MessageRow)
                .where(MessageRow.session_id == session_id)
                .where(MessageRow.sender_kind == SenderKind.agent.value)
                .order_by(MessageRow.seq)
            ).scalars()
            return list(rows)

    def _role_steps(self, run_id: str) -> dict[str, StepRow]:
        steps: dict[str, StepRow] = {}
        for role in FOUR_ROLES:
            step = self.get_step(run_id, role_logical_key(role))
            if step is not None:
                steps[role.value] = step
        return steps

    def _event_types(self, run_id: str) -> list[str]:
        return [str(event.type) for event in self.events(run_id)]

    def _usage(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        return json.loads(run.usage_json or "{}")

    # -- 用例 -------------------------------------------------------------- #

    def test_four_role_brainstorm_commits_once(self) -> None:
        session_id = self._prepare_brainstorm_session()
        receipt = self._submit_brainstorm("all")
        run_id = receipt.run_id
        self.assertEqual(str(receipt.status), RunStatus.queued.value)
        self.assertEqual(str(receipt.session_id), session_id)

        # 命令层把 target 写进 budget_json，供工作流跨进程重放。
        run = self.get_run(run_id)
        self.assertEqual(json.loads(run.budget_json)["target"], "all")

        self.claim(run_id)
        llm = _BrainstormLLM()
        self.run_once(run_id, llm=llm)

        run = self.get_run(run_id)
        self.assertEqual(run.status, RunStatus.succeeded.value, run.error_message)
        self.assertIsNone(run.error_code)
        self.assertEqual(run.result_product_version, 3)

        # 四个角色各一次模型调用（全部首轮合法，无修复）。
        self.assertEqual(len(llm.calls), 4)
        self.assertEqual(
            sorted(call["role"] for call in llm.calls), sorted(role.value for role in FOUR_ROLES)
        )
        for call in llm.calls:
            self.assertTrue(call["purpose"].startswith("brainstorm.role."))

        # 四条对外消息，只含 display_text，角色齐全。
        messages = self._agent_messages(session_id)
        self.assertEqual(len(messages), 4)
        self.assertEqual(
            [message.content for message in messages],
            [f"{role.value} 的评审意见" for role in FOUR_ROLES],
        )
        self.assertEqual({message.stage for message in messages}, {MessageStage.brainstorm.value})
        self.assertEqual({message.role_code for message in messages}, {role.value for role in FOUR_ROLES})

        # 顶层步骤 succeeded；四个角色步骤各有一条 succeeded 记录（可审计）。
        top_step = self.get_step(run_id, TOP_STEP_KEY)
        self.assertIsNotNone(top_step)
        self.assertEqual(top_step.status, StepStatus.succeeded.value)
        role_steps = self._role_steps(run_id)
        self.assertEqual(set(role_steps), {role.value for role in FOUR_ROLES})
        for step in role_steps.values():
            self.assertEqual(step.status, StepStatus.succeeded.value)
            self.assertEqual(step.attempt_count, 1)

        # 产品状态只推进一版（v1 → v2 skip → v3 brainstorm），来源是本 Run。
        self.assertEqual(self._product_versions(session_id), [1, 2, 3])
        with database.session_scope() as db:
            state = session_repo.get_product_state(db, session_id)
        self.assertEqual(state.version, 3)
        self.assertEqual(state.source_run_id, run_id)

        # 事件：排队/开始/步骤开始/四角色完成/产品状态/唯一终结事件。
        types = self._event_types(run_id)
        self.assertEqual(types.count(EventType.run_queued.value), 1)
        self.assertEqual(types.count(EventType.run_started.value), 1)
        self.assertGreaterEqual(types.count(EventType.step_started.value), 1)
        self.assertEqual(types.count(EventType.role_completed.value), 4)
        self.assertEqual(types.count(EventType.product_state_updated.value), 1)
        terminal = [event for event in self.events(run_id) if str(event.type) in TERMINAL_EVENT_TYPES]
        self.assertEqual(len(terminal), 1)
        self.assertEqual(terminal[0].type, EventType.run_completed.value)

        # usage 落库：调用次数 + 来源（供 FR-08 审计）。
        usage = self._usage(run_id)
        self.assertEqual(usage["calls"], 4)
        self.assertEqual(usage["upstream_calls"], 4)
        self.assertEqual(usage["usage_source"], "provider")
        self.assertEqual(usage["prompt_tokens"], PROMPT_TOKENS * 4)
        self.assertEqual(usage["completion_tokens"], COMPLETION_TOKENS * 4)

    def test_target_role_runs_single_role(self) -> None:
        session_id = self._prepare_brainstorm_session()
        receipt = self._submit_brainstorm("cto")
        run_id = receipt.run_id
        self.assertEqual(json.loads(self.get_run(run_id).budget_json)["target"], "cto")

        self.claim(run_id)
        llm = _BrainstormLLM()
        self.run_once(run_id, llm=llm)

        self.assertEqual([call["role"] for call in llm.calls], [RoleCode.cto.value])
        run = self.get_run(run_id)
        self.assertEqual(run.status, RunStatus.succeeded.value, run.error_message)

        role_steps = self._role_steps(run_id)
        self.assertEqual(set(role_steps), {RoleCode.cto.value})
        self.assertEqual(role_steps[RoleCode.cto.value].status, StepStatus.succeeded.value)

        messages = self._agent_messages(session_id)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].role_code, RoleCode.cto.value)
        self.assertEqual(messages[0].content, "cto 的评审意见")

        types = self._event_types(run_id)
        self.assertEqual(types.count(EventType.role_completed.value), 1)
        self.assertEqual(types.count(EventType.product_state_updated.value), 1)
        self.assertEqual(self._product_versions(session_id), [1, 2, 3])
        self.assertEqual(self._usage(run_id)["calls"], 1)

    def test_invalid_role_output_marks_partial(self) -> None:
        session_id = self._prepare_brainstorm_session()
        receipt = self._submit_brainstorm("all")
        run_id = receipt.run_id
        self.claim(run_id)

        llm = _BrainstormLLM(invalid_roles=(RoleCode.ops,))
        self.run_once(run_id, llm=llm)

        run = self.get_run(run_id)
        self.assertEqual(run.status, RunStatus.partial_succeeded.value, run.error_message)
        self.assertIsNone(run.error_code)

        # 非法角色被回喂修复一次 → 两次调用；其余角色各一次。
        ops_calls = [call for call in llm.calls if call["role"] == RoleCode.ops.value]
        self.assertEqual(len(ops_calls), 2)
        self.assertEqual(len(llm.calls), 5)

        role_steps = self._role_steps(run_id)
        self.assertEqual(set(role_steps), {role.value for role in FOUR_ROLES})
        self.assertEqual(role_steps[RoleCode.ops.value].status, StepStatus.failed.value)
        self.assertEqual(role_steps[RoleCode.ops.value].error_code, ErrorCode.MODEL_OUTPUT_INVALID)
        # 修复由 brainstorm 在工作流内部完成（同一 step 内两次模型调用），
        # 不经过 runner 的步骤重试，因此 attempt_count 仍为 1。
        self.assertEqual(role_steps[RoleCode.ops.value].attempt_count, 1)
        for role in (RoleCode.cto, RoleCode.designer, RoleCode.target_user):
            self.assertEqual(role_steps[role.value].status, StepStatus.succeeded.value)

        # 其余三个角色的产物照常合并并各写一条对外消息。
        messages = self._agent_messages(session_id)
        self.assertEqual(
            {message.role_code for message in messages},
            {RoleCode.cto.value, RoleCode.designer.value, RoleCode.target_user.value},
        )
        self.assertEqual(self._product_versions(session_id), [1, 2, 3])
        terminal = [event for event in self.events(run_id) if str(event.type) in TERMINAL_EVENT_TYPES]
        self.assertEqual(len(terminal), 1)
        self.assertEqual(terminal[0].type, EventType.run_completed.value)
        # 部分成功也写出缺失角色说明（PRD FR-03）。
        warnings = json.loads(run.warnings_json or "[]")
        self.assertTrue(any("ops" in str(item) for item in warnings), warnings)

    def test_budget_exhausted_midway_writes_no_product_state(self) -> None:
        # 直接构造 Run：输出预算只够两次 1500 token 的预留（第三次 1200+1500 > 1600）。
        _, run_id = self.create_run(
            action=TaskKind.brainstorm.value,
            budget={
                "max_upstream_calls": 10,
                "max_run_output_tokens": 1600,
                "target": "all",
            },
        )
        session_id = self.get_run(run_id).session_id
        self.claim(run_id)
        llm = _BrainstormLLM()
        self.run_once(run_id, llm=llm)

        # 第三次 begin_call 被拒绝，模型只被真实调用两次。
        self.assertEqual(len(llm.calls), 2)
        run = self.get_run(run_id)
        self.assertEqual(run.status, RunStatus.failed.value)
        self.assertEqual(run.error_code, ErrorCode.BUDGET_EXCEEDED)

        top_step = self.get_step(run_id, TOP_STEP_KEY)
        self.assertIsNotNone(top_step)
        self.assertEqual(top_step.status, StepStatus.failed.value)
        self.assertEqual(top_step.error_code, ErrorCode.BUDGET_EXCEEDED)

        # 预算超限不得写产品状态，也不得写对外消息。
        self.assertEqual(self._product_versions(session_id), [1])
        self.assertEqual(self._agent_messages(session_id), [])
        usage = self._usage(run_id)
        self.assertEqual(usage["upstream_calls"], 2)
        terminal = [event for event in self.events(run_id) if str(event.type) in TERMINAL_EVENT_TYPES]
        self.assertEqual(len(terminal), 1)
        self.assertEqual(terminal[0].type, EventType.run_failed.value)

    def test_eleventh_upstream_call_is_rejected(self) -> None:
        # 默认预算 MAX_UPSTREAM_CALLS_PER_RUN=10：第 11 次调用必须在发请求前被拒绝。
        session_id, run_id = self.create_run()
        self.claim(run_id)
        llm = _ElevenCallLLM()

        async def dispatch(ctx: Any) -> Any:
            for index in range(11):
                await ctx.llm.complete(
                    messages=[{"role": "user", "content": f"hi-{index}"}],
                    max_output_tokens=50,
                    timeout_seconds=5,
                    purpose=f"test.eleven.{index}",
                )
            raise AssertionError("第 11 次调用不应到达模型")

        runner.WORKFLOW_DISPATCH[TaskKind.clarify] = dispatch
        self.run_once(run_id, llm=llm)

        self.assertEqual(len(llm.calls), 10, "第 11 次调用必须在 begin_call 处被拒绝")
        run = self.get_run(run_id)
        self.assertEqual(run.status, RunStatus.failed.value)
        self.assertEqual(run.error_code, ErrorCode.BUDGET_EXCEEDED)
        self.assertEqual(self._product_versions(session_id), [1])

        step = self.get_step(run_id, "coach.clarify")
        self.assertIsNotNone(step)
        self.assertEqual(step.status, StepStatus.failed.value)
        self.assertEqual(step.error_code, ErrorCode.BUDGET_EXCEEDED)


class WorkflowRegistryTests(unittest.TestCase):
    """``runner.WORKFLOWS`` 是惰性描述：导入期不触发，解析时按需 import。"""

    def test_brainstorm_is_registered(self) -> None:
        from workflows.runner import WORKFLOWS

        self.assertEqual(WORKFLOWS[TaskKind.brainstorm], ("workflows.brainstorm", "run_brainstorm"))

    def test_audit_workflows_registered_lazily(self) -> None:
        from workflows.runner import WORKFLOWS

        self.assertEqual(WORKFLOWS[TaskKind.audit_open], ("workflows.audit", "run_audit_open"))
        self.assertEqual(WORKFLOWS[TaskKind.audit_answer], ("workflows.audit", "run_audit_answer"))
        self.assertEqual(WORKFLOWS[TaskKind.audit_report], ("workflows.audit", "run_audit_report"))


if __name__ == "__main__":
    unittest.main()
