"""排队超时收口回归（P1，AUD-01 同类死锁）。

覆盖审计复现 ``.tmp_tests/probe_loop_queue_timeout.py`` 的三条断言：

1. ``expire_queued_runs`` 把闭环轮次 Run 置 ``failed/QUEUE_WAIT_EXCEEDED`` 时，
   必须在同一事务内收口闭环（round/loop=failed、清 ``session.active_run_id``），
   否则 ``start_loop`` 会永久 409 ``LOOP_ACTIVE``、``loop_decision`` 永久 409
   ``LOOP_NOT_AWAITING_DECISION``，闭环卡死在 running。
2. 收口后 ``start_loop`` 能重新开启闭环（错误可恢复），不是只能重试原命令。
3. 重试失败轮次时子 Run 继承 ``loop_id`` 并重新挂回该轮（``loop.latest_run_id``
   更新），取消子 Run 才能经 ``_abort_loop_for_run`` 收口闭环。
4. 收口后已经开了新闭环时，重试旧 Run 必须 409 ``SESSION_BUSY``，不得建出
   圈外孤儿子 Run（``probe_loop_queue_timeout.py`` 第 7 步的 409 空响应会被
   探针误读成「子 Run loop_id=None」，这里锁定真实契约）。
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from sqlalchemy import func, select

import domain.contracts as C
import domain.state_machine as SM
from domain.errors import AppError, ErrorCode
from domain.contracts import (
    Action,
    CommandRequest,
    LoopRoundStatus,
    LoopStatus,
    RunStatus,
)
from repositories import database, loop_repo, run_repo, session_repo
from repositories.database import RunRow, session_scope
from services import command_service

NOW_ISO = "2026-11-19T09:00:00Z"
NOW_EPOCH = 1_764_224_400
#: 每条命令推进一个限流窗口，避免 20 次/分钟上限互相干扰（与 test_loop_commands 一致）。
CLOCK = {"epoch": NOW_EPOCH}
OWNER = "queue-recovery@example.com"


class LoopQueueRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.makedirs(".tmp_tests", exist_ok=True)
        cls._tmpdir = tempfile.mkdtemp(prefix="loop_queue_", dir=os.path.abspath(".tmp_tests"))
        cls._db_url = "sqlite+pysqlite:///" + os.path.join(cls._tmpdir, "queue.db").replace("\\", "/")
        database.configure(cls._db_url, force=True)
        database.initialize_database(database.get_engine(), drop_first=True)

    @classmethod
    def tearDownClass(cls):
        database.dispose_engine()
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def setUp(self):
        """清掉上一个用例遗留的非终结 Run（共享库 + 单 owner 的排队上限）。"""

        with session_scope() as db:
            rows = db.execute(select(RunRow).where(RunRow.owner_email == OWNER)).scalars().all()
            for row in rows:
                if not SM.is_terminal_run(RunStatus(row.status)):
                    command_service.cancel_run(db, owner_email=OWNER, run_id=row.id, now_iso=NOW_ISO)

    # ------------------------------------------------------------------ 夹具

    def _new_session(self) -> str:
        with session_scope() as db:
            session = session_repo.create_session(
                db,
                session_id=C.new_id(),
                owner_email=OWNER,
                problem_statement="排队超时收口：预算协同工具",
                now_iso=NOW_ISO,
            )
            return session.id

    def _submit(self, session_id: str, action: Action, payload: dict):
        CLOCK["epoch"] += 70
        with session_scope() as db:
            command = CommandRequest(request_id=C.new_request_id(), action=action, payload=payload)
            return command_service.submit_command(
                db,
                owner_email=OWNER,
                session_id=session_id,
                command=command,
                now_iso=NOW_ISO,
                now_epoch=CLOCK["epoch"],
            )

    def _advance_to_brainstorming(self, session_id: str) -> None:
        with session_scope() as db:
            state = session_repo.get_product_state(db, session_id)
            self._submit(
                session_id,
                Action.revise_brief,
                {
                    "expected_product_version": state.version,
                    "fields": {
                        "target_users": {"value": "中小企业财务团队", "status": "proposed"},
                        "usage_scenario": {"value": "月度预算协同与审批", "status": "proposed"},
                        "core_problem": {"value": "预算协作靠表格来回传", "status": "proposed"},
                        "success_criteria": {"value": "月度预算对齐时间缩短一半", "status": "proposed"},
                    },
                },
            )
            state = session_repo.get_product_state(db, session_id)
            self._submit(
                session_id,
                Action.confirm_brief,
                {"expected_product_version": state.version, "fields": {}},
            )

    def _start_loop(self, session_id: str):
        with session_scope() as db:
            state = session_repo.get_product_state(db, session_id)
        return self._submit(
            session_id,
            Action.start_loop,
            {
                "scope": "mvp",
                "max_rounds": 3,
                "target_score": 85,
                "focus": "先做预算协同主流程",
                "expected_product_version": state.version,
            },
        )

    def _expire(self, run_id: str) -> list[str]:
        with session_scope() as db:
            return run_repo.expire_queued_runs(
                db,
                now_epoch=CLOCK["epoch"] + C.QUEUE_WAIT_LIMIT_SECONDS + 1,
                now_iso=NOW_ISO,
            )

    # ------------------------------------------------------------------ 用例

    def test_queue_timeout_settles_loop_round_and_session(self):
        """P1：Run 失败必须收口闭环，不能留下 running 的 loop/round。"""

        sid = self._new_session()
        self._advance_to_brainstorming(sid)
        receipt = self._start_loop(sid)

        with session_scope() as db:
            loop = loop_repo.get_loop(db, receipt.loop_id)
            run = run_repo.get_run(db, receipt.run_id)
            session = session_repo.require_session(db, sid, owner_email=OWNER)
            self.assertIsNotNone(loop)
            self.assertEqual(loop.status, LoopStatus.running.value)
            self.assertEqual(run.status, RunStatus.queued.value)
            self.assertEqual(session.active_run_id, receipt.run_id)

        expired = self._expire(receipt.run_id)
        self.assertIn(receipt.run_id, expired)

        with session_scope() as db:
            run = run_repo.get_run(db, receipt.run_id)
            loop = loop_repo.get_loop(db, receipt.loop_id)
            session = session_repo.require_session(db, sid, owner_email=OWNER)
            round_row = loop_repo.get_round(db, loop_id=receipt.loop_id, ordinal=1)

            self.assertEqual(run.status, RunStatus.failed.value)
            self.assertEqual(run.error_code, "QUEUE_WAIT_EXCEEDED")
            # 闭环与轮次必须离开 running（failed 是终结态）
            self.assertEqual(loop.status, LoopStatus.failed.value)
            self.assertIsNotNone(loop.closed_at)
            self.assertNotEqual(round_row.status, LoopRoundStatus.running.value)
            # 会话占位必须释放，否则 retry / start_loop 都会 409 SESSION_BUSY
            self.assertIsNone(session.active_run_id)
            self.assertIsNone(run_repo.active_run_for_session(db, sid))
            self.assertIsNone(loop_repo.active_loop_for_session(db, sid))

    def test_start_loop_recovers_after_queue_timeout(self):
        """收口后可重新开闭环（不再 409 LOOP_ACTIVE）。"""

        sid = self._new_session()
        self._advance_to_brainstorming(sid)
        first = self._start_loop(sid)
        self._expire(first.run_id)

        with session_scope() as db:
            self.assertIsNone(loop_repo.active_loop_for_session(db, sid))

        second = self._start_loop(sid)
        self.assertNotEqual(second.loop_id, first.loop_id)
        with session_scope() as db:
            loop = loop_repo.get_loop(db, second.loop_id)
            session = session_repo.require_session(db, sid, owner_email=OWNER)
            self.assertEqual(loop.status, LoopStatus.running.value)
            self.assertEqual(session.active_run_id, second.run_id)

    def test_retry_after_queue_timeout_rejoins_loop_and_can_be_cancelled(self):
        """P1：重试子 Run 继承 loop_id，取消子 Run 能收口闭环。"""

        sid = self._new_session()
        self._advance_to_brainstorming(sid)
        receipt = self._start_loop(sid)
        self._expire(receipt.run_id)

        CLOCK["epoch"] += 70
        with session_scope() as db:
            child = command_service.retry_run(
                db,
                owner_email=OWNER,
                run_id=receipt.run_id,
                request_id=C.new_request_id(),
                now_iso=NOW_ISO,
                now_epoch=CLOCK["epoch"],
            )

        self.assertEqual(child.loop_id, receipt.loop_id)
        with session_scope() as db:
            child_row = run_repo.get_run(db, child.run_id)
            loop = loop_repo.get_loop(db, receipt.loop_id)
            round_row = loop_repo.get_round(db, loop_id=receipt.loop_id, ordinal=1)
            session = session_repo.require_session(db, sid, owner_email=OWNER)

            self.assertEqual(child_row.loop_id, receipt.loop_id)
            self.assertEqual(loop.latest_run_id, child.run_id)
            self.assertEqual(loop.status, LoopStatus.running.value)
            self.assertEqual(round_row.status, LoopRoundStatus.running.value)
            self.assertEqual(round_row.run_id, child.run_id)
            self.assertEqual(session.active_run_id, child.run_id)

        # 取消重试出来的子 Run：latest_run_id 指向它，_abort_loop_for_run 必须收口
        with session_scope() as db:
            command_service.cancel_run(db, owner_email=OWNER, run_id=child.run_id, now_iso=NOW_ISO)

        with session_scope() as db:
            loop = loop_repo.get_loop(db, receipt.loop_id)
            round_row = loop_repo.get_round(db, loop_id=receipt.loop_id, ordinal=1)
            session = session_repo.require_session(db, sid, owner_email=OWNER)
            self.assertEqual(loop.status, LoopStatus.failed.value)
            self.assertIsNotNone(loop.closed_at)
            self.assertNotEqual(round_row.status, LoopRoundStatus.running.value)
            self.assertIsNone(session.active_run_id)

    def test_retry_after_new_loop_is_rejected_without_orphan_run(self):
        """收口后已开新闭环时，重试旧 Run 必须 409 SESSION_BUSY，不能建出圈外孤儿 Run。

        ``probe_loop_queue_timeout.py`` 第 7 步先 ``start_loop`` 再 ``retry-failed``，
        探针把 409 响应里的空 run_id 误读成「子 Run loop_id=None」；这里锁定真实契约：
        重试被拒、不产生子 Run、不抢走新闭环的 active_run_id。
        """

        sid = self._new_session()
        self._advance_to_brainstorming(sid)
        first = self._start_loop(sid)
        self._expire(first.run_id)
        second = self._start_loop(sid)

        CLOCK["epoch"] += 70
        with session_scope() as db:
            children_before = db.execute(
                select(func.count())
                .select_from(RunRow)
                .where(RunRow.owner_email == OWNER, RunRow.parent_run_id == first.run_id)
            ).scalar_one()
            with self.assertRaises(AppError) as ctx:
                command_service.retry_run(
                    db,
                    owner_email=OWNER,
                    run_id=first.run_id,
                    request_id=C.new_request_id(),
                    now_iso=NOW_ISO,
                    now_epoch=CLOCK["epoch"],
                )
            self.assertEqual(ctx.exception.code, ErrorCode.SESSION_BUSY)

            children_after = db.execute(
                select(func.count())
                .select_from(RunRow)
                .where(RunRow.owner_email == OWNER, RunRow.parent_run_id == first.run_id)
            ).scalar_one()
            session = session_repo.require_session(db, sid, owner_email=OWNER)
            new_loop = loop_repo.get_loop(db, second.loop_id)

            self.assertEqual(children_after, children_before, "重试被拒时不得落库孤儿子 Run")
            self.assertEqual(session.active_run_id, second.run_id, "不得抢走新闭环的会话占位")
            self.assertEqual(new_loop.status, LoopStatus.running.value)

    def test_plain_queued_run_timeout_clears_session_active_run(self):
        """非闭环 Run 同样要释放会话占位（否则整会话 SESSION_BUSY）。"""

        sid = self._new_session()
        with session_scope() as db:
            session = session_repo.require_session(db, sid, owner_email=OWNER)
            run = run_repo.create_run(
                db,
                session_id=sid,
                owner_email=OWNER,
                request_id=C.new_request_id(),
                action=Action.brainstorm.value,
                input_hash="a" * 64,
                base_session_version=session.session_version,
                base_product_version=session.current_product_version,
                now_iso=NOW_ISO,
                deadline_at=CLOCK["epoch"] + C.RUN_TIMEOUT_SECONDS,
                queue_deadline_at=CLOCK["epoch"] + C.QUEUE_WAIT_LIMIT_SECONDS,
            )
            session_repo.touch_session(db, session, now_iso=NOW_ISO, active_run_id=run.id)

        self._expire(run.id)

        with session_scope() as db:
            run_row = run_repo.get_run(db, run.id)
            session = session_repo.require_session(db, sid, owner_email=OWNER)
            self.assertEqual(run_row.status, RunStatus.failed.value)
            self.assertEqual(run_row.error_code, "QUEUE_WAIT_EXCEEDED")
            self.assertIsNone(session.active_run_id)
            self.assertIsNone(run_repo.active_run_for_session(db, sid))


if __name__ == "__main__":
    unittest.main()
