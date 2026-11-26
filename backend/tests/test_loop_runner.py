"""``workflows.runner`` 驱动的 loop 闭环端到端测试（真实 sqlite + 假 LLM）。

与前两个测试文件不同，这里不手工调用 ``run_loop_round``，而是走真实
「命令 → Run → 领取 → ``execute_run`` → 提交」路径，验证：

* ``WORKFLOWS[TaskKind.loop_round]`` 注册可被 ``_resolve_dispatch`` 解析；
* 事件序列：``run.queued`` / ``run.started`` / ``step.started`` ×6
  （顶层 ``loop.round`` + 五个 ``loop.stage.*``）/ ``loop.updated`` ×5 /
  ``product_state.updated`` / ``run.completed``；
* 恰好一个终结事件；
* ``runs.usage_json`` 记录真实上游调用次数；
* ``loop_rounds`` 行 ``done`` + 分数，``loop_artifacts`` 五条；
* 产品状态 v+1、loop 进入 ``awaiting_decision``、``loop_version`` 递进；
* 摘要消息写入会话。
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
import unittest

from sqlalchemy import select

from domain import contracts as C
from domain.contracts import (
    Action,
    CommandRequest,
    EventType,
    LoopArtifactKind,
    LoopRoundStatus,
    LoopStage,
    LoopStatus,
    RunStatus,
    StepStatus,
    TaskKind,
)
from repositories import database, loop_repo, run_repo, session_repo
from repositories.database import MessageRow, RunRow, session_scope
from services import command_service
from tests.test_loop_workflow import (
    NOW_ISO,
    OWNER,
    WORKER,
    FakeLLM,
    _script,
)
from workflows import loop as loop_workflow
from workflows import runner
from workflows.runner import execute_run

TERMINAL_EVENT_TYPES = {event.value for event in C.TERMINAL_EVENT_TYPES}
STAGE_LOGICAL_KEYS = [f"loop.stage.{stage.value}" for stage in LoopStage]


class LoopRunnerTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.makedirs(".tmp_tests", exist_ok=True)
        cls._tmpdir = tempfile.mkdtemp(prefix="loop_runner_", dir=os.path.abspath(".tmp_tests"))
        db_path = os.path.join(cls._tmpdir, "loop_runner.db").replace("\\", "/")
        database.configure("sqlite+pysqlite:///" + db_path, force=True)
        database.initialize_database(database.get_engine(), drop_first=True)

    @classmethod
    def tearDownClass(cls) -> None:
        database.dispose_engine()
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def setUp(self) -> None:
        self._dispatch_backup = dict(runner.WORKFLOW_DISPATCH)
        runner.WORKFLOW_DISPATCH.clear()
        self.db = database.new_session()
        self._clock = {"epoch": int(time.time())}
        self._cancel_open_runs()
        self.session_id = self._new_session()
        self._advance_to_brainstorming()
        self.loop_id = ""
        self.run_id = ""
        self.lease_epoch = 0

    def tearDown(self) -> None:
        self._cancel_open_runs()
        try:
            self.db.close()
        finally:
            runner.WORKFLOW_DISPATCH.clear()
            runner.WORKFLOW_DISPATCH.update(self._dispatch_backup)

    # -- 夹具 ------------------------------------------------------------- #

    def _cancel_open_runs(self) -> None:
        with session_scope() as db:
            rows = db.execute(select(RunRow).where(RunRow.owner_email == OWNER)).scalars().all()
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
                problem_statement="loop runner 验收：预算协同工具",
                now_iso=NOW_ISO,
            )
            return session.id

    def _submit(self, action: Action, payload: dict, *, version: int | None = None):
        self._clock["epoch"] += 70
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
                now_epoch=self._clock["epoch"],
            )

    def _advance_to_brainstorming(self) -> None:
        with session_scope() as db:
            state = session_repo.get_product_state(db, self.session_id)
            self._submit(
                Action.revise_brief,
                {
                    "expected_product_version": state.version,
                    "fields": {
                        "target_users": {"value": "中小企业财务团队", "status": "proposed"},
                        "usage_scenario": {"value": "月度预算协同与审批", "status": "proposed"},
                        "core_problem": {"value": "预算协作靠表格来回传", "status": "proposed"},
                        "success_criteria": {"value": "预算对齐时间缩短一半", "status": "proposed"},
                    },
                },
            )
            state = session_repo.get_product_state(db, self.session_id)
            self._submit(
                Action.confirm_brief,
                {"expected_product_version": state.version, "fields": {}},
            )

    def _prepare_round(self) -> None:
        with session_scope() as db:
            state = session_repo.get_product_state(db, self.session_id)
        receipt = self._submit(
            Action.start_loop,
            {
                "scope": "mvp",
                "max_rounds": 3,
                "target_score": 85,
                "focus": "先做预算协同主流程",
                "expected_product_version": state.version,
            },
        )
        self.loop_id = str(receipt.loop_id)
        self.run_id = str(receipt.run_id)
        claimed = run_repo.claim_next_run(
            self.db,
            worker_id=WORKER,
            now_epoch=int(time.time()),
            lease_seconds=120,
        )
        self.assertIsNotNone(claimed)
        self.assertEqual(str(claimed.id), self.run_id)
        self.lease_epoch = int(claimed.lease_epoch)
        self.db.commit()

    # -- 查询 ------------------------------------------------------------- #

    def _events(self) -> list:
        with session_scope() as db:
            return list(run_repo.events_after(db, self.run_id, after_seq=0, limit=500))

    def _run_row(self) -> RunRow:
        with session_scope() as db:
            row = db.get(RunRow, self.run_id)
            self.assertIsNotNone(row)
            db.expunge(row)
            return row

    def _step_row(self, logical_key: str):
        with session_scope() as db:
            row = run_repo.get_step_by_key(db, self.run_id, logical_key)
            self.assertIsNotNone(row)
            db.expunge(row)
            return row

    def _loop_row(self):
        with session_scope() as db:
            row = loop_repo.get_loop(db, self.loop_id)
            self.assertIsNotNone(row)
            db.expunge(row)
            return row

    def _round_row(self, ordinal: int = 1):
        with session_scope() as db:
            row = loop_repo.get_round(db, loop_id=self.loop_id, ordinal=ordinal)
            self.assertIsNotNone(row)
            db.expunge(row)
            return row

    def _artifact_rows(self) -> list:
        with session_scope() as db:
            rows = list(loop_repo.list_artifacts(db, loop_id=self.loop_id, ordinal=1))
            for row in rows:
                db.expunge(row)
            return rows


class LoopRunnerRegistrationTests(LoopRunnerTestCase):
    def test_loop_round_is_registered_for_runner(self) -> None:
        self.assertEqual(
            runner.WORKFLOWS[TaskKind.loop_round], ("workflows.loop", "run_loop_round")
        )
        self.assertEqual(runner.ACTION_TASK_KIND[Action.start_loop], TaskKind.loop_round)
        self.assertEqual(runner.ACTION_TASK_KIND[Action.loop_decision], TaskKind.loop_round)
        self.assertEqual(runner.LOGICAL_KEYS[TaskKind.loop_round], "loop.round")

        dispatch = runner._resolve_dispatch(TaskKind.loop_round)
        self.assertIsNotNone(dispatch)
        self.assertTrue(callable(dispatch))
        self.assertIs(dispatch, loop_workflow.run_loop_round)
        self.assertIs(runner.WORKFLOW_DISPATCH[TaskKind.loop_round], loop_workflow.run_loop_round)


class LoopRunnerEndToEndTests(LoopRunnerTestCase):
    def test_full_round_through_runner(self) -> None:
        self._prepare_round()
        llm = FakeLLM(_script())
        runner.WORKFLOW_DISPATCH[TaskKind.loop_round] = loop_workflow.run_loop_round
        execute_run(
            self.run_id,
            worker_id=WORKER,
            llm_factory=lambda: llm,
            renew_thread=False,
        )

        # -- Run 终态与用量账本 ------------------------------------------- #
        run = self._run_row()
        self.assertEqual(run.status, RunStatus.succeeded.value)
        self.assertIsNone(run.error_code)
        usage = json.loads(run.usage_json or "{}")
        self.assertEqual(int(usage["calls"]), 5)
        self.assertEqual(int(usage["upstream_calls"]), 5)
        self.assertFalse(bool(usage["exceeded"]))
        self.assertEqual(int(usage["prompt_tokens"]), 5 * 120)
        self.assertEqual(int(usage["completion_tokens"]), 5 * 60)
        self.assertEqual(len(llm.calls), 5)

        # -- 事件序列 ------------------------------------------------------ #
        events = self._events()
        types = [str(event.type) for event in events]
        self.assertEqual(types[0], EventType.run_queued.value)
        self.assertEqual(types[1], EventType.run_started.value)
        self.assertEqual(types.count(EventType.step_started.value), 6)
        self.assertEqual(types.count(EventType.loop_updated.value), 5)
        self.assertEqual(types.count(EventType.product_state_updated.value), 1)
        self.assertEqual(types[-1], EventType.run_completed.value)

        started_keys = [
            json.loads(event.payload_json)["logical_key"]
            for event in events
            if str(event.type) == EventType.step_started.value
        ]
        self.assertEqual(started_keys, ["loop.round"] + STAGE_LOGICAL_KEYS)

        terminal = [
            event for event in events if str(event.type) in TERMINAL_EVENT_TYPES
        ]
        self.assertEqual(len(terminal), 1, "每个 Run 必须恰好一个终结事件")
        self.assertEqual(
            json.loads(terminal[0].payload_json)["status"], RunStatus.succeeded.value
        )

        # -- 步骤行 -------------------------------------------------------- #
        top_step = self._step_row("loop.round")
        self.assertEqual(top_step.status, StepStatus.succeeded.value)
        self.assertEqual(int(top_step.attempt_count), 1)
        for key in STAGE_LOGICAL_KEYS:
            stage_step = self._step_row(key)
            self.assertEqual(stage_step.status, StepStatus.succeeded.value, key)
            self.assertEqual(int(stage_step.attempt_count), 1, key)

        # -- Loop / Round / 产物 ------------------------------------------ #
        loop = self._loop_row()
        self.assertEqual(loop.status, LoopStatus.awaiting_decision.value)
        self.assertEqual(int(loop.current_round), 1)
        self.assertEqual(str(loop.latest_run_id), self.run_id)
        self.assertEqual(int(loop.loop_version), 3, "排期 +1、提交 +1")

        round_row = self._round_row()
        self.assertEqual(round_row.status, LoopRoundStatus.done.value)
        self.assertEqual(int(round_row.score), 96)
        self.assertEqual(str(round_row.verdict), C.LoopVerdict.pass_.value)

        artifacts = self._artifact_rows()
        self.assertEqual(len(artifacts), 5)
        self.assertNotIn(
            LoopArtifactKind.sandbox_run.value,
            {str(artifact.kind) for artifact in artifacts},
            "沙箱产物只由 /sandbox-runs 追加，回合内不应出现",
        )
        self.assertEqual(
            {str(artifact.kind) for artifact in artifacts},
            {
                LoopArtifactKind.mvp_spec.value,
                LoopArtifactKind.validation.value,
                LoopArtifactKind.prototype.value,
                LoopArtifactKind.test_report.value,
                LoopArtifactKind.scorecard.value,
            },
        )
        scorecard = next(
            artifact for artifact in artifacts if artifact.kind == LoopArtifactKind.scorecard.value
        )
        scorecard_payload = json.loads(scorecard.payload_json)
        self.assertEqual(int(scorecard_payload["total"]), 96)
        self.assertFalse(bool(scorecard_payload["hard_capped"]))

        # -- 产品状态 / 会话 ---------------------------------------------- #
        with session_scope() as db:
            state = session_repo.get_product_state(db, self.session_id)
            session = session_repo.get_session(db, self.session_id)
        self.assertEqual(int(state.version), int(run.base_product_version) + 1)
        feature_claims = [claim for claim in state.claims if str(claim.kind) == "feature"]
        self.assertEqual(len(feature_claims), 2)
        self.assertEqual(len(state.options), 2)
        self.assertEqual(len(state.decisions), 1)
        self.assertIsNone(session.active_run_id)
        # 排期时 command_service 已 bump 一次，提交结果再 bump 一次。
        self.assertEqual(int(session.session_version), int(run.base_session_version) + 2)

        # -- 摘要消息 ------------------------------------------------------ #
        with session_scope() as db:
            messages = (
                db.execute(select(MessageRow).where(MessageRow.run_id == self.run_id))
                .scalars()
                .all()
            )
        self.assertEqual(len(messages), 1)
        self.assertIn("第 1 轮闭环已完成：评分 96/100（待决策）", messages[0].content)
        self.assertEqual(str(messages[0].sender_kind), "agent")

        # -- 再跑一次：终态 Run 不重复执行 --------------------------------- #
        execute_run(
            self.run_id,
            worker_id=WORKER,
            llm_factory=lambda: llm,
            renew_thread=False,
        )
        self.assertEqual(len(self._events()), len(events))
        self.assertEqual(len(llm.calls), 5)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
