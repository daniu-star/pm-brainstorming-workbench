"""AC-10：模型已响应但结果提交前进程崩溃 —— 预留成本不丢、总尝试 <=2、结果只应用一次。

判据（PRD）：恢复可能重调但总尝试<=2、预留成本不丢、最终结果仅应用一次。

这里的「预留成本不丢」不是指 ``usage_json`` 里有最终数字，而是：执行器在
**发上游请求之前** 就把 ``BudgetTracker`` 的预留额度落盘（``BudgetedLLM`` 的
``on_reserve`` 钩子）。进程在「模型已响应、结果未提交」处崩溃后，接管者的
账本会把上一 epoch 的预留按已消耗处理，于是：

1. 崩溃那一次调用不会被抹掉（``upstream_calls`` 从 1 继续，上限 2 仍成立）；
2. 崩溃后再跑一次成功，累计调用次数恰好 2，不会出现「崩溃几次就白嫖几次」；
3. 结果仍然只应用一次（一条助手消息、一个终结事件、产品版本只推进一次）。

真正的 ``os._exit(9)`` 子进程是必要的：只有跨进程 + 真事务边界才能证明
「预留已落盘、结果未提交」这个中间态。
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:  # pragma: no cover - 直接运行单文件时兜底
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import func, select, update  # noqa: E402

from domain import contracts as C  # noqa: E402
from repositories import database, run_repo, session_repo  # noqa: E402
from repositories.database import MessageRow, ProductStateRow, RunRow, StepRow  # noqa: E402
from services.command_service import OutgoingMessage, RunOutcome  # noqa: E402
from workflows import runner  # noqa: E402
from workflows.runner import BudgetTracker, execute_run  # noqa: E402

OWNER = "budget-crash@example.com"
CRASH_WORKER = "budget-crash-worker"
TAKEOVER_WORKER = "budget-takeover-worker"

# 子进程：抢租约 → 走 BudgetedLLM 调一次模型（预留会先落盘）→ 结果提交前 os._exit(9)。
_CHILD = r"""
import os

from repositories import database

database.configure(os.environ["DATABASE_URL"], force=True)

from core.llm_client import LLMResult, LLMUsage
from domain import contracts as C
from repositories import run_repo
from workflows import runner


class _CrashLLM:
    # 模型正常响应；崩溃点在响应之后、结果提交之前。

    async def complete(self, **kwargs):
        return await self.complete_json(**kwargs)

    async def complete_json(self, *, messages, **kwargs):
        usage = LLMUsage(
            prompt_tokens=10, completion_tokens=5, total_tokens=15, usage_source="provider"
        )
        result = LLMResult(
            text="ok",
            usage=usage,
            model="fake-crash-model",
            latency_ms=1,
            attempts=1,
            finish_reason="stop",
        )
        return {"ok": True}, result


async def _dispatch(ctx):
    await ctx.llm.complete_json(
        messages=[{"role": "user", "content": "预留给崩溃那次调用的上下文 " * 60}],
        purpose="coach.clarify",
    )
    # 模型已响应（预留已落盘），但结果还没提交：进程在此处消失。
    os._exit(9)


runner.WORKFLOW_DISPATCH[C.TaskKind.clarify] = _dispatch

with database.session_scope() as db:
    claimed = run_repo.claim_next_run(
        db, worker_id=os.environ["WORKER"], now_epoch=int(__import__("time").time())
    )
if claimed is None:
    raise SystemExit("claim failed")

runner.execute_run(claimed.id, worker_id=os.environ["WORKER"], llm_factory=_CrashLLM, renew_thread=False)
print("CHILD_FINISHED_WITHOUT_CRASH")
"""


class _OkLLM:
    """接管后重跑用的模型：正常返回，记录调用次数。"""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, **kwargs):
        return await self.complete_json(**kwargs)

    async def complete_json(self, *, messages, **kwargs):
        from core.llm_client import LLMResult, LLMUsage

        self.calls += 1
        usage = LLMUsage(prompt_tokens=7, completion_tokens=3, total_tokens=10, usage_source="provider")
        result = LLMResult(
            text="ok",
            usage=usage,
            model="fake-ok-model",
            latency_ms=1,
            attempts=1,
            finish_reason="stop",
        )
        return {"ok": True}, result


class BudgetLedgerUnitTests(unittest.TestCase):
    """账本自身的语义：预留写入 usage_json 后必须按已消耗恢复。"""

    def test_as_dict_records_in_flight_reservation(self) -> None:
        budget = BudgetTracker()
        budget.begin_call(input_tokens=120, output_tokens=40)
        snapshot = budget.as_dict()
        self.assertEqual(snapshot["reserved_prompt_tokens"], 120)
        self.assertEqual(snapshot["reserved_completion_tokens"], 40)
        self.assertEqual(snapshot["upstream_calls"], 1)
        # 预留仍占用剩余额度（结算前不能重复花）
        self.assertEqual(budget.remaining_input_tokens, budget.max_run_input_tokens - 120)

    def test_restore_counts_previous_reservation_as_spent(self) -> None:
        budget = BudgetTracker()
        reservation = budget.begin_call(input_tokens=120, output_tokens=40)
        snapshot = budget.as_dict()

        restored = BudgetTracker.from_budget_json(None, json.dumps(snapshot))
        self.assertEqual(restored.input_tokens, 120)
        self.assertEqual(restored.output_tokens, 40)
        self.assertEqual(restored.upstream_calls, 1)
        self.assertFalse(restored.exceeded)

        budget.finish_call(reservation, None)
        self.assertEqual(budget.as_dict()["reserved_prompt_tokens"], 0)


async def _takeover_dispatch(ctx):
    """接管后的确定性工作流：只调一次模型，提交一条回复。"""

    await ctx.llm.complete_json(
        messages=[{"role": "user", "content": "接管后的重调 " * 20}], purpose="coach.clarify"
    )
    state = ctx.product_state
    return RunOutcome(
        status=C.RunStatus.succeeded,
        product_state=state.model_copy(update={"version": int(state.version) + 1}),
        messages=[OutgoingMessage(content="恢复后的助手回复")],
    )


class BudgetReservationCrashTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp_root = BACKEND / ".tmp_tests"
        tmp_root.mkdir(exist_ok=True)
        self.tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="budget_crash_", dir=str(tmp_root)))
        self.db_url = "sqlite:///" + (self.tmpdir / "budget.db").as_posix()
        database.configure(self.db_url, force=True)
        database.initialize_database()

        with database.session_scope() as db:
            session = session_repo.create_session(
                db,
                owner_email=OWNER,
                problem_statement="AC-10：崩溃前预留预算不得丢失",
                now_iso=C.utc_now_iso(),
            )
            self.session_id = session.id

        now = int(time.time())
        with database.session_scope() as db:
            self.run_id = run_repo.create_run(
                db,
                session_id=self.session_id,
                owner_email=OWNER,
                request_id=C.new_id(),
                action=C.Action.clarify.value,
                input_hash="budget-crash-hash",
                base_session_version=1,
                base_product_version=1,
                budget={},
                now_iso=C.utc_now_iso(),
                deadline_at=now + 300,
                queue_deadline_at=now + 600,
                run_id=C.new_id(),
            ).id

    def tearDown(self) -> None:
        database.dispose_engine()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # -- 工具 -------------------------------------------------------------- #

    def _spawn_crasher(self) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env.update(
            {
                "DATABASE_URL": self.db_url,
                "PYTHONPATH": str(BACKEND),
                "PYTHONIOENCODING": "utf-8",
                "WORKER": CRASH_WORKER,
            }
        )
        return subprocess.run(
            [sys.executable, "-c", _CHILD],
            cwd=str(BACKEND),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )

    def _run_row(self) -> RunRow:
        with database.session_scope() as db:
            row = db.get(RunRow, self.run_id)
            self.assertIsNotNone(row)
            return row

    def _step(self) -> StepRow | None:
        with database.session_scope() as db:
            return run_repo.get_step_by_key(db, self.run_id, "coach.clarify")

    def _agent_message_count(self) -> int:
        with database.session_scope() as db:
            return int(
                db.execute(
                    select(func.count())
                    .select_from(MessageRow)
                    .where(MessageRow.sender_kind != "human")
                ).scalar_one()
            )

    def _product_versions(self) -> list[int]:
        with database.session_scope() as db:
            rows = list(
                db.execute(
                    select(ProductStateRow.version).where(ProductStateRow.session_id == self.session_id)
                ).scalars()
            )
        return sorted(rows)

    def _terminal_events(self) -> list:
        with database.session_scope() as db:
            events = list(run_repo.events_after(db, self.run_id, after_seq=0, limit=500))
        terminal = {e.value for e in C.TERMINAL_EVENT_TYPES}
        return [event for event in events if str(event.type) in terminal]

    def _take_over(self) -> str:
        """把死掉 Worker 的租约推到过期，然后由父进程领取。"""

        with database.session_scope() as db:
            db.execute(
                update(RunRow)
                .where(RunRow.id == self.run_id)
                .values(lease_expires_at=int(time.time()) - 1)
            )
        with database.session_scope() as db:
            claimed = run_repo.claim_next_run(
                db, worker_id=TAKEOVER_WORKER, now_epoch=int(time.time())
            )
        self.assertIsNotNone(claimed, "租约过期后应能接管")
        return claimed.id

    # -- 用例 -------------------------------------------------------------- #

    def test_reservation_is_persisted_before_upstream_call(self) -> None:
        """崩溃发生在「模型已响应、结果未提交」之后：预留必须已落盘。"""

        proc = self._spawn_crasher()
        self.assertEqual(proc.returncode, 9, msg=proc.stderr)
        self.assertNotIn("CHILD_FINISHED_WITHOUT_CRASH", proc.stdout)

        row = self._run_row()
        self.assertIsNone(row.finished_at, "崩溃后 Run 不能是终态")
        usage = json.loads(row.usage_json or "{}")
        self.assertEqual(usage.get("upstream_calls"), 1, "崩溃那次调用的次数必须已落盘")
        self.assertGreater(usage.get("reserved_prompt_tokens", 0), 0, "预留额度必须已落盘")

        step = self._step()
        self.assertIsNotNone(step)
        self.assertEqual(step.status, C.StepStatus.running.value)
        self.assertEqual(int(step.attempt_count or 0), 1)
        self.assertEqual(self._agent_message_count(), 0)
        self.assertEqual(self._product_versions(), [1])
        self.assertEqual(self._terminal_events(), [])
    def test_takeover_charges_crashed_reservation_and_never_double_applies(self) -> None:
        proc = self._spawn_crasher()
        self.assertEqual(proc.returncode, 9, msg=proc.stderr)
        usage_before = json.loads(self._run_row().usage_json or "{}")
        reserved_before = int(usage_before.get("reserved_prompt_tokens", 0))
        self.assertGreater(reserved_before, 0)

        run_id = self._take_over()
        original_dispatch = runner.WORKFLOW_DISPATCH.get(C.TaskKind.clarify)
        runner.WORKFLOW_DISPATCH[C.TaskKind.clarify] = _takeover_dispatch

        def _restore_dispatch() -> None:
            if original_dispatch is None:
                runner.WORKFLOW_DISPATCH.pop(C.TaskKind.clarify, None)
            else:
                runner.WORKFLOW_DISPATCH[C.TaskKind.clarify] = original_dispatch

        self.addCleanup(_restore_dispatch)
        llm = _OkLLM()
        execute_run(run_id, worker_id=TAKEOVER_WORKER, llm_factory=lambda: llm, renew_thread=False)

        row = self._run_row()
        self.assertEqual(row.status, C.RunStatus.succeeded.value, row.error_message)
        self.assertIsNotNone(row.finished_at)

        usage = json.loads(row.usage_json or "{}")
        # 崩溃一次 + 重跑一次 = 2，且上一 epoch 的预留按已消耗计入 token 账本。
        self.assertEqual(usage.get("upstream_calls"), 2)
        self.assertGreaterEqual(usage.get("prompt_tokens", 0), reserved_before)
        # 结果只应用一次：一条助手消息、一个终结事件、产品版本只推进一版。
        self.assertEqual(llm.calls, 1)
        self.assertEqual(self._agent_message_count(), 1)
        self.assertEqual(len(self._terminal_events()), 1)
        self.assertEqual(self._product_versions(), [1, 2])
        self.assertEqual(self._step().status, C.StepStatus.succeeded.value)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
