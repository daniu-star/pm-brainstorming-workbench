"""``workflows.loop`` 五阶段闭环测试（真实 sqlite + 假 LLM，不联网）。

覆盖契约 §5.3 / §5.5 的关键合同：

* 五阶段全成功：5 个 ``loop.stage.*`` step、5 条产物、每阶段一条
  ``loop.updated``、末条 ``product_state.updated``、产品状态 v+1、
  ``loop.status=awaiting_decision``、``loop_version`` 递进、摘要消息；
* 阶段 blocker（validate 有 fail 检查）：该阶段 step failed，但后续阶段继续
  执行，Run 以 ``partial_succeeded`` 提交，评分被硬规则压到 70；
* evaluate 的模型自评分被覆盖：模型 96 分 + 2 项 blocker → 70 分且
  ``hard_capped=True``；
* 轮次用尽：``ordinal >= max_rounds`` 时结论仍为 iterate，但 loop 进入
  ``awaiting_decision`` 并追加「轮次已用尽」提示；
* 预算不足：直接 ``failed(BUDGET_EXCEEDED)``，不写 product_state，loop/round
  在 apply 钩子里标记失败；
* 结构输出两次非法：``failed(MODEL_OUTPUT_INVALID)``；
* 缺少 loop 参数：``failed(INVALID_REQUEST)``。

所有 product_state / 事件 / 消息都经 ``command_service.apply_outcome`` 在同一
事务提交，与真实 runner 路径一致。
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select

from core.llm_client import LLMResult, LLMUsage
from core.loop_prompts import (
    PURPOSE_DEVELOP,
    PURPOSE_EVALUATE,
    PURPOSE_IDEATE,
    PURPOSE_TEST,
    PURPOSE_VALIDATE,
)
from domain import contracts as C
from domain.contracts import (
    Action,
    CommandRequest,
    LoopArtifactKind,
    LoopRoundStatus,
    LoopStage,
    LoopStageStatus,
    LoopStatus,
    LoopVerdict,
    RunStatus,
    StepStatus,
    new_id,
)
from domain.errors import AppError, ErrorCode
from repositories import database, loop_repo, run_repo, session_repo
from repositories.database import RunRow, StepRow, session_scope
from services import command_service
from services.command_service import apply_outcome
from workflows import loop as loop_workflow
from workflows.runner import BudgetExceededError, BudgetTracker

OWNER = "loop-workflow@example.com"
WORKER = "test-worker"
NOW_ISO = "2026-11-19T09:00:00Z"
NOW_EPOCH = 1_764_224_400
#: 每条命令推进一个限流窗口（同 test_loop_commands 的做法）。
CLOCK = {"epoch": NOW_EPOCH}
PROMPT_TOKENS = 120
COMPLETION_TOKENS = 60

STAGE_PURPOSES = [
    PURPOSE_IDEATE,
    PURPOSE_VALIDATE,
    PURPOSE_DEVELOP,
    PURPOSE_TEST,
    PURPOSE_EVALUATE,
]


# --------------------------------------------------------------------------- #
# 假 LLM
# --------------------------------------------------------------------------- #


def _llm_result() -> LLMResult:
    return LLMResult(
        text="ok",
        usage=LLMUsage(
            prompt_tokens=PROMPT_TOKENS,
            completion_tokens=COMPLETION_TOKENS,
            total_tokens=PROMPT_TOKENS + COMPLETION_TOKENS,
            usage_source="provider",
        ),
        model="fake-model",
        latency_ms=1,
        attempts=1,
        finish_reason="stop",
    )


class FakeLLM:
    """按 ``purpose`` 出队的假 LLM；脚本耗尽时抛 ``AssertionError``。"""

    def __init__(self, script: dict[str, list[Any]]) -> None:
        self.script = {key: list(value) for key, value in script.items()}
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
        self.calls.append(
            {
                "purpose": purpose,
                "messages": list(messages),
                "max_output_tokens": max_output_tokens,
                "json_schema": json_schema,
            }
        )
        queue = self.script.get(purpose)
        if not queue:
            raise AssertionError(f"未预期的模型调用：purpose={purpose}")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item, _llm_result()

    def purposes(self) -> list[str]:
        return [call["purpose"] for call in self.calls]


# --------------------------------------------------------------------------- #
# 五阶段合法输出
# --------------------------------------------------------------------------- #


def _spec_payload() -> dict[str, Any]:
    return {
        "title": "预算协同 MVP",
        "summary": "让财务团队在一个页面完成月度预算对齐与审批",
        "features": [
            {
                "id": "f1",
                "title": "预算表导入",
                "description": "支持上传 CSV 预算表并解析成预算行",
                "acceptance": "上传 CSV 后能逐行看到解析后的预算",
                "priority": "must",
                "effort": "M",
            },
            {
                "id": "f2",
                "title": "协同审批",
                "description": "在预算行上发起并完成审批",
                "acceptance": "审批通过后预算行状态变为已通过",
                "priority": "must",
                "effort": "M",
            },
        ],
        "assumptions": [
            {
                "id": "a1",
                "statement": "财务团队愿意把表格迁移到线上工具",
                "risk": "迁移成本高，可能抵触",
                "verification": "访谈 3 个财务团队确认迁移意愿",
            }
        ],
        "not_in_scope": ["移动端 App", "多币种"],
    }


def _validation_payload(*, fail_first: bool = False) -> dict[str, Any]:
    return {
        "checks": [
            {
                "id": "c1",
                "feature_id": "f1",
                "question": "各家财务团队的 CSV 模板是否统一？",
                "result": "fail" if fail_first else "pass",
                "evidence_refs": [],
                "notes": "访谈结论" if not fail_first else "模板差异较大，需要映射层",
            },
            {
                "id": "c2",
                "feature_id": "f2",
                "question": "现有审批权限模型能否直接复用？",
                "result": "pass",
                "evidence_refs": [],
                "notes": "可复用现有权限",
            },
        ],
        "summary": "两个 must 功能的关键假设都已验证",
        "unknowns": [],
    }


def _prototype_payload() -> dict[str, Any]:
    return {
        "title": "预算协同原型",
        "summary": "单页原型：导入 CSV 后逐行展示并支持审批",
        "entry_file": "app.py",
        "run_command": "python app.py",
        "files": [
            {
                "path": "app.py",
                "language": "python",
                "content": "print('budget sync prototype')\n",
                "purpose": "入口：解析 CSV 并渲染预算行",
            }
        ],
        "limitations": "无持久化存储，刷新后数据丢失",
    }


def _report_payload() -> dict[str, Any]:
    return {
        "total": 2,
        "passed": 2,
        "failed": 0,
        "skipped": 0,
        "simulated": True,
        "cases": [
            {
                "id": "t1",
                "feature_id": "f1",
                "title": "导入预算表",
                "steps": ["上传样例 CSV", "查看解析结果"],
                "expected": "逐行展示预算",
                "status": "passed",
                "evidence": "原型截图",
            },
            {
                "id": "t2",
                "feature_id": "f2",
                "title": "发起审批",
                "steps": ["点击审批按钮", "确认状态"],
                "expected": "状态变为已通过",
                "status": "passed",
                "evidence": "原型截图",
            },
        ],
        "notes": "本轮为模拟执行，未实际执行",
    }


_FULL_SCORES = {
    "functionality": 25,
    "feasibility": 14,
    "user_value": 20,
    "risk": 14,
    "delivery": 10,
    "testability": 9,
    "docs": 4,
}

_LOW_SCORES = {
    "functionality": 8,
    "feasibility": 6,
    "user_value": 6,
    "risk": 6,
    "delivery": 4,
    "testability": 5,
    "docs": 5,
}


def _scorecard_payload(
    *,
    scores: dict[str, int] | None = None,
    blockers: list[str] | None = None,
    verdict: str = "pending",
) -> dict[str, Any]:
    chosen = dict(scores or _FULL_SCORES)
    dimensions = []
    for key, max_score in C.LOOP_SCORE_MAX.items():
        dimensions.append(
            {
                "key": key.value,
                "score": int(chosen.get(key.value, 0)),
                "max": max_score,
                "reason": "按五阶段产物打分",
            }
        )
    return {
        "total": sum(int(dimension["score"]) for dimension in dimensions),
        "dimensions": dimensions,
        "verdict": verdict,
        "blockers": list(blockers or []),
        "highlights": ["主流程闭环完整", "原型可复现"],
        "next_actions": [],
        "generated_by": "model",
        "hard_capped": False,
    }


def _script(
    *,
    validation: dict[str, Any] | None = None,
    scorecard: dict[str, Any] | None = None,
) -> dict[str, list[Any]]:
    return {
        PURPOSE_IDEATE: [_spec_payload()],
        PURPOSE_VALIDATE: [validation or _validation_payload()],
        PURPOSE_DEVELOP: [_prototype_payload()],
        PURPOSE_TEST: [_report_payload()],
        PURPOSE_EVALUATE: [scorecard or _scorecard_payload()],
    }


class _TightBudget:
    """剩余上游调用不足的预算桩（在第一次预检就失败）。"""

    def ensure_required_calls(self, required: int, *, purpose: str = "") -> None:
        raise BudgetExceededError(
            ErrorCode.BUDGET_EXCEEDED,
            f"剩余上游调用不足以完成本轮（需要 {required} 次）",
        )


# --------------------------------------------------------------------------- #
# 测试基类
# --------------------------------------------------------------------------- #


class LoopWorkflowTestCase(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.makedirs(".tmp_tests", exist_ok=True)
        cls._tmpdir = tempfile.mkdtemp(
            prefix="loop_workflow_", dir=os.path.abspath(".tmp_tests")
        )
        db_url = "sqlite+pysqlite:///" + os.path.join(cls._tmpdir, "loop.db").replace(
            "\\\\", "/"
        )
        database.configure(db_url, force=True)
        database.initialize_database(database.get_engine(), drop_first=True)

    @classmethod
    def tearDownClass(cls) -> None:
        database.dispose_engine()
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def setUp(self) -> None:
        self.db = database.new_session()
        self._cancel_open_runs()
        self.session_id = self._new_session()
        self._advance_to_brainstorming(self.session_id)
        self.run_id = ""
        self.loop_id = ""
        self.lease_epoch = 0

    def tearDown(self) -> None:
        self._cancel_open_runs()
        self.db.close()

    # -- 夹具 ------------------------------------------------------------- #

    def _cancel_open_runs(self) -> None:
        with session_scope() as db:
            rows = (
                db.execute(select(RunRow).where(RunRow.owner_email == OWNER)).scalars().all()
            )
            for row in rows:
                if row.status not in {
                    RunStatus.succeeded.value,
                    RunStatus.partial_succeeded.value,
                    RunStatus.failed.value,
                    RunStatus.cancelled.value,
                }:
                    command_service.cancel_run(
                        db, owner_email=OWNER, run_id=row.id, now_iso=NOW_ISO
                    )

    def _new_session(self) -> str:
        with session_scope() as db:
            session = session_repo.create_session(
                db,
                session_id=C.new_id(),
                owner_email=OWNER,
                problem_statement="闭环工作流验收：预算协同工具",
                now_iso=NOW_ISO,
            )
            return session.id

    def _submit(
        self,
        action: Action,
        payload: dict[str, Any],
        *,
        version: int | None = None,
    ) -> Any:
        CLOCK["epoch"] += 70
        with session_scope() as db:
            command = CommandRequest(
                request_id=C.new_request_id(),
                action=action,
                expected_session_version=version,
                payload=payload,
            )
            return command_service.submit_command(
                db,
                owner_email=OWNER,
                session_id=self.session_id,
                command=command,
                now_iso=NOW_ISO,
                now_epoch=CLOCK["epoch"],
            )

    def _advance_to_brainstorming(self, session_id: str) -> None:
        with session_scope() as db:
            state = session_repo.get_product_state(db, session_id)
            self.session_id = session_id
            self._submit(
                Action.revise_brief,
                {
                    "expected_product_version": state.version,
                    "fields": {
                        "target_users": {"value": "中小企业财务团队", "status": "proposed"},
                        "usage_scenario": {"value": "月度预算协同与审批", "status": "proposed"},
                        "core_problem": {"value": "预算协作靠表格来回传", "status": "proposed"},
                        "success_criteria": {
                            "value": "月度预算对齐时间缩短一半",
                            "status": "proposed",
                        },
                    },
                },
            )
            state = session_repo.get_product_state(db, session_id)
            self._submit(
                Action.confirm_brief,
                {"expected_product_version": state.version, "fields": {}},
            )

    def _start_loop(self, **overrides: Any) -> Any:
        with session_scope() as db:
            state = session_repo.get_product_state(db, self.session_id)
        payload: dict[str, Any] = {
            "scope": "mvp",
            "max_rounds": 3,
            "target_score": 85,
            "focus": "先做预算协同主流程",
            "expected_product_version": state.version,
        }
        payload.update(overrides)
        return self._submit(Action.start_loop, payload)

    def _prepare_round(self, **overrides: Any) -> None:
        """start_loop 建 Loop/Round/Run，然后由测试 Worker 领取 Run。"""

        receipt = self._start_loop(**overrides)
        self.loop_id = str(receipt.loop_id)
        self.run_id = str(receipt.run_id)
        claimed = run_repo.claim_next_run(
            self.db,
            worker_id=WORKER,
            now_epoch=int(time.time()),
            lease_seconds=60,
        )
        self.assertIsNotNone(claimed)
        self.assertEqual(str(claimed.id), self.run_id)
        self.lease_epoch = int(claimed.lease_epoch)
        self.db.commit()

    def _create_plain_run(self) -> None:
        """没有 loop 参数的普通 Run（用于参数缺失用例）。"""

        session = session_repo.get_session(self.db, self.session_id)
        state = session_repo.get_product_state(self.db, self.session_id)
        now = int(time.time())
        run = run_repo.create_run(
            self.db,
            session_id=self.session_id,
            owner_email=OWNER,
            request_id=new_id(),
            action=Action.start_loop.value,
            input_hash="plain-run",
            base_session_version=int(session.session_version),
            base_product_version=int(state.version),
            budget={},
            now_iso=NOW_ISO,
            deadline_at=now + 300,
            queue_deadline_at=now + 600,
            run_id=new_id(),
        )
        self.db.commit()
        claimed = run_repo.claim_next_run(
            self.db,
            worker_id=WORKER,
            now_epoch=int(time.time()),
            lease_seconds=60,
        )
        self.assertIsNotNone(claimed)
        self.assertEqual(str(claimed.id), str(run.id))
        self.run_id = str(run.id)
        self.lease_epoch = int(claimed.lease_epoch)
        self.db.commit()

    def make_ctx(
        self,
        *,
        llm: Any,
        budget: Any = None,
        current_message: Any = None,
    ) -> SimpleNamespace:
        run = run_repo.get_run(self.db, self.run_id)
        self.assertIsNotNone(run)
        session = session_repo.get_session(self.db, self.session_id)
        self.assertIsNotNone(session)
        state = session_repo.get_product_state(
            self.db, self.session_id, version=int(run.base_product_version)
        )
        if budget is None:
            budget = BudgetTracker.from_budget_json(run.budget_json)
        return SimpleNamespace(
            db=self.db,
            run=run,
            session=session,
            product_state=state,
            current_message=current_message,
            task_kind=C.TaskKind.loop_round,
            llm=llm,
            budget=budget,
            worker_id=WORKER,
            lease_epoch=int(run.lease_epoch),
            deadline_epoch=time.time() + 120,
        )

    def commit_outcome(self, outcome: Any) -> None:
        apply_outcome(
            self.db,
            run_id=self.run_id,
            worker_id=WORKER,
            lease_epoch=self.lease_epoch,
            outcome=outcome,
        )
        self.db.commit()

    # -- 查询 ------------------------------------------------------------- #

    def _loop_row(self) -> Any:
        with session_scope() as db:
            row = loop_repo.get_loop(db, self.loop_id)
            self.assertIsNotNone(row)
            db.expunge(row)
            return row

    def _round_row(self, ordinal: int = 1) -> Any:
        with session_scope() as db:
            row = loop_repo.get_round(db, loop_id=self.loop_id, ordinal=ordinal)
            self.assertIsNotNone(row)
            db.expunge(row)
            return row

    def _stage_rows(self, ordinal: int = 1) -> list[Any]:
        with session_scope() as db:
            rows = loop_repo.list_stages(db, loop_id=self.loop_id, ordinal=ordinal)
            for row in rows:
                db.expunge(row)
            return list(rows)

    def _artifact_rows(self, ordinal: int = 1) -> list[Any]:
        with session_scope() as db:
            rows = loop_repo.list_artifacts(db, loop_id=self.loop_id, ordinal=ordinal)
            for row in rows:
                db.expunge(row)
            return list(rows)

    def _step_rows(self) -> list[Any]:
        self.db.expire_all()
        rows = (
            self.db.execute(
                select(StepRow).where(
                    StepRow.run_id == self.run_id,
                    StepRow.kind == loop_workflow.LOOP_STEP_KIND,
                )
            )
            .scalars()
            .all()
        )
        return list(rows)

    def _events(self, run_id: str | None = None) -> list[Any]:
        with session_scope() as db:
            rows = run_repo.events_after(
                db, run_id or self.run_id, after_seq=0, limit=200
            )
            for row in rows:
                db.expunge(row)
            return list(rows)

    def _event_payloads(self, event_type: str) -> list[dict[str, Any]]:
        payloads = []
        for row in self._events():
            if str(row.type) == event_type:
                payloads.append(json.loads(row.payload_json))
        return payloads

    def _run_row(self) -> Any:
        with session_scope() as db:
            row = run_repo.get_run(db, self.run_id)
            self.assertIsNotNone(row)
            db.expunge(row)
            return row

    def _session_row(self) -> Any:
        with session_scope() as db:
            row = session_repo.get_session(db, self.session_id)
            self.assertIsNotNone(row)
            db.expunge(row)
            return row

    # -- 通用断言 --------------------------------------------------------- #

    def assert_stage_events(self, outcome: Any, *, expected_loop_status: LoopStatus) -> None:
        loop_events = [
            event
            for event in outcome.events
            if event["type"] == C.EventType.loop_updated.value
        ]
        self.assertEqual(len(loop_events), len(STAGE_PURPOSES))
        stages = [event["payload"]["stage"] for event in loop_events]
        self.assertEqual(stages, [stage.value for stage in LoopStage])
        for event in loop_events:
            payload = event["payload"]
            self.assertEqual(payload["loop_id"], self.loop_id)
            self.assertEqual(payload["status"], expected_loop_status.value)
            self.assertEqual(len(payload["artifact_ids"]), 1)
        self.assertEqual(
            outcome.events[-1]["type"], C.EventType.product_state_updated.value
        )
        self.assertEqual(len(outcome.events), len(STAGE_PURPOSES) + 1)


# --------------------------------------------------------------------------- #
# 用例
# --------------------------------------------------------------------------- #


class LoopWorkflowSuccessTests(LoopWorkflowTestCase):
    async def test_full_round_succeeds_and_awaits_decision(self) -> None:
        self._prepare_round()
        llm = FakeLLM(_script())
        ctx = self.make_ctx(llm=llm)
        outcome = await loop_workflow.run_loop_round(ctx)

        self.assertEqual(outcome.status, RunStatus.succeeded)
        self.assertIsNotNone(outcome.product_state)
        base_version = int(ctx.run.base_product_version)
        expected_version = base_version + 1
        self.assertEqual(outcome.product_state.version, expected_version)
        self.assertEqual(len(outcome.events), 6)
        self.assert_stage_events(outcome, expected_loop_status=LoopStatus.awaiting_decision)
        self.assertEqual(outcome.events[-2]["payload"]["score"], 96)
        self.assertEqual(outcome.events[-2]["payload"]["verdict"], LoopVerdict.pass_.value)
        self.assertEqual(
            outcome.events[-1]["payload"]["product_version"], expected_version
        )
        self.assertEqual(
            outcome.events[-1]["payload"]["session_version"],
            int(ctx.session.session_version) + 1,
        )
        self.assertEqual(len(outcome.messages), 1)
        self.assertIn("第 1 轮闭环已完成：评分 96/100（待决策）", outcome.messages[0].content)
        self.assertEqual(outcome.usage.get("prompt_tokens"), 5 * PROMPT_TOKENS)
        self.assertEqual(outcome.usage.get("completion_tokens"), 5 * COMPLETION_TOKENS)

        # 产品状态：must 功能 → claims/options，一条 proposed 决策，无阻塞项
        state = outcome.product_state
        self.assertEqual(len(state.claims), 2)
        for claim in state.claims:
            self.assertEqual(claim.kind, C.ClaimKind.feature)
            self.assertEqual(claim.created_product_version, expected_version)
            self.assertEqual(claim.status, C.ClaimStatus.proposed)
            self.assertEqual(claim.role_code, None)
        self.assertEqual(len(state.options), 2)
        self.assertEqual(len(state.decisions), 1)
        self.assertEqual(state.decisions[0].status, C.DecisionStatus.proposed)
        self.assertEqual(state.unresolved_issues, [])
        self.assertEqual(state.source_run_id, self.run_id)

        # 提交后：版本、阶段 step、产物、Loop/Round 状态
        self.commit_outcome(outcome)
        self.assertEqual(
            self._session_row().current_product_version, expected_version
        )
        self.assertEqual(outcome.result_product_version, expected_version)

        steps = self._step_rows()
        self.assertEqual(len(steps), 5)
        keys = {step.logical_key for step in steps}
        self.assertEqual(
            keys,
            {loop_workflow.STAGE_KEYS[stage] for stage in LoopStage},
        )
        for step in steps:
            self.assertEqual(step.status, StepStatus.succeeded.value)
            self.assertEqual(step.kind, loop_workflow.LOOP_STEP_KIND)

        artifacts = self._artifact_rows()
        self.assertEqual(len(artifacts), 5)
        self.assertEqual(
            {artifact.kind for artifact in artifacts},
            {kind.value for kind in LoopArtifactKind},
        )

        loop = self._loop_row()
        self.assertEqual(loop.status, LoopStatus.awaiting_decision.value)
        self.assertEqual(loop.current_round, 1)
        # 建 Loop 时 version=1，排期 +1，apply +1
        self.assertEqual(loop.loop_version, 3)
        self.assertEqual(self._round_row().status, LoopRoundStatus.done.value)
        self.assertEqual(int(self._round_row().score), 96)

        run = self._run_row()
        self.assertEqual(run.status, RunStatus.succeeded.value)

        persisted_loop = self._event_payloads(C.EventType.loop_updated.value)
        self.assertEqual(len(persisted_loop), 5)
        persisted_state = self._event_payloads(C.EventType.product_state_updated.value)
        self.assertEqual(len(persisted_state), 1)
        self.assertEqual(persisted_state[0]["product_version"], expected_version)
        self.assertEqual(llm.purposes(), STAGE_PURPOSES)

    async def test_validate_blocker_makes_run_partial_but_continues(self) -> None:
        self._prepare_round()
        llm = FakeLLM(_script(validation=_validation_payload(fail_first=True)))
        ctx = self.make_ctx(llm=llm)
        outcome = await loop_workflow.run_loop_round(ctx)

        self.assertEqual(outcome.status, RunStatus.partial_succeeded)
        self.assertIsNotNone(outcome.product_state)
        # 五个阶段都执行了，validate 之后仍继续
        self.assertEqual(llm.purposes(), STAGE_PURPOSES)
        self.assertEqual(len(outcome.events), 6)
        self.assert_stage_events(outcome, expected_loop_status=LoopStatus.running)

        # validate 阶段事件标记 failed，其余为 done
        events = [
            event
            for event in outcome.events
            if event["type"] == C.EventType.loop_updated.value
        ]
        self.assertEqual(events[1]["payload"]["stage_status"], LoopStageStatus.failed.value)
        for index in (0, 2, 3, 4):
            self.assertEqual(events[index]["payload"]["stage_status"], LoopStageStatus.done.value)
        # 硬规则：1 项 p0 阻塞 → 96 被压到 70
        self.assertEqual(events[4]["payload"]["score"], 70)
        self.assertEqual(events[4]["payload"]["verdict"], LoopVerdict.iterate.value)
        self.assertIn("部分完成", outcome.messages[0].content)
        self.assertEqual(len(outcome.product_state.unresolved_issues), 1)

        self.commit_outcome(outcome)
        stage_rows = self._stage_rows()
        self.assertEqual(len(stage_rows), 5)
        statuses = {row.stage: row.status for row in stage_rows}
        self.assertEqual(statuses[LoopStage.validate.value], LoopStageStatus.failed.value)
        for stage in (LoopStage.ideate, LoopStage.develop, LoopStage.test, LoopStage.evaluate):
            self.assertEqual(statuses[stage.value], LoopStageStatus.done.value)

        steps = self._step_rows()
        validate_step = [
            step
            for step in steps
            if step.logical_key == loop_workflow.STAGE_KEYS[LoopStage.validate]
        ]
        self.assertEqual(len(validate_step), 1)
        self.assertEqual(validate_step[0].status, StepStatus.failed.value)

        loop = self._loop_row()
        self.assertEqual(loop.status, LoopStatus.running.value)
        self.assertEqual(loop.loop_version, 3)
        round_row = self._round_row()
        self.assertEqual(round_row.status, LoopRoundStatus.done.value)
        self.assertEqual(int(round_row.score), 70)
        self.assertEqual(round_row.verdict, LoopVerdict.iterate.value)

        scorecards = [
            artifact
            for artifact in self._artifact_rows()
            if artifact.kind == LoopArtifactKind.scorecard.value
        ]
        self.assertEqual(len(scorecards), 1)
        payload = json.loads(scorecards[0].payload_json)
        self.assertEqual(payload["total"], 70)

    async def test_evaluate_blockers_hard_cap_model_score(self) -> None:
        self._prepare_round()
        scorecard = _scorecard_payload(
            blockers=["关键假设未验证", "缺少真实用户反馈"]
        )
        llm = FakeLLM(_script(scorecard=scorecard))
        ctx = self.make_ctx(llm=llm)
        outcome = await loop_workflow.run_loop_round(ctx)

        self.assertEqual(outcome.status, RunStatus.succeeded)
        self.assertEqual(len(outcome.product_state.unresolved_issues), 2)
        self.assert_stage_events(outcome, expected_loop_status=LoopStatus.running)
        evaluate_event = outcome.events[-2]["payload"]
        self.assertEqual(evaluate_event["score"], 70)
        self.assertEqual(evaluate_event["verdict"], LoopVerdict.iterate.value)

        self.commit_outcome(outcome)
        scorecards = [
            artifact
            for artifact in self._artifact_rows()
            if artifact.kind == LoopArtifactKind.scorecard.value
        ]
        self.assertEqual(len(scorecards), 1)
        payload = json.loads(scorecards[0].payload_json)
        self.assertEqual(payload["total"], 70)
        self.assertTrue(payload["hard_capped"])
        self.assertEqual(payload["generated_by"], "harness")
        self.assertEqual(len(payload["blockers"]), 2)
        # 维度仍被补全为 7 个；硬上限只压 total，不重写各维度得分
        self.assertEqual(len(payload["dimensions"]), len(C.LOOP_SCORE_MAX))
        self.assertGreaterEqual(
            sum(int(d["score"]) for d in payload["dimensions"]), payload["total"]
        )
        self.assertEqual(int(self._round_row().score), 70)

    async def test_exhausted_round_awaits_decision(self) -> None:
        self._prepare_round(max_rounds=1)
        scorecard = _scorecard_payload(scores=_LOW_SCORES)
        llm = FakeLLM(_script(scorecard=scorecard))
        ctx = self.make_ctx(llm=llm)
        outcome = await loop_workflow.run_loop_round(ctx)

        self.assertEqual(outcome.status, RunStatus.succeeded)
        self.assert_stage_events(outcome, expected_loop_status=LoopStatus.awaiting_decision)
        self.assertEqual(outcome.events[-2]["payload"]["score"], 40)
        self.assertEqual(outcome.events[-2]["payload"]["verdict"], LoopVerdict.iterate.value)
        self.assertIn("待决策", outcome.messages[0].content)

        self.commit_outcome(outcome)
        loop = self._loop_row()
        self.assertEqual(loop.status, LoopStatus.awaiting_decision.value)
        self.assertEqual(int(loop.max_rounds), 1)
        scorecards = [
            artifact
            for artifact in self._artifact_rows()
            if artifact.kind == LoopArtifactKind.scorecard.value
        ]
        payload = json.loads(scorecards[0].payload_json)
        self.assertIn("轮次已用尽，请决定交付或停止", payload["next_actions"])


class LoopWorkflowFailureTests(LoopWorkflowTestCase):
    async def test_budget_shortfall_fails_without_product_state(self) -> None:
        self._prepare_round()
        llm = FakeLLM(_script())
        ctx = self.make_ctx(llm=llm, budget=_TightBudget())
        base_version = int(ctx.run.base_product_version)
        outcome = await loop_workflow.run_loop_round(ctx)

        self.assertEqual(outcome.status, RunStatus.failed)
        self.assertEqual(outcome.error_code, ErrorCode.BUDGET_EXCEEDED)
        self.assertIsNone(outcome.product_state)
        self.assertEqual(outcome.events, [])
        self.assertEqual(llm.calls, [])
        self.assertIn("5 次上游调用", outcome.error_message)

        self.commit_outcome(outcome)
        run = self._run_row()
        self.assertEqual(run.status, RunStatus.failed.value)
        self.assertEqual(run.error_code, ErrorCode.BUDGET_EXCEEDED)
        loop = self._loop_row()
        self.assertEqual(loop.status, LoopStatus.failed.value)
        self.assertEqual(loop.loop_version, 3)
        self.assertEqual(self._round_row().status, LoopRoundStatus.failed.value)
        self.assertEqual(self._artifact_rows(), [])
        self.assertEqual(self._session_row().current_product_version, base_version)

    async def test_invalid_output_after_repair_fails_the_round(self) -> None:
        self._prepare_round()
        bad = {"title": "预算协同 MVP"}  # 缺 features → 结构校验失败
        llm = FakeLLM(
            {
                PURPOSE_IDEATE: [bad, bad],
                PURPOSE_VALIDATE: [_validation_payload()],
                PURPOSE_DEVELOP: [_prototype_payload()],
                PURPOSE_TEST: [_report_payload()],
                PURPOSE_EVALUATE: [_scorecard_payload()],
            }
        )
        ctx = self.make_ctx(llm=llm)
        outcome = await loop_workflow.run_loop_round(ctx)

        self.assertEqual(outcome.status, RunStatus.failed)
        self.assertEqual(outcome.error_code, ErrorCode.MODEL_OUTPUT_INVALID)
        self.assertIsNone(outcome.product_state)
        # 首轮 + 一次修复，且没有继续后面的阶段
        self.assertEqual(llm.purposes(), [PURPOSE_IDEATE, PURPOSE_IDEATE])
        self.assertEqual(outcome.usage.get("prompt_tokens"), 2 * PROMPT_TOKENS)

        self.commit_outcome(outcome)
        stage_rows = self._stage_rows()
        self.assertEqual(len(stage_rows), 1)
        self.assertEqual(stage_rows[0].stage, LoopStage.ideate.value)
        self.assertEqual(stage_rows[0].status, LoopStageStatus.failed.value)
        ideate_step = [
            step
            for step in self._step_rows()
            if step.logical_key == loop_workflow.STAGE_KEYS[LoopStage.ideate]
        ]
        self.assertEqual(ideate_step[0].status, StepStatus.failed.value)
        self.assertEqual(self._loop_row().status, LoopStatus.failed.value)
        self.assertEqual(self._round_row().status, LoopRoundStatus.failed.value)

    async def test_missing_loop_parameters_is_invalid_request(self) -> None:
        self._create_plain_run()
        llm = FakeLLM({})
        ctx = self.make_ctx(llm=llm)
        outcome = await loop_workflow.run_loop_round(ctx)

        self.assertEqual(outcome.status, RunStatus.failed)
        self.assertEqual(outcome.error_code, ErrorCode.INVALID_REQUEST)
        self.assertIsNone(outcome.product_state)
        self.assertEqual(llm.calls, [])
        self.assertIn("loop", outcome.error_message)
        self.commit_outcome(outcome)
        self.assertEqual(self._run_row().status, RunStatus.failed.value)


if __name__ == "__main__":
    unittest.main()
