"""``workflows.runner`` 与 ``workers.run_worker`` 的集成测试（真实 sqlite，不联网）。

覆盖验收点：
* 未知 / 已终结 Run 不产生新写入；
* 可重试上游错误最多两次尝试，之后 Run failed 且步骤 attempt 计数正确；
* 预算超限 → ``BUDGET_EXCEEDED`` 且不发模型请求；
* 幂等重放：已 succeeded 的步骤跳过（AC-09）、终态 Run 重复执行不重复写消息；
* 租约被抢占（开始前 / 执行中）→ 不提交结果、不写回；
* 恰好一个终结事件；
* 会话已删除 → cancelled(NOT_FOUND) 且不写回结果；
* cancel_requested → 提交 cancelled，不写产品状态（AC-30）；
* 未实现 task_kind → failed(FEATURE_NOT_READY) 且步骤可审计；
* Run 级截止 → failed(RUN_DEADLINE_EXCEEDED)；
* Worker ``--once`` 在无任务时正常退出。
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select, update  # noqa: E402

from core.llm_client import LLMResult, LLMUnavailableError, LLMUsage  # noqa: E402
from domain import contracts as C  # noqa: E402
from domain.contracts import (  # noqa: E402
    EventType,
    RunStatus,
    StepStatus,
    TaskKind,
    new_id,
    utc_now_iso,
)
from domain.errors import ErrorCode  # noqa: E402
from repositories import database, run_repo, session_repo  # noqa: E402
from repositories.database import EventRow, MessageRow, RunRow, StepRow  # noqa: E402
from services.command_service import OutgoingMessage, RunOutcome  # noqa: E402
from workflows import runner  # noqa: E402
from workflows.runner import BudgetTracker, execute_run  # noqa: E402
from workers.run_worker import RunWorker  # noqa: E402

WORKER = "test-worker"
OWNER = "owner@example.com"
TERMINAL_EVENT_TYPES = {event.value for event in C.TERMINAL_EVENT_TYPES}


# --------------------------------------------------------------------------- #
# 假 LLM
# --------------------------------------------------------------------------- #


class _StubLLM:
    """可注入的假 LLM：记录调用并按预设返回/抛错。"""

    def __init__(self, *, result: LLMResult | None = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict] = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def _llm_result(prompt: int = 7, completion: int = 3) -> LLMResult:
    return LLMResult(
        text="ok",
        usage=LLMUsage(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=prompt + completion,
            usage_source="provider",
        ),
        model="fake-model",
        latency_ms=1,
        attempts=1,
        finish_reason="stop",
    )


def _success_outcome(*messages: str) -> RunOutcome:
    return RunOutcome(
        status=RunStatus.succeeded,
        messages=[OutgoingMessage(content=text) for text in messages],
    )


# --------------------------------------------------------------------------- #
# 测试基类
# --------------------------------------------------------------------------- #


class RunnerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="runner-test-")
        db_path = Path(self._tmpdir, "t.db").as_posix()
        database.configure(f"sqlite:///{db_path}", force=True)
        database.initialize_database()
        self._dispatch_backup = dict(runner.WORKFLOW_DISPATCH)
        runner.WORKFLOW_DISPATCH.clear()

    def tearDown(self) -> None:
        runner.WORKFLOW_DISPATCH.clear()
        runner.WORKFLOW_DISPATCH.update(self._dispatch_backup)
        database.dispose_engine()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    # -- 数据构造 ---------------------------------------------------------- #

    def create_session(self) -> str:
        with database.session_scope() as db:
            session = session_repo.create_session(
                db,
                owner_email=OWNER,
                problem_statement="我想把这个问题想清楚",
            )
            return session.id

    def create_run(
        self,
        *,
        action: str = "clarify",
        budget: dict | None = None,
        deadline_at: int | None = None,
        session_id: str | None = None,
    ) -> tuple[str, str]:
        now = int(time.time())
        with database.session_scope() as db:
            if session_id is None:
                session = session_repo.create_session(
                    db, owner_email=OWNER, problem_statement="我想把这个问题想清楚"
                )
                session_id = session.id
            run = run_repo.create_run(
                db,
                session_id=session_id,
                owner_email=OWNER,
                request_id=new_id(),
                action=action,
                input_hash="input-hash",
                base_session_version=1,
                base_product_version=1,
                budget=budget or {},
                now_iso=utc_now_iso(),
                deadline_at=deadline_at if deadline_at is not None else now + 300,
                queue_deadline_at=now + 600,
                run_id=new_id(),
            )
            return session_id, run.id

    def claim(self, run_id: str) -> int:
        with database.session_scope() as db:
            claimed = run_repo.claim_next_run(
                db, worker_id=WORKER, now_epoch=int(time.time()), lease_seconds=30
            )
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.id, run_id)
        return int(claimed.lease_epoch)

    # -- 查询 -------------------------------------------------------------- #

    def get_run(self, run_id: str) -> RunRow:
        with database.session_scope() as db:
            row = db.get(RunRow, run_id)
            self.assertIsNotNone(row)
            return row

    def get_step(self, run_id: str, logical_key: str = "coach.clarify") -> StepRow | None:
        with database.session_scope() as db:
            return run_repo.get_step_by_key(db, run_id, logical_key)

    def events(self, run_id: str) -> list[EventRow]:
        with database.session_scope() as db:
            return list(run_repo.events_after(db, run_id, after_seq=0, limit=500))

    def terminal_events(self, run_id: str) -> list[EventRow]:
        return [e for e in self.events(run_id) if str(e.type) in TERMINAL_EVENT_TYPES]

    def message_count(self, session_id: str) -> int:
        with database.session_scope() as db:
            return int(
                db.execute(
                    select(func.count())
                    .select_from(MessageRow)
                    .where(MessageRow.session_id == session_id)
                ).scalar_one()
            )

    def total_events(self) -> int:
        with database.session_scope() as db:
            return int(db.execute(select(func.count()).select_from(EventRow)).scalar_one())

    # -- 执行 -------------------------------------------------------------- #

    def run_once(self, run_id: str, llm: object | None = None) -> None:
        execute_run(
            run_id,
            worker_id=WORKER,
            llm_factory=(lambda: llm) if llm is not None else None,
            renew_thread=False,
        )


# --------------------------------------------------------------------------- #
# 用例
# --------------------------------------------------------------------------- #


class NoopTests(RunnerTestCase):
    def test_unknown_run_writes_nothing(self) -> None:
        execute_run("missing-run", worker_id=WORKER, renew_thread=False)
        self.assertEqual(self.total_events(), 0)

    def test_terminal_run_is_not_re_executed(self) -> None:
        session_id, run_id = self.create_run()
        self.claim(run_id)
        calls: list[str] = []

        def dispatch(ctx):
            calls.append("called")
            return _success_outcome("澄清问题一")

        runner.WORKFLOW_DISPATCH[TaskKind.clarify] = dispatch
        self.run_once(run_id)

        run = self.get_run(run_id)
        self.assertEqual(run.status, RunStatus.succeeded.value)
        message_count = self.message_count(session_id)
        event_count = self.total_events()
        self.assertEqual(len(calls), 1)

        # 第二次执行：终态直接返回，不产生任何新写入。
        self.run_once(run_id)
        self.assertEqual(len(calls), 1, "终态 Run 不得再次调用工作流")
        self.assertEqual(self.message_count(session_id), message_count)
        self.assertEqual(self.total_events(), event_count)
        self.assertEqual(len(self.terminal_events(run_id)), 1)


class RetryTests(RunnerTestCase):
    def test_retryable_error_two_attempts_then_run_failed(self) -> None:
        _, run_id = self.create_run()
        self.claim(run_id)
        failing_llm = _StubLLM(error=LLMUnavailableError("upstream down"))

        async def dispatch(ctx):
            await ctx.llm.complete(
                messages=[{"role": "user", "content": "hi"}],
                max_output_tokens=100,
                timeout_seconds=5,
                purpose="test.retry",
            )
            return _success_outcome("不应到达")

        runner.WORKFLOW_DISPATCH[TaskKind.clarify] = dispatch
        self.run_once(run_id, llm=failing_llm)

        run = self.get_run(run_id)
        self.assertEqual(run.status, RunStatus.failed.value)
        self.assertEqual(run.error_code, "LLM_UNAVAILABLE")
        self.assertEqual(len(failing_llm.calls), 2, "单步骤最多两次上游尝试")

        step = self.get_step(run_id)
        self.assertIsNotNone(step)
        self.assertEqual(step.status, StepStatus.failed.value)
        self.assertEqual(int(step.attempt_count), 2)
        self.assertEqual(step.error_code, "LLM_UNAVAILABLE")
        self.assertEqual(len(self.terminal_events(run_id)), 1)

    def test_non_retryable_error_only_one_attempt(self) -> None:
        from core.llm_client import LLMRequestError

        _, run_id = self.create_run()
        self.claim(run_id)
        failing_llm = _StubLLM(error=LLMRequestError("bad key"))

        async def dispatch(ctx):
            return await ctx.llm.complete(
                messages=[{"role": "user", "content": "hi"}],
                max_output_tokens=10,
                timeout_seconds=5,
                purpose="test.norety",
            )

        runner.WORKFLOW_DISPATCH[TaskKind.clarify] = dispatch
        self.run_once(run_id, llm=failing_llm)

        run = self.get_run(run_id)
        self.assertEqual(run.status, RunStatus.failed.value)
        self.assertEqual(run.error_code, "LLM_REQUEST_ERROR")
        self.assertEqual(len(failing_llm.calls), 1)
        self.assertEqual(int(self.get_step(run_id).attempt_count), 1)


class BudgetTests(RunnerTestCase):
    def test_budget_exceeded_fails_without_model_call(self) -> None:
        _, run_id = self.create_run(budget={"max_upstream_calls": 0})
        self.claim(run_id)
        calls: list[str] = []
        llm = _StubLLM(result=_llm_result())

        def dispatch(ctx):
            calls.append("called")
            return _success_outcome("不应到达")

        runner.WORKFLOW_DISPATCH[TaskKind.clarify] = dispatch
        self.run_once(run_id, llm=llm)

        run = self.get_run(run_id)
        self.assertEqual(run.status, RunStatus.failed.value)
        self.assertEqual(run.error_code, ErrorCode.BUDGET_EXCEEDED)
        self.assertEqual(calls, [], "预算不足时不得调用工作流")
        self.assertEqual(llm.calls, [], "预算不足时不得发模型请求")
        self.assertEqual(len(self.terminal_events(run_id)), 1)

    def test_upstream_call_budget_rejects_eleventh_call(self) -> None:
        tracker = BudgetTracker(max_upstream_calls=10)
        for _ in range(10):
            tracker.begin_call(input_tokens=0, output_tokens=0)
        with self.assertRaises(runner.BudgetExceededError) as ctx:
            tracker.begin_call(input_tokens=0, output_tokens=0)
        self.assertEqual(ctx.exception.code, ErrorCode.BUDGET_EXCEEDED)


class ReplayTests(RunnerTestCase):
    """SRV-02：步骤成功必须与结果提交原子生效；旧版本残留只能重跑，不能伪造成功。"""

    def test_successful_run_marks_step_committed_inside_the_commit(self) -> None:
        session_id, run_id = self.create_run()
        self.claim(run_id)

        calls: list[str] = []

        def dispatch(ctx):
            calls.append("called")
            return _success_outcome("澄清问题一")

        runner.WORKFLOW_DISPATCH[TaskKind.clarify] = dispatch
        self.run_once(run_id)

        run = self.get_run(run_id)
        self.assertEqual(run.status, RunStatus.succeeded.value)
        step = self.get_step(run_id)
        self.assertIsNotNone(step)
        self.assertEqual(step.status, StepStatus.succeeded.value)
        output = json.loads(step.output_json or "{}")
        self.assertTrue(output.get("committed"), "步骤成功必须由提交事务写入（committed=true）")
        self.assertEqual(output.get("message_count"), 1)
        self.assertEqual(len(calls), 1)

        # 终态 Run 重复执行：不重调模型、不重复写消息、仍只有一个终结事件（AC-09）。
        messages = self.message_count(session_id)
        self.run_once(run_id)
        self.assertEqual(len(calls), 1, "终态 Run 不得再次调用工作流")
        self.assertEqual(self.message_count(session_id), messages)
        self.assertEqual(len(self.terminal_events(run_id)), 1)

    def test_legacy_succeeded_step_without_result_is_reexecuted(self) -> None:
        """旧版本崩溃残留（step=succeeded 但 Run 未终结）必须重跑而非空成功（SRV-02）。"""

        session_id, run_id = self.create_run()
        self.claim(run_id)

        with database.session_scope() as db:
            run = run_repo.get_run(db, run_id)
            step = run_repo.ensure_step(
                db, run, logical_key="coach.clarify", kind=TaskKind.clarify.value
            )
            run_repo.start_step(db, step, now_iso=utc_now_iso())
            run_repo.finish_step(
                db, step, status=StepStatus.succeeded, output={"ok": True}, now_iso=utc_now_iso()
            )

        calls: list[str] = []

        def dispatch(ctx):
            calls.append("called")
            return _success_outcome("重新执行后的真实回复")

        runner.WORKFLOW_DISPATCH[TaskKind.clarify] = dispatch
        self.run_once(run_id)

        self.assertEqual(calls, ["called"], "旧残留必须重跑工作流，不能提交空 outcome")
        run = self.get_run(run_id)
        self.assertEqual(run.status, RunStatus.succeeded.value)
        self.assertEqual(self.message_count(session_id), 2, "必须真的写入 assistant 消息")
        self.assertEqual(self.get_step(run_id).status, StepStatus.succeeded.value)
        self.assertEqual(len(self.terminal_events(run_id)), 1)


class LeaseTests(RunnerTestCase):
    def test_lease_taken_before_start_writes_nothing(self) -> None:
        _, run_id = self.create_run()
        self.claim(run_id)

        with database.session_scope() as db:
            db.execute(
                update(RunRow)
                .where(RunRow.id == run_id)
                .values(lease_owner="other-worker", lease_epoch=RunRow.lease_epoch + 1)
            )
        before = self.total_events()

        calls: list[str] = []

        def dispatch(ctx):  # pragma: no cover - 不应被调用
            calls.append("called")
            return _success_outcome("不应写入")

        runner.WORKFLOW_DISPATCH[TaskKind.clarify] = dispatch
        self.run_once(run_id)

        run = self.get_run(run_id)
        self.assertEqual(run.status, RunStatus.running.value)
        self.assertEqual(run.lease_owner, "other-worker")
        self.assertEqual(self.total_events(), before, "租约不属于本 Worker 时不得写入")
        self.assertEqual(len(self.terminal_events(run_id)), 0)
        self.assertEqual(calls, [])

    def test_renew_thread_detects_lease_loss_and_skips_commit(self) -> None:
        """AC-08：续租线程发现租约被接管后，主流程不得提交结果。"""
        _, run_id = self.create_run()
        self.claim(run_id)
        before_messages = self.message_count(self.get_run(run_id).session_id)
        original_interval = C.LEASE_RENEW_SECONDS
        C.LEASE_RENEW_SECONDS = 0.05
        try:

            def steal_lease() -> None:
                with database.session_scope() as db:
                    db.execute(
                        update(RunRow)
                        .where(RunRow.id == run_id)
                        .values(
                            lease_owner="other-worker", lease_epoch=RunRow.lease_epoch + 1
                        )
                    )

            async def dispatch(ctx):
                steal_lease()
                await asyncio.sleep(0.4)  # 留出至少一个续约周期，让续约线程发现丢失
                return _success_outcome("不应写入")

            runner.WORKFLOW_DISPATCH[TaskKind.clarify] = dispatch
            execute_run(run_id, worker_id=WORKER, renew_thread=True)
        finally:
            C.LEASE_RENEW_SECONDS = original_interval

        run = self.get_run(run_id)
        self.assertEqual(run.status, RunStatus.running.value, "租约丢失后不得终结 Run")
        self.assertEqual(run.lease_owner, "other-worker")
        self.assertEqual(len(self.terminal_events(run_id)), 0)
        self.assertEqual(self.message_count(run.session_id), before_messages)


class CommitTests(RunnerTestCase):
    def test_success_writes_exactly_one_terminal_event_and_usage(self) -> None:
        session_id, run_id = self.create_run()
        self.claim(run_id)
        llm = _StubLLM(result=_llm_result(prompt=7, completion=3))
        seen: dict[str, object] = {}

        async def dispatch(ctx):
            seen["message"] = ctx.current_message
            seen["product_version"] = ctx.product_state.version
            await ctx.llm.complete(
                messages=[{"role": "user", "content": "hi"}],
                max_output_tokens=50,
                timeout_seconds=5,
                purpose="test.success",
            )
            return _success_outcome("澄清问题一", "澄清问题二")

        runner.WORKFLOW_DISPATCH[TaskKind.clarify] = dispatch
        self.run_once(run_id, llm=llm)

        # StepContext 必须拿到触发命令的人类消息（其 run_id 为空，不能靠 Run 快照）。
        current = seen.get("message")
        self.assertIsNotNone(current, "StepContext.current_message 不应为空")
        self.assertEqual(str(current.sender_kind), "human")
        self.assertIn("我想把这个问题想清楚", current.content)
        self.assertEqual(seen.get("product_version"), 1)

        run = self.get_run(run_id)
        self.assertEqual(run.status, RunStatus.succeeded.value)
        self.assertIsNone(run.lease_owner)
        self.assertIsNone(run.lease_expires_at)
        self.assertEqual(len(self.terminal_events(run_id)), 1)
        self.assertEqual(self.message_count(session_id), 3)  # 初始人类消息 + 2 条 agent 消息

        usage = json.loads(run.usage_json or "{}")
        self.assertEqual(usage.get("upstream_calls"), 1)
        self.assertEqual(usage.get("prompt_tokens"), 7)
        self.assertEqual(usage.get("completion_tokens"), 3)

        step = self.get_step(run_id)
        self.assertEqual(step.status, StepStatus.succeeded.value)
        self.assertEqual(int(step.attempt_count), 1)

    def test_session_deleted_cancels_run_without_results(self) -> None:
        session_id, run_id = self.create_run()
        self.claim(run_id)
        with database.session_scope() as db:
            session = session_repo.get_session(db, session_id)
            session_repo.soft_delete_session(db, session, now_iso=utc_now_iso())
        before_messages = self.message_count(session_id)
        before_events = self.total_events()

        calls: list[str] = []

        def dispatch(ctx):  # pragma: no cover - 不应被调用
            calls.append("called")
            return _success_outcome("不应写入")

        runner.WORKFLOW_DISPATCH[TaskKind.clarify] = dispatch
        self.run_once(run_id)

        run = self.get_run(run_id)
        self.assertEqual(run.status, RunStatus.cancelled.value)
        self.assertEqual(run.error_code, ErrorCode.NOT_FOUND)
        self.assertEqual(calls, [])
        self.assertEqual(self.message_count(session_id), before_messages)
        self.assertEqual(self.total_events(), before_events + 1, "只允许写一个终结事件")
        self.assertEqual(len(self.terminal_events(run_id)), 1)

    def test_cancel_requested_commits_cancelled_without_product_state(self) -> None:
        session_id, run_id = self.create_run()
        self.claim(run_id)
        with database.session_scope() as db:
            db.execute(
                update(RunRow)
                .where(RunRow.id == run_id)
                .values(status=RunStatus.cancel_requested.value)
            )
        before_messages = self.message_count(session_id)

        def dispatch(ctx):  # pragma: no cover - 不应被调用
            return _success_outcome("不应写入")

        runner.WORKFLOW_DISPATCH[TaskKind.clarify] = dispatch
        self.run_once(run_id)

        run = self.get_run(run_id)
        self.assertEqual(run.status, RunStatus.cancelled.value)
        self.assertEqual(self.message_count(session_id), before_messages)
        terminal = self.terminal_events(run_id)
        self.assertEqual(len(terminal), 1)
        self.assertEqual(str(terminal[0].type), EventType.run_cancelled.value)


class FeatureGateTests(RunnerTestCase):
    def test_unimplemented_task_kind_fails_but_records_step(self) -> None:
        # M1/M2 已注册 clarify / brainstorm / audit_*；rebuild_legacy 仍未实现，
        # 用它验证“未注册任务类型”的 FEATURE_NOT_READY 兜底与可审计步骤。
        _, run_id = self.create_run(action="rebuild_legacy")
        self.claim(run_id)

        self.run_once(run_id)

        run = self.get_run(run_id)
        self.assertEqual(run.status, RunStatus.failed.value)
        self.assertEqual(run.error_code, ErrorCode.FEATURE_NOT_READY)
        step = self.get_step(run_id, "legacy.rebuild")
        self.assertIsNotNone(step, "未实现的任务也必须留下可审计步骤")
        self.assertEqual(step.status, StepStatus.failed.value)
        self.assertEqual(step.error_code, ErrorCode.FEATURE_NOT_READY)
        self.assertEqual(len(self.terminal_events(run_id)), 1)

    def test_run_deadline_exceeded_before_step(self) -> None:
        _, run_id = self.create_run(deadline_at=int(time.time()) - 10)
        self.claim(run_id)
        calls: list[str] = []

        def dispatch(ctx):  # pragma: no cover - 不应被调用
            calls.append("called")
            return _success_outcome("不应写入")

        runner.WORKFLOW_DISPATCH[TaskKind.clarify] = dispatch
        self.run_once(run_id)

        run = self.get_run(run_id)
        self.assertEqual(run.status, RunStatus.failed.value)
        self.assertEqual(run.error_code, ErrorCode.RUN_DEADLINE_EXCEEDED)
        self.assertEqual(calls, [])
        self.assertEqual(len(self.terminal_events(run_id)), 1)


class ExceptionTests(RunnerTestCase):
    def test_uncaught_exception_finalizes_run_as_internal(self) -> None:
        _, run_id = self.create_run()
        self.claim(run_id)

        async def dispatch(ctx):
            raise RuntimeError("boom " + "x" * 800)

        runner.WORKFLOW_DISPATCH[TaskKind.clarify] = dispatch
        self.run_once(run_id)

        run = self.get_run(run_id)
        self.assertEqual(run.status, RunStatus.failed.value)
        self.assertEqual(run.error_code, ErrorCode.INTERNAL)
        self.assertIsNotNone(run.error_message)
        self.assertLessEqual(len(run.error_message or ""), 500, "error_message 必须截断 500 字")
        self.assertIn("RuntimeError", run.error_message or "")
        self.assertEqual(len(self.terminal_events(run_id)), 1, "未捕获异常也必须恰好一个终结事件")
        step = self.get_step(run_id)
        self.assertEqual(step.status, StepStatus.failed.value)
        self.assertEqual(step.error_code, ErrorCode.INTERNAL)


class WorkerTests(RunnerTestCase):
    def test_worker_once_exits_cleanly_without_tasks(self) -> None:
        worker = RunWorker(worker_id="w-once", interval=0.1)
        exit_code = worker.run(once=True)
        self.assertEqual(exit_code, 0)
        self.assertEqual(worker.processed, 0)
        self.assertTrue(worker.stopping is False)

    def test_worker_once_executes_queued_run(self) -> None:
        _, run_id = self.create_run()
        worker = RunWorker(worker_id="w-once-2", interval=0.1)
        llm = _StubLLM(result=_llm_result())

        async def dispatch(ctx):
            await ctx.llm.complete(
                messages=[{"role": "user", "content": "hi"}],
                max_output_tokens=20,
                timeout_seconds=5,
                purpose="test.worker",
            )
            return _success_outcome("worker 输出")

        runner.WORKFLOW_DISPATCH[TaskKind.clarify] = dispatch

        # Worker 内部 execute_run 不接收 llm 注入，直接 monkeypatch 模块函数。
        original_execute = runner.execute_run

        def fake_execute(rid, *, worker_id, **kwargs):
            return original_execute(rid, worker_id=worker_id, llm_factory=lambda: llm, renew_thread=False)

        runner.execute_run = fake_execute
        try:
            worker.run(once=True)
        finally:
            runner.execute_run = original_execute

        run = self.get_run(run_id)
        self.assertEqual(run.status, RunStatus.succeeded.value)
        self.assertEqual(worker.processed, 1)
        self.assertEqual(self.message_count(run.session_id), 2)


if __name__ == "__main__":
    unittest.main()
