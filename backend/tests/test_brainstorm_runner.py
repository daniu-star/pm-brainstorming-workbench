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
import re
import sys
import time
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from core.llm_client import (  # noqa: E402
    LLMInsufficientBalanceError,
    LLMRequestError,
    LLMResult,
    LLMUnavailableError,
    LLMUsage,
)
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
    utc_now_iso,
)
from domain.errors import ErrorCode  # noqa: E402
from repositories import database, session_repo  # noqa: E402
from repositories.database import MessageRow, ProductStateRow, RunRow, StepRow  # noqa: E402
from services import command_service  # noqa: E402
from services.canvas_projector import project_canvas  # noqa: E402
from services.reducer import role_logical_key  # noqa: E402
from tests.test_runner import OWNER, RunnerTestCase  # noqa: E402
from workflows import runner  # noqa: E402

FOUR_ROLES = (RoleCode.cto, RoleCode.designer, RoleCode.ops, RoleCode.target_user)
TERMINAL_EVENT_TYPES = {event.value for event in C.TERMINAL_EVENT_TYPES}
TOP_STEP_KEY = "brainstorm.generate"
#: brainstorm 每个角色的原生 usage（与 coach 的测试替身保持一致的量级）。
PROMPT_TOKENS = 120
COMPLETION_TOKENS = 60


def _role_payload(
    role: RoleCode,
    *,
    display: str | None = None,
    evidence_refs: list[str] | None = None,
    source_message_ids: list[str] | None = None,
) -> dict[str, Any]:
    """构造一份合法的 ``RoleResult`` JSON（默认含一条 claim，可注入引用）。"""

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
                "evidence_refs": list(evidence_refs or []),
                "source_message_ids": list(source_message_ids or []),
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

    def __init__(
        self,
        *,
        invalid_roles: tuple[RoleCode, ...] = (),
        ghost_ref_roles: tuple[RoleCode, ...] = (),
        ref_ids: dict[str, list[str]] | None = None,
    ) -> None:
        self.invalid_roles = set(invalid_roles)
        #: 这些角色输出「臆造的引用」，用来验证 FR-04C 的归属校验与一次修复。
        self.ghost_ref_roles = set(ghost_ref_roles)
        #: 合法引用（本会话证据/消息），供正向用例注入。
        self.ref_ids = ref_ids or {}
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
        if role in self.ghost_ref_roles:
            # 臆造引用：每次（含修复后）都返回同一个越权证据/消息 ID → 一次修复后仍失败。
            return (
                _role_payload(
                    RoleCode(role),
                    evidence_refs=["ghost-evidence-does-not-exist"],
                    source_message_ids=["ghost-message-does-not-exist"],
                ),
                _llm_result(),
            )
        return (
            _role_payload(
                RoleCode(role),
                evidence_refs=self.ref_ids.get("evidence"),
                source_message_ids=self.ref_ids.get("messages"),
            ),
            _llm_result(),
        )


class _FailRoleLLM(_BrainstormLLM):
    """让指定角色抛不可重试的上游错误，用于构造“部分角色已成功”的失败 Run。"""

    def __init__(self, role: RoleCode, error: Exception) -> None:
        super().__init__()
        self.fail_role = role
        self.error = error
        self.attempted: list[str] = []

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
        if role == self.fail_role.value:
            self.attempted.append(role)
            raise self.error
        return await super().complete_json(
            messages=messages,
            max_output_tokens=max_output_tokens,
            timeout_seconds=timeout_seconds,
            purpose=purpose,
            json_schema=json_schema,
            temperature=temperature,
        )


class _NumberClaimLLM(_BrainstormLLM):
    """返回固定论断文本的角色替身，用来验证 AC-20 的数字标注。"""

    def __init__(self, *, claim_text: str, ref_ids: dict[str, list[str]] | None = None) -> None:
        super().__init__(ref_ids=ref_ids)
        self.claim_text = claim_text

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
        payload, result = await super().complete_json(
            messages=messages,
            max_output_tokens=max_output_tokens,
            timeout_seconds=timeout_seconds,
            purpose=purpose,
            json_schema=json_schema,
            temperature=temperature,
        )
        payload["claims"][0]["text"] = self.claim_text
        return payload, result


class _ElevenCallLLM:
    """记录调用；供“第 11 次被拒”的端到端用例。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def complete(self, **kwargs: Any) -> LLMResult:
        self.calls.append(kwargs)
        return _llm_result()


_ID_PATTERN = r"\[([0-9a-f]{32})\]"


class _SynthesisLLM:
    """主持人/复核替身：从提示词里读真实 claim/option id，保证引用不越界。"""

    def __init__(self) -> None:
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
        if purpose != "synthesis.moderator":
            return {"agree": True, "concerns": []}, _llm_result()

        digest = messages[-1].get("content", "") if messages else ""
        claims_block, _, options_block = digest.partition("### 方案与主张的引用关系")
        claim_ids = re.findall(_ID_PATTERN, claims_block)
        option_ids = re.findall(_ID_PATTERN, options_block)
        conflicts = []
        if claim_ids:
            conflicts.append(
                {
                    "topic": "首版是否先做采集",
                    "claim_ids": claim_ids[:2],
                    "impact": "决定首版信息架构",
                    "needed_evidence": "一次 5 人可用性测试",
                    "role_codes": ["cto", "designer"],
                }
            )
        return (
            {
                "schema_version": 1,
                "consensus_claim_ids": claim_ids[:1],
                "conflicts": conflicts,
                "recommended_option_id": option_ids[0] if option_ids else None,
                "alternative_option_ids": option_ids[1:2],
                "decision_proposals": [
                    {
                        "option_id": option_ids[0] if option_ids else None,
                        "rationale": "引用主张最多的方案，建议作为首版方向",
                        "evidence_refs": claim_ids[:1],
                    }
                ],
                "unresolved_issue_ids": [],
                "next_experiment_ids": [],
                "limitations": "样本量小，需一次可用性测试验证",
                "degraded": False,
            },
            _llm_result(),
        )


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

    def test_non_retryable_llm_error_records_real_code_and_leaves_no_orphan_steps(self) -> None:
        """余额不足（402）这类不可重试错误：角色 step 记录真实错误码、不白烧修复调用、无 running 残留。"""

        session_id = self._prepare_brainstorm_session()
        receipt = self._submit_brainstorm("all")
        run_id = receipt.run_id
        self.claim(run_id)

        llm = _FailRoleLLM(
            RoleCode.cto,
            LLMInsufficientBalanceError("模型账户余额不足（HTTP 402）：请充值或更换 Key"),
        )
        self.run_once(run_id, llm=llm)

        run = self.get_run(run_id)
        # Run 级失败保留真实错误码（而不是被当成 MODEL_OUTPUT_INVALID / 超时）。
        self.assertEqual(run.status, RunStatus.failed.value, run.error_message)
        self.assertEqual(run.error_code, "LLM_INSUFFICIENT_BALANCE")
        self.assertIn("余额不足", run.error_message)

        role_steps = self._role_steps(run_id)
        cto = role_steps[RoleCode.cto.value]
        self.assertEqual(cto.status, StepStatus.failed.value)
        self.assertEqual(cto.error_code, "LLM_INSUFFICIENT_BALANCE")
        self.assertIn("余额不足", cto.error_message)
        # 不可重试错误不再回喂修复，只调一次模型。
        self.assertEqual(llm.attempted, [RoleCode.cto.value])
        # 不允许任何步骤停在 running（否则界面永远显示「执行中」）。
        for step in role_steps.values():
            self.assertNotEqual(step.status, StepStatus.running.value, step.logical_key)

    def test_retry_only_reruns_failed_role_and_reuses_successes(self) -> None:
        """AC-14：重试子 Run 只调度失败步骤，已成功角色的产物直接复用。"""

        session_id = self._prepare_brainstorm_session()
        parent_id = self._submit_brainstorm("all").run_id
        self.claim(parent_id)

        # target_user 是最后一个角色：前三个角色 step 已 succeeded，随后上游报错使 Run 失败。
        failing = _FailRoleLLM(RoleCode.target_user, LLMRequestError("bad key"))
        self.run_once(parent_id, llm=failing)
        self.assertEqual(failing.attempted, [RoleCode.target_user.value])

        parent = self.get_run(parent_id)
        self.assertEqual(parent.status, RunStatus.failed.value, parent.error_message)
        self.assertEqual(parent.error_code, "LLM_REQUEST_ERROR")
        parent_steps = self._role_steps(parent_id)
        self.assertEqual(set(parent_steps), {role.value for role in FOUR_ROLES})
        for role in (RoleCode.cto, RoleCode.designer, RoleCode.ops):
            self.assertEqual(parent_steps[role.value].status, StepStatus.succeeded.value)
        self.assertNotEqual(parent_steps[RoleCode.target_user.value].status, StepStatus.succeeded.value)
        # 失败 Run 未提交产品状态，重试上下文仍与父 Run 一致。
        self.assertEqual(self._product_versions(session_id), [1, 2])

        with database.session_scope() as db:
            retry = command_service.retry_run(
                db,
                owner_email=OWNER,
                run_id=parent_id,
                request_id=new_id(),
                now_iso=utc_now_iso(),
                now_epoch=int(time.time()),
            )

        child = self.get_run(retry.run_id)
        self.assertNotEqual(child.id, parent_id)
        self.assertEqual(child.parent_run_id, parent_id)
        self.assertEqual(
            set(json.loads(child.failed_steps_json)),
            {TOP_STEP_KEY, role_logical_key(RoleCode.target_user)},
        )

        self.claim(child.id)
        retry_llm = _BrainstormLLM()
        self.run_once(child.id, llm=retry_llm)

        # 只重新调用失败专家，cto/designer/ops 不重复调用、也不新建子 Run 角色 step。
        self.assertEqual([call["role"] for call in retry_llm.calls], [RoleCode.target_user.value])
        child = self.get_run(child.id)
        self.assertEqual(child.status, RunStatus.succeeded.value, child.error_message)
        self.assertEqual(set(self._role_steps(child.id)), {RoleCode.target_user.value})
        self.assertEqual(self._usage(child.id)["calls"], 1)
        self.assertEqual(self._product_versions(session_id), [1, 2, 3])
        self.assertEqual(len(self._agent_messages(session_id)), 4)

        # 复用而非重新生成：父 Run cto 的候选 ID 原样出现在最终产品状态中。
        parent_cto_step = self.get_step(parent_id, role_logical_key(RoleCode.cto))
        self.assertIsNotNone(parent_cto_step)
        assert parent_cto_step is not None
        parent_cto_output = json.loads(parent_cto_step.output_json or "{}")
        reused_claim_id = parent_cto_output["candidates"]["c1"]["id"]
        with database.session_scope() as db:
            state = session_repo.get_product_state(db, session_id)
        self.assertIsNotNone(state)
        assert state is not None
        self.assertIn(reused_claim_id, {claim.id for claim in state.claims})

    def test_retry_reruns_all_failed_roles_and_enqueues_synthesis(self) -> None:
        """AC-14：多个专家失败（超时 + 结构失败）时，重试只调这两个并接必要汇总。"""

        session_id = self._prepare_brainstorm_session()
        parent_id = self._submit_brainstorm("all").run_id
        self.claim(parent_id)

        class _MixedFailureLLM(_BrainstormLLM):
            """ops 超时（LLMUnavailableError，可重试）、target_user 结构非法；cto/designer 正常。"""

            def __init__(self) -> None:
                super().__init__(invalid_roles=(RoleCode.target_user,))
                self.timeout_roles = {RoleCode.ops.value}

            async def complete_json(self, **kwargs: Any) -> tuple[Any, LLMResult]:  # type: ignore[override]
                role = str(kwargs.get("purpose", "")).rsplit(".", 1)[-1]
                if role in self.timeout_roles:
                    self.calls.append({"role": role, "purpose": kwargs.get("purpose", "")})
                    raise LLMUnavailableError("upstream timeout")
                return await super().complete_json(**kwargs)

        mixed = _MixedFailureLLM()
        self.run_once(parent_id, llm=mixed)

        parent = self.get_run(parent_id)
        self.assertEqual(parent.status, RunStatus.partial_succeeded.value, parent.error_message)
        parent_steps = self._role_steps(parent_id)
        for role in (RoleCode.cto, RoleCode.designer):
            self.assertEqual(parent_steps[role.value].status, StepStatus.succeeded.value)
        for role in (RoleCode.ops, RoleCode.target_user):
            self.assertNotEqual(parent_steps[role.value].status, StepStatus.succeeded.value)

        with database.session_scope() as db:
            retry = command_service.retry_run(
                db,
                owner_email=OWNER,
                run_id=parent_id,
                request_id=new_id(),
                now_iso=utc_now_iso(),
                now_epoch=int(time.time()),
            )

        child = self.get_run(retry.run_id)
        # 父 Run 的顶层步骤其实成功了（部分成功会提交），失败的是两个角色步骤。
        self.assertEqual(
            set(json.loads(child.failed_steps_json)),
            {role_logical_key(RoleCode.ops), role_logical_key(RoleCode.target_user)},
        )

        self.claim(child.id)
        retry_llm = _BrainstormLLM()
        self.run_once(child.id, llm=retry_llm)

        # 只重调两个失败专家，成功角色不再调用。
        self.assertEqual(
            sorted(call["role"] for call in retry_llm.calls),
            sorted([RoleCode.ops.value, RoleCode.target_user.value]),
        )
        child = self.get_run(child.id)
        self.assertEqual(child.status, RunStatus.succeeded.value, child.error_message)
        self.assertEqual(
            set(self._role_steps(child.id)),
            {RoleCode.ops.value, RoleCode.target_user.value},
        )
        # 父 Run 已把两个成功角色提交为 v3；重试在 v3 上合并新角色 → v4。
        self.assertEqual(self._product_versions(session_id), [1, 2, 3, 4])
        self.assertEqual(len(self._agent_messages(session_id)), 4)

        # 必要汇总：重试成功后同事务排入主持人归并 Run。
        with database.session_scope() as db:
            synthesis = db.execute(
                select(RunRow)
                .where(RunRow.session_id == session_id)
                .where(RunRow.action == TaskKind.synthesis.value)
            ).scalars().one_or_none()
        self.assertIsNotNone(synthesis, "重试成功后应排入 synthesis 子 Run")
        assert synthesis is not None
        self.assertEqual(synthesis.parent_run_id, child.id)
        self.assertEqual(synthesis.status, RunStatus.queued.value)
        self.assertEqual(int(synthesis.base_product_version), 4)

    def test_brainstorm_enqueues_synthesis_child_run(self) -> None:
        """FR-04D：四角色合并后自动排入主持人归并 Run，结论投影到画布。"""

        session_id = self._prepare_brainstorm_session()
        receipt = self._submit_brainstorm("all")
        run_id = receipt.run_id
        self.claim(run_id)
        self.run_once(run_id, llm=_BrainstormLLM())

        parent = self.get_run(run_id)
        self.assertEqual(parent.status, RunStatus.succeeded.value, parent.error_message)

        # 同事务排入的合成子 Run：queued、action=synthesis、parent 指向本 Run。
        with database.session_scope() as db:
            child = db.execute(
                select(RunRow)
                .where(RunRow.session_id == session_id)
                .where(RunRow.action == TaskKind.synthesis.value)
            ).scalars().one_or_none()
            session = session_repo.get_session(db, session_id)
        self.assertIsNotNone(child, "brainstorm 成功后应排入 synthesis 子 Run")
        assert child is not None
        self.assertEqual(child.status, RunStatus.queued.value)
        self.assertEqual(child.parent_run_id, run_id)
        self.assertEqual(int(child.base_product_version), 3)
        self.assertIsNotNone(session)
        self.assertEqual(session.active_run_id, child.id)

        # 子 Run 由同一个 Worker 继续领跑：主持人 1 次 + 两位复核。
        self.claim(child.id)
        llm = _SynthesisLLM()
        self.run_once(child.id, llm=llm)

        child = self.get_run(child.id)
        self.assertEqual(child.status, RunStatus.succeeded.value, child.error_message)
        self.assertEqual(child.result_product_version, 4)
        self.assertEqual(llm.calls[0], "synthesis.moderator")
        self.assertEqual(len(llm.calls), 3)
        self.assertTrue(all(call.startswith("synthesis.review.") for call in llm.calls[1:]))
        self.assertEqual(self._product_versions(session_id), [1, 2, 3, 4])

        # 主持人消息进入会话；产品状态包含 proposed 决策与分歧/缺口。
        messages = self._agent_messages(session_id)
        self.assertIn(RoleCode.moderator.value, {message.role_code for message in messages})
        moderator_message = [m for m in messages if m.role_code == RoleCode.moderator.value]
        self.assertEqual(len(moderator_message), 1)
        self.assertIn("决策建议", moderator_message[0].content)
        self.assertIn("主要分歧", moderator_message[0].content)

        with database.session_scope() as db:
            state = session_repo.get_product_state(db, session_id)
        self.assertEqual(state.version, 4)
        self.assertEqual(state.source_run_id, child.id)
        self.assertEqual(len(state.decisions), 1)
        self.assertEqual(state.decisions[0].status.value, "proposed")
        self.assertEqual(state.decisions[0].origin.value, "model")
        self.assertEqual({issue.kind for issue in state.unresolved_issues}, {"conflict", "gap"})

        # 画布：决策节点 + 分歧/缺口（question）节点，不再只有四份并列观点。
        canvas = project_canvas(state)
        branch_keys = {branch.key for branch in canvas.branches}
        self.assertIn("decision", branch_keys)
        self.assertIn("unresolved", branch_keys)
        decision_nodes = [node for branch in canvas.branches if branch.key == "decision" for node in branch.nodes]
        self.assertEqual(len(decision_nodes), 1)
        self.assertEqual(decision_nodes[0].kind, "decision")
        unresolved_nodes = [node for branch in canvas.branches if branch.key == "unresolved" for node in branch.nodes]
        self.assertGreaterEqual(len([node for node in unresolved_nodes if node.kind == "question"]), 1)

        # 事件：主持人产出 + 产品状态推进；终结事件唯一。
        types = self._event_types(child.id)
        self.assertEqual(types.count(EventType.role_completed.value), 1)
        self.assertEqual(types.count(EventType.product_state_updated.value), 1)
        terminal = [event for event in self.events(child.id) if str(event.type) in TERMINAL_EVENT_TYPES]
        self.assertEqual(len(terminal), 1)
        self.assertEqual(terminal[0].type, EventType.run_completed.value)

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


    def test_foreign_evidence_ref_fails_after_one_repair(self) -> None:
        """FR-04C：模型给出不属于本会话/公开知识域的引用 → 修复一次后角色 step failed。"""

        session_id = self._prepare_brainstorm_session()
        receipt = self._submit_brainstorm("all")
        run_id = receipt.run_id
        self.claim(run_id)

        llm = _BrainstormLLM(ghost_ref_roles=(RoleCode.cto,))
        self.run_once(run_id, llm=llm)

        run = self.get_run(run_id)
        self.assertEqual(run.status, RunStatus.partial_succeeded.value, run.error_message)

        # 越权引用被回喂修复一次 → 该角色两次调用；其余角色各一次。
        cto_calls = [call for call in llm.calls if call["role"] == RoleCode.cto.value]
        self.assertEqual(len(cto_calls), 2, "引用校验失败必须走一次修复")
        self.assertEqual(len(llm.calls), 5)

        step = self.get_step(run_id, role_logical_key(RoleCode.cto))
        self.assertIsNotNone(step)
        self.assertEqual(step.status, StepStatus.failed.value)
        self.assertEqual(step.error_code, ErrorCode.MODEL_OUTPUT_INVALID)
        self.assertIn("引用校验失败", step.error_message or "")
        self.assertIn("evidence_refs", step.error_message or "")

        # 其余三个角色照常合并，产品状态推进一次。
        self.assertEqual(self._product_versions(session_id), [1, 2, 3])

    def test_session_evidence_and_messages_are_referenceable(self) -> None:
        """正向：引用本会话证据行与消息 ID 通过校验并进入合并结果。"""

        session_id = self._prepare_brainstorm_session()
        receipt = self._submit_brainstorm("cto")
        run_id = receipt.run_id

        with database.session_scope() as db:
            session = session_repo.get_session(db, session_id)
            self.assertIsNotNone(session)
            first_message = session_repo.list_messages(db, session_id, limit=1)[0]
            evidence = session_repo.add_evidence(
                db,
                session,
                kind=C.EvidenceKind.user_statement,
                title="种子用户访谈",
                excerpt="用户说：现在的灵感管理工具都太重，想要一个能随手记的。",
                created_by=OWNER,
            )
            evidence_id = evidence.id
            message_id = first_message.id

        self.claim(run_id)
        llm = _BrainstormLLM(
            ref_ids={"evidence": [evidence_id], "messages": [message_id]}
        )
        self.run_once(run_id, llm=llm)

        run = self.get_run(run_id)
        self.assertEqual(run.status, RunStatus.succeeded.value, run.error_message)
        step = self.get_step(run_id, role_logical_key(RoleCode.cto))
        self.assertEqual(step.status, StepStatus.succeeded.value)
        self.assertEqual(len(llm.calls), 1)

        with database.session_scope() as db:
            state = session_repo.get_product_state(db, session_id)
        self.assertIsNotNone(state)
        cto_claims = [claim for claim in state.claims if claim.role_code is RoleCode.cto]
        self.assertTrue(cto_claims)
        self.assertIn(evidence_id, cto_claims[0].evidence_refs)
        self.assertIn(message_id, cto_claims[0].source_message_ids)

    def test_numbers_absent_from_sources_are_marked_unverified(self) -> None:
        """AC-20：来源原文里找不到的数字必须标为未验证，可追溯的数字不标。"""

        session_id = self._prepare_brainstorm_session()
        receipt = self._submit_brainstorm("cto")
        run_id = receipt.run_id

        with database.session_scope() as db:
            session = session_repo.get_session(db, session_id)
            self.assertIsNotNone(session)
            evidence = session_repo.add_evidence(
                db,
                session,
                kind=C.EvidenceKind.user_statement,
                title="种子用户访谈",
                excerpt="访谈记录：我们访谈了 12 家中小企业财务团队。",
                created_by=OWNER,
            )
            evidence_id = evidence.id

        self.claim(run_id)
        llm = _NumberClaimLLM(
            claim_text="12 家客户每周节省 40% 对账时间",
            ref_ids={"evidence": [evidence_id], "messages": []},
        )
        self.run_once(run_id, llm=llm)

        run = self.get_run(run_id)
        self.assertEqual(run.status, RunStatus.succeeded.value, run.error_message)

        with database.session_scope() as db:
            state = session_repo.get_product_state(db, session_id)
        self.assertIsNotNone(state)
        claims = [claim for claim in state.claims if claim.role_code is RoleCode.cto]
        self.assertTrue(claims)
        # 12 在证据原文里 → 可追溯；40 找不到 → 标未验证。
        self.assertEqual(claims[0].unverified_numbers, ["40"])

        projection = project_canvas(state)
        nodes = [node for branch in projection.branches for node in branch.nodes]
        self.assertTrue(nodes)
        self.assertEqual(nodes[0].unverified_numbers, ["40"])
        self.assertIn("未验证数字：40", nodes[0].detail)
        assert nodes[0].verification_suggestion is not None
        self.assertIn("统计日期", nodes[0].verification_suggestion)



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
