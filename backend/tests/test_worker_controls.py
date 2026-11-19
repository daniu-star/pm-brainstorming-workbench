"""第 5 轮 P0/P1 回归：Worker 驱动控制意图 + 兜底终结收口闭环。

对应评测组第 5 轮结论（job ``workflow-dd3864e6c8314ce1``，63/100）：

* **P0 SRV-01**：``command_service.reconcile_control_intents`` 只在测试里被手工调用，
  生产代码无人驱动 → 活跃 Run 上提交 ``end_audit`` 后审计永久停在 ``closing``、
  ``cancel_audit`` 后会话永久停在 ``auditing``（start/end/cancel 全被拒）。
  修复：``RunWorker.tick()`` 每轮在 ``expire_queued_runs`` 之后调用扫描器。
* **P1 SRV-03**：``runner._force_terminal`` 兜底终结 Run 时不处理 loops/loop_rounds，
  提交失败后闭环永久 ``running``（``start_loop`` → LOOP_ACTIVE、
  ``loop_decision`` → LOOP_NOT_AWAITING_DECISION）。
  修复：``_force_terminal`` 调用 ``command_service.settle_loop_for_run_failure``。

用例全部是真 sqlite + 真 ORM，不联网、不起子进程；``_execute`` 被替换为空实现，
因为本文件只验证「维护阶段」的副作用（跑模型属于 ``test_runner`` 的职责）。
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
import unittest

from sqlalchemy import select

import domain.contracts as C
import domain.state_machine as SM
from domain.contracts import (
    Action,
    AuditStatus,
    CommandRequest,
    ControlStatus,
    LoopRoundStatus,
    LoopStatus,
    RunStatus,
    SessionPhase,
)
from repositories import audit_repo, database, loop_repo, run_repo, session_repo
from repositories.database import RunRow, session_scope
from services import command_service
from workers.run_worker import RunWorker

NOW_ISO = "2026-11-19T10:00:00Z"
NOW_EPOCH = 1_764_227_600
CLOCK = {"epoch": NOW_EPOCH}
OWNER = "worker-controls@example.com"
WORKER_ID = "worker-controls-test"


class _NoExecWorker(RunWorker):
    """只跑维护阶段，不执行 Run（避免真实模型调用）。

    Worker 的 ``tick`` 在事务内完成 recovery/requeue/expire/reconcile 后才领取
    Run；这里把执行截断，断言仍然覆盖「每轮必须驱动扫描器」这一契约。
    """

    def __init__(self) -> None:
        super().__init__(worker_id=WORKER_ID, interval=0.1, max_runs=1)
        self.executed: list[str] = []

    def _execute(self, run_id: str, *, lease_epoch: int) -> None:  # noqa: D401 - 覆写
        self.executed.append(run_id)


class _SharedCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.makedirs(".tmp_tests", exist_ok=True)
        cls._tmpdir = tempfile.mkdtemp(prefix="worker_ctrl_", dir=os.path.abspath(".tmp_tests"))
        cls._db_url = "sqlite+pysqlite:///" + os.path.join(cls._tmpdir, "w.db").replace("\\", "/")
        database.configure(cls._db_url, force=True)
        database.initialize_database(database.get_engine(), drop_first=True)

    @classmethod
    def tearDownClass(cls):
        database.dispose_engine()
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def setUp(self):
        with session_scope() as db:
            rows = db.execute(select(RunRow).where(RunRow.owner_email == OWNER)).scalars().all()
            for row in rows:
                if not SM.is_terminal_run(RunStatus(row.status)):
                    command_service.cancel_run(db, owner_email=OWNER, run_id=row.id, now_iso=NOW_ISO)

    # ------------------------------------------------------------------ 夹具

    def _new_session(self, *, phase: SessionPhase = SessionPhase.brainstorming) -> str:
        with session_scope() as db:
            session = session_repo.create_session(
                db,
                session_id=C.new_id(),
                owner_email=OWNER,
                problem_statement="控制意图扫描：预算协同工具",
                now_iso=NOW_ISO,
            )
            if phase is not SessionPhase.clarifying:
                session_repo.touch_session(db, session, now_iso=NOW_ISO, phase=phase)
            return session.id

    def _new_audit(self, session_id: str, *, status: AuditStatus) -> str:
        with session_scope() as db:
            audit = audit_repo.create_audit(
                db, session_id=session_id, product_version=1, now_iso=NOW_ISO
            )
            if status is not AuditStatus.created:
                audit_repo.update_audit(db, audit, now_iso=NOW_ISO, status=status)
            return audit.id

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
        with session_scope() as db:
            state = session_repo.get_product_state(db, session_id)
        self._submit(
            session_id,
            Action.confirm_brief,
            {"expected_product_version": state.version, "fields": {}},
        )


class WorkerReconcileTests(_SharedCase):
    """P0 SRV-01：Worker 每轮必须驱动控制意图扫描器。"""

    def test_tick_reconciles_accepted_end_control_into_closing_run(self):
        sid = self._new_session(phase=SessionPhase.auditing)
        audit_id = self._new_audit(sid, status=AuditStatus.active)
        with session_scope() as db:
            session = session_repo.require_session(db, sid, owner_email=OWNER)
            session_repo.touch_session(db, session, now_iso=NOW_ISO, current_audit_id=audit_id)
            control = audit_repo.create_control_intent(
                db,
                session_id=sid,
                audit_id=audit_id,
                owner_email=OWNER,
                request_id=C.new_request_id(),
                action=Action.end_audit,
                now_iso=NOW_ISO,
            )
            control_id = control.id

        worker = _NoExecWorker()
        self.assertTrue(worker.tick())
        self.assertEqual(len(worker.executed), 1)

        with session_scope() as db:
            control = audit_repo.get_control(db, control_id)
            audit = audit_repo.get_audit(db, audit_id)
            session = session_repo.require_session(db, sid, owner_email=OWNER)
            # 扫描器把控制意图从 accepted 推进到 scheduled，并建出唯一的 closing Run
            self.assertEqual(control.status, ControlStatus.scheduled.value)
            self.assertIsNotNone(control.related_run_id)
            closing = run_repo.get_run(db, control.related_run_id)
            self.assertEqual(closing.action, Action.end_audit.value)
            self.assertEqual(closing.session_id, sid)
            self.assertEqual(closing.audit_id, audit_id)
            self.assertEqual(worker.executed, [closing.id])
            # 审计进入 closing、会话挂上 active_run_id（否则前端无从恢复）
            self.assertEqual(audit.status, AuditStatus.closing.value)
            self.assertEqual(session.active_run_id, closing.id)

    def test_tick_leaves_control_pending_while_a_run_is_active(self):
        sid = self._new_session(phase=SessionPhase.auditing)
        audit_id = self._new_audit(sid, status=AuditStatus.active)
        with session_scope() as db:
            control = audit_repo.create_control_intent(
                db,
                session_id=sid,
                audit_id=audit_id,
                owner_email=OWNER,
                request_id=C.new_request_id(),
                action=Action.end_audit,
                now_iso=NOW_ISO,
            )
            control_id = control.id
            # 造一个仍被别的 Worker 持有的 running Run（租约有效）
            active = run_repo.create_run(
                db,
                session_id=sid,
                owner_email=OWNER,
                request_id=C.new_request_id(),
                action=Action.answer_audit.value,
                input_hash="x" * 64,
                base_session_version=1,
                base_product_version=1,
                now_iso=NOW_ISO,
                audit_id=audit_id,
            )
            # 用真实时间领取：tick 内部用 time.time() 判断租约，测试夹具的固定
            # NOW_EPOCH 早已过期，会让 other-worker 的租约被判定为可接管。
            run_repo.claim_next_run(db, worker_id="other-worker", now_epoch=int(time.time()))

        worker = _NoExecWorker()
        # 唯一可领取的 Run 已被 other-worker 持有 → 本轮无事可做
        self.assertFalse(worker.tick())
        self.assertEqual(worker.executed, [])

        with session_scope() as db:
            control = audit_repo.get_control(db, control_id)
            audit = audit_repo.get_audit(db, audit_id)
            self.assertEqual(control.status, ControlStatus.accepted.value)
            self.assertIsNone(control.related_run_id)
            self.assertEqual(audit.status, AuditStatus.active.value)
            self.assertIsNotNone(run_repo.active_run_for_session(db, sid))


class LoopSettleTests(_SharedCase):
    """P1 SRV-03：兜底终结路径必须收口闭环，不能留下 running 的 loop/round。"""

    def _start_loop(self) -> tuple[str, str, str]:
        sid = self._new_session(phase=SessionPhase.clarifying)
        self._advance_to_brainstorming(sid)
        with session_scope() as db:
            state = session_repo.get_product_state(db, sid)
        receipt = self._submit(
            sid,
            Action.start_loop,
            {
                "scope": "mvp",
                "max_rounds": 3,
                "target_score": 85,
                "focus": "先做预算协同主流程",
                "expected_product_version": state.version,
            },
        )
        return sid, receipt.loop_id, receipt.run_id

    def test_force_terminal_settles_loop_and_round(self):
        from workflows import runner as runner_mod

        sid, loop_id, run_id = self._start_loop()
        with session_scope() as db:
            run = run_repo.get_run(db, run_id)
            claimed = run_repo.claim_next_run(db, worker_id=WORKER_ID, now_epoch=NOW_EPOCH)
            self.assertIsNotNone(claimed)
            lease_epoch = int(run_repo.get_run(db, run_id).lease_epoch or 0)

        ok = runner_mod._force_terminal(
            run_id=run_id,
            worker_id=WORKER_ID,
            lease_epoch=lease_epoch,
            status=RunStatus.failed,
            error_code="INTERNAL",
            error_message="提交阶段异常",
            bump_session_version=False,
        )
        self.assertTrue(ok)

        with session_scope() as db:
            run = run_repo.get_run(db, run_id)
            loop = loop_repo.get_loop(db, loop_id)
            round_row = loop_repo.get_round(db, loop_id=loop_id, ordinal=1)
            session = session_repo.require_session(db, sid, owner_email=OWNER)
            self.assertEqual(run.status, RunStatus.failed.value)
            self.assertEqual(loop.status, LoopStatus.failed.value)
            self.assertIsNotNone(loop.closed_at)
            self.assertNotEqual(round_row.status, LoopRoundStatus.running.value)
            self.assertIsNone(session.active_run_id)
            self.assertIsNone(loop_repo.active_loop_for_session(db, sid))
            event_types = [event.type for event in run_repo.events_after(db, run_id, after_seq=0)]
            self.assertIn(C.EventType.loop_updated.value, event_types)

    def test_settle_ignores_run_that_is_no_longer_current(self):
        """旧轮次 Run 的兜底终结不得关掉已经推进到下一轮的闭环。"""

        sid, loop_id, run_id = self._start_loop()
        with session_scope() as db:
            loop = loop_repo.get_loop(db, loop_id)
            loop_repo.touch_loop(db, loop, now_iso=NOW_ISO, latest_run_id=C.new_id())

        with session_scope() as db:
            settled = command_service.settle_loop_for_run_failure(
                db, run_id=run_id, now_iso=NOW_ISO, note="迟到的兜底终结"
            )
        self.assertFalse(settled)

        with session_scope() as db:
            loop = loop_repo.get_loop(db, loop_id)
            self.assertEqual(loop.status, LoopStatus.running.value)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
