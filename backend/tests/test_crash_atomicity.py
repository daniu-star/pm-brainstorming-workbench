"""跨进程崩溃注入：步骤成功与结果提交必须原子（SRV-02）。

第 5 轮评审的真实缺陷是：``backend/workflows/runner.py`` 先把步骤标记为
``succeeded``（独立事务），随后才在另一个事务里提交消息 / 产品状态 / 事件。
如果进程在两者之间崩溃，重放分支会看到一个 ``succeeded`` 的步骤，并用**空
outcome** 提交，于是 Run 显示成功、用户却拿不到任何回复。

本模块用**真实的独立解释器子进程**验证修复后的不变量：

1. 子进程在「步骤成功 + 结果」那一次事务的 commit 处直接 ``os._exit``（模拟崩溃，
   此时 flush 已完成、commit 未发出，SQLite 会把整个事务回滚）；
2. 父进程检查数据库：Run **不得**是终态、**不得**有 assistant 消息、步骤不得是
   ``succeeded``（即不可能出现「步骤成功但结果没落库」的残留）；
3. 父进程接管租约后重跑一次：必须产出**恰好一条**消息、**恰好一个**终结事件、
   product_state 推进到 v2，步骤 ``succeeded`` 且 ``output_json.committed`` 为真；
4. 再次执行同一 Run 不得重复写入（AC-09）。

只有真进程 + 真事务边界能证明这件事，因此这里不退化为同进程 mock。
"""
from __future__ import annotations

import json
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
from repositories.database import EventRow, MessageRow, RunRow, StepRow  # noqa: E402
from workflows import runner  # noqa: E402
from workflows.runner import execute_run  # noqa: E402

OWNER = "crash-owner@example.com"
WORKER = "crash-worker-1"

# 子进程脚本：抢到租约 → 装崩溃钩子 → 跑一次 run → 在提交步骤成功时 os._exit(9)。
_CHILD = r"""
import os
from repositories import database

database.configure(os.environ["DATABASE_URL"], force=True)

from sqlalchemy.orm import Session

from domain import contracts as C
from repositories.database import MessageRow, StepRow
from repositories import run_repo
from services.command_service import OutgoingMessage, RunOutcome
from workflows import runner


_original_flush = Session.flush


def _fatal_flush(self, objects=None):
    # 「步骤成功」与消息 / 产品状态 / 事件在同一个事务里 flush；
    # 在这一刻杀掉进程 = 事务已写但未提交，SQLite 会整体回滚。
    for obj in list(self.dirty) + list(self.new):
        if isinstance(obj, StepRow) and str(getattr(obj, "status", "")) == "succeeded":
            os._exit(9)
    return _original_flush(self, objects)


Session.flush = _fatal_flush


def _dispatch(ctx):
    state = ctx.product_state
    return RunOutcome(
        status=C.RunStatus.succeeded,
        product_state=state.model_copy(update={"version": int(state.version) + 1}),
        messages=[OutgoingMessage(content="崩溃注入后的助手回复")],
    )


runner.WORKFLOW_DISPATCH[C.TaskKind.clarify] = _dispatch

with database.session_scope() as db:
    claimed = run_repo.claim_next_run(
        db, worker_id=os.environ["WORKER"], now_epoch=int(__import__("time").time())
    )
if claimed is None:
    raise SystemExit("claim failed")

runner.execute_run(claimed.id, worker_id=os.environ["WORKER"], renew_thread=False)
print("CHILD_FINISHED_WITHOUT_CRASH")
"""


class _UnusedLLM:
    """dispatch 不调用模型；Runner 仍要求一个 llm 对象。"""

    async def complete(self, **kwargs):  # pragma: no cover - 不应被调用
        raise AssertionError("崩溃注入用例不应调用模型")


class CrashAtomicityTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp_root = BACKEND / ".tmp_tests"
        tmp_root.mkdir(exist_ok=True)
        self.tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="crash_", dir=str(tmp_root)))
        self.db_url = "sqlite:///" + (self.tmpdir / "crash.db").as_posix()
        database.configure(self.db_url, force=True)
        database.initialize_database()

        with database.session_scope() as db:
            session = session_repo.create_session(
                db,
                owner_email=OWNER,
                problem_statement="崩溃注入：验证结果与步骤成功同事务",
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
                input_hash="crash-input-hash",
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
        env = dict(__import__("os").environ)
        env.update(
            {
                "DATABASE_URL": self.db_url,
                "PYTHONPATH": str(BACKEND),
                "PYTHONIOENCODING": "utf-8",
                "WORKER": WORKER,
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

    def _count(self, model) -> int:
        with database.session_scope() as db:
            return int(db.execute(select(func.count()).select_from(model)).scalar_one())

    def _agent_message_count(self) -> int:
        """只数助手/系统消息：seq=1 的种子人类消息由 create_session 写入。"""
        with database.session_scope() as db:
            return int(
                db.execute(
                    select(func.count())
                    .select_from(MessageRow)
                    .where(MessageRow.sender_kind != "human")
                ).scalar_one()
            )

    def _run_row(self) -> RunRow:
        with database.session_scope() as db:
            row = db.get(RunRow, self.run_id)
            self.assertIsNotNone(row)
            return row

    def _step(self) -> StepRow | None:
        with database.session_scope() as db:
            return run_repo.get_step_by_key(db, self.run_id, "coach.clarify")

    def _terminal_events(self) -> list[EventRow]:
        with database.session_scope() as db:
            rows = list(run_repo.events_after(db, self.run_id, after_seq=0, limit=500))
        return [row for row in rows if str(row.type) in {e.value for e in C.TERMINAL_EVENT_TYPES}]

    def _take_over(self) -> None:
        """把死掉的 worker 租约推到过期，然后由父进程重新领取。"""
        with database.session_scope() as db:
            db.execute(
                update(RunRow)
                .where(RunRow.id == self.run_id)
                .values(lease_expires_at=int(time.time()) - 1)
            )

    # -- 用例 -------------------------------------------------------------- #

    def test_crash_during_commit_leaves_no_succeeded_step_and_no_empty_success(self) -> None:
        proc = self._spawn_crasher()
        self.assertEqual(
            proc.returncode,
            9,
            f"子进程应在提交步骤成功时崩溃，实际 rc={proc.returncode}\n{proc.stdout}\n{proc.stderr[-2000:]}",
        )
        self.assertNotIn("CHILD_FINISHED_WITHOUT_CRASH", proc.stdout)

        run = self._run_row()
        self.assertNotIn(
            str(run.status),
            {"succeeded", "partial_succeeded"},
            "崩溃后 Run 不得是成功态",
        )
        self.assertEqual(self._agent_message_count(), 0, "崩溃后不得留下任何助手消息")
        self.assertEqual(self._count(MessageRow), 1, "只允许存在会话创建时的种子消息")

        step = self._step()
        self.assertIsNotNone(step, "步骤应已创建（attempt 1 已启动）")
        assert step is not None
        self.assertNotEqual(
            str(step.status),
            "succeeded",
            "崩溃后步骤不得是 succeeded —— 否则重放会用空 outcome 伪造成功（SRV-02）",
        )
        self.assertEqual(self._terminal_events(), [], "崩溃后不得有终结事件")

        # 产品状态不得推进（v1 是会话创建时写入的唯一版本）。
        with database.session_scope() as db:
            latest = session_repo.get_product_state(db, self.session_id)
        self.assertEqual(int(latest.version), 1, "崩溃后产品状态不得推进")

        # -- 恢复：另一个 worker 接管后必须真正重跑并提交一次 --------------- #
        self._take_over()
        with database.session_scope() as db:
            claimed = run_repo.claim_next_run(
                db, worker_id="crash-worker-2", now_epoch=int(time.time())
            )
        self.assertIsNotNone(claimed)
        assert claimed is not None
        self.assertEqual(claimed.id, self.run_id)

        runner.WORKFLOW_DISPATCH.clear()
        runner.WORKFLOW_DISPATCH[C.TaskKind.clarify] = runner_temp_outcome
        try:
            execute_run(
                self.run_id,
                worker_id="crash-worker-2",
                llm_factory=_UnusedLLM,
                renew_thread=False,
            )
        finally:
            runner.WORKFLOW_DISPATCH.clear()

        run = self._run_row()
        self.assertEqual(str(run.status), "succeeded", run.error_message)
        self.assertEqual(self._agent_message_count(), 1, "恢复后必须恰好写入一条助手消息")
        self.assertEqual(self._count(MessageRow), 2)
        self.assertEqual(len(self._terminal_events()), 1, "必须恰好一个终结事件")

        step = self._step()
        assert step is not None
        self.assertEqual(str(step.status), "succeeded")
        self.assertIsNotNone(step.output_json)
        payload = json.loads(step.output_json)
        self.assertTrue(payload.get("committed"), "步骤成功必须带 committed 标记")

        with database.session_scope() as db:
            state = session_repo.get_product_state(db, self.session_id)
        self.assertEqual(int(state.version), 2, "恢复后产品状态必须推进到 v2")

        # AC-09：已终结 Run 再次执行不得重复写入。
        execute_run(self.run_id, worker_id="crash-worker-2", renew_thread=False)
        self.assertEqual(self._agent_message_count(), 1)
        self.assertEqual(len(self._terminal_events()), 1)


def runner_temp_outcome(ctx):
    from services.command_service import OutgoingMessage, RunOutcome

    state = ctx.product_state
    return RunOutcome(
        status=C.RunStatus.succeeded,
        product_state=state.model_copy(update={"version": int(state.version) + 1}),
        messages=[OutgoingMessage(content="崩溃注入后的助手回复")],
    )
