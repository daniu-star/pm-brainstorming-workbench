"""崩溃/重启与审计上下文变化验收（PRD AC-36 / AC-41，含 AC-30 取消回归）。

AC-36「生成下一问题时结束审计，收到回执后关闭页面并重启 Worker」：
    1. 提交「回答审计问题」的 Run（会生成下一问）；
    2. 该 Run 正在生成时提交 ``end_audit`` —— 结束意图必须先落库；
    3. 强杀正在生成的工作进程（真 ``SIGKILL`` / ``TerminateProcess``）；
    4. 断言旧生成没有提交（评估、下一问、消息都没写）；
    5. 重启 Worker（租约过期后接管），断言只有唯一一条 ``end_audit`` closing Run
       接续报告，审计最终 ``completed``，会话阶段 ``audit_completed``。

AC-41「开场失败后同产品版本新建审计，再重试旧 Run」：必须以
``RETRY_AUDIT_CONTEXT_CHANGED`` 拒绝，且不复活旧审计实例。

AC-30 回归：``cancel_requested`` 的 Run 在租约过期后被另一个 Worker 接管时，
取消意图不得被重置为 ``running``（否则取消会被静默复活）。
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

from domain import contracts as C  # noqa: E402
from domain.errors import AppError, ErrorCode  # noqa: E402
from repositories import audit_repo, database, run_repo, session_repo  # noqa: E402

OWNER = "control-crash@example.com"
WORKER_SCRIPT = BACKEND / "tests" / "support" / "control_worker.py"


class ControlCrashCase(unittest.TestCase):
    """每个用例独立数据库文件 + 独立临时目录（跨进程共用同一 SQLite 文件）。"""

    def setUp(self) -> None:
        tmp_root = BACKEND / ".tmp_tests"
        tmp_root.mkdir(exist_ok=True)
        self.tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="crash_", dir=str(tmp_root)))
        self.db_path = self.tmpdir / "control.db"
        self.db_url = "sqlite:///" + self.db_path.as_posix()
        database.configure(self.db_url, force=True)
        database.initialize_database()
        self.session_id = C.new_id()
        with database.session_scope() as db:
            session_repo.create_session(
                db,
                session_id=self.session_id,
                owner_email=OWNER,
                problem_statement="崩溃注入验收：预算协同工具",
                now_iso=C.utc_now_iso(),
            )

    def tearDown(self) -> None:
        database.dispose_engine()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # -- 工具 -------------------------------------------------------------- #

    def _command_service(self):
        from services import command_service

        return command_service

    def _worker_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.update(
            {
                "DATABASE_URL": self.db_url,
                "PYTHONPATH": str(BACKEND),
                "PYTHONIOENCODING": "utf-8",
            }
        )
        return env

    def _spawn_worker(self, worker_id: str, max_runs: int, mode: str) -> subprocess.Popen:
        return subprocess.Popen(
            [sys.executable, str(WORKER_SCRIPT), self.db_url, worker_id, str(max_runs), mode],
            cwd=str(BACKEND),
            env=self._worker_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def _advance_to_brainstorming(self) -> None:
        """用同步命令把产品定义填全并确认，会话进入 brainstorming（不触发模型）。"""

        command_service = self._command_service()
        with database.session_scope() as db:
            state = session_repo.get_product_state(db, self.session_id)
            command_service.submit_command(
                db,
                owner_email=OWNER,
                session_id=self.session_id,
                command=C.CommandRequest(
                    request_id=C.new_request_id(),
                    action=C.Action.revise_brief,
                    payload={
                        "expected_product_version": state.version,
                        "fields": {
                            "target_users": {"value": "中小企业财务团队"},
                            "usage_scenario": {"value": "月度预算协同与审批"},
                            "core_problem": {"value": "预算表版本混乱、审批链缺失"},
                            "success_criteria": {"value": "月度预算编制时间缩短 40%"},
                        },
                    },
                ),
            )
        with database.session_scope() as db:
            state = session_repo.get_product_state(db, self.session_id)
            command_service.submit_command(
                db,
                owner_email=OWNER,
                session_id=self.session_id,
                command=C.CommandRequest(
                    request_id=C.new_request_id(),
                    action=C.Action.confirm_brief,
                    payload={"expected_product_version": state.version, "fields": {}},
                ),
            )
        with database.session_scope() as db:
            session = session_repo.require_session(db, self.session_id, owner_email=OWNER)
            self.assertEqual(session.phase, C.SessionPhase.brainstorming.value)

    def _prepare_active_audit(self) -> tuple[str, str]:
        """准备一个 active 审计 + 当前待答问题（不调模型）。"""

        self._advance_to_brainstorming()
        now_iso = C.utc_now_iso()
        with database.session_scope() as db:
            state = session_repo.get_product_state(db, self.session_id)
            audit = audit_repo.create_audit(
                db,
                session_id=self.session_id,
                product_version=state.version,
                now_iso=now_iso,
            )
            question = audit_repo.add_question(
                db,
                audit,
                ordinal=1,
                dimension="problem_validity",
                issue_refs=[],
                question_text="目标用户是谁？有访谈证据吗？",
                now_iso=now_iso,
            )
            audit_repo.update_audit(
                db,
                audit,
                now_iso=now_iso,
                status=C.AuditStatus.active,
                active_question_id=question.id,
                question_count=1,
            )
            session_repo.touch_session(
                db,
                session_repo.require_session(db, self.session_id, owner_email=OWNER),
                now_iso=now_iso,
                phase=C.SessionPhase.auditing,
                current_audit_id=audit.id,
            )
            return audit.id, question.id

    def _submit_answer(self, audit_id: str, question_id: str) -> str:
        command_service = self._command_service()
        with database.session_scope() as db:
            receipt = command_service.submit_command(
                db,
                owner_email=OWNER,
                session_id=self.session_id,
                command=C.CommandRequest(
                    request_id=C.new_request_id(),
                    action=C.Action.answer_audit,
                    payload={
                        "audit_id": audit_id,
                        "question_id": question_id,
                        "answer": "目标用户是中小企业财务团队，已访谈 12 家。",
                    },
                ),
            )
        return receipt.run_id

    def _wait_until_generating(self, run_id: str, *, timeout: float = 30.0) -> None:
        """等到该 Run 已被领取且至少一个步骤进入 running（= 正在调模型）。"""

        deadline = time.time() + timeout
        while time.time() < deadline:
            with database.session_scope() as db:
                run = run_repo.get_run(db, run_id)
                steps = run_repo.list_steps(db, run_id)
                generating = (
                    run is not None
                    and run.status == C.RunStatus.running.value
                    and any(step.status == C.StepStatus.running.value for step in steps)
                )
            if generating:
                return
            time.sleep(0.05)
        self.fail(f"等待 Run {run_id} 进入生成中状态超时")

    def _run_counts(self) -> dict[str, int]:
        with database.session_scope() as db:
            runs = run_repo.list_session_runs(db, self.session_id, limit=100)
            return {
                "total": len(runs),
                "end_audit": len([r for r in runs if r.action == "end_audit"]),
            }

    # -- AC-36 ------------------------------------------------------------- #

    def test_end_audit_receipt_survives_worker_kill_and_closing_run_resumes(self) -> None:
        audit_id, question_id = self._prepare_active_audit()
        run_id = self._submit_answer(audit_id, question_id)

        with database.session_scope() as db:
            baseline_messages = len(session_repo.list_messages(db, self.session_id))
            baseline_dimension_states = audit_repo.get_audit(db, audit_id).dimension_states_json
            self.assertEqual(baseline_dimension_states, "[]", "生成开始前维度评估应为空列表")

        # 1) 慢速 Worker 领取并进入 audit.answer 的模型调用（每次 sleep 8 秒）。
        slow = self._spawn_worker("crash-slow-worker", 1, "slow")
        try:
            self._wait_until_generating(run_id)
            time.sleep(0.3)  # 确保已进入模型调用内部

            # 2) 生成途中提交 end_audit：意图必须先落库（返回回执）。
            command_service = self._command_service()
            with database.session_scope() as db:
                receipt = command_service.submit_command(
                    db,
                    owner_email=OWNER,
                    session_id=self.session_id,
                    command=C.CommandRequest(
                        request_id=C.new_request_id(),
                        action=C.Action.end_audit,
                        payload={"audit_id": audit_id},
                    ),
                )
            self.assertTrue(getattr(receipt, "control_id", None), f"end_audit 必须返回回执：{receipt}")

            self.assertIsNone(slow.poll(), "慢速 Worker 必须仍在生成中（否则 kill 未覆盖真实路径）")
            # 3) 强杀（模拟关页面 / 机器重启）：Windows 上是 TerminateProcess。
            slow.kill()
            slow.communicate(timeout=30)
        finally:
            if slow.poll() is None:  # pragma: no cover - 兜底
                slow.kill()
                slow.communicate(timeout=30)

        # 4) 结束意图已持久；旧生成不提交。
        with database.session_scope() as db:
            run = run_repo.get_run(db, run_id)
            self.assertIsNotNone(run)
            self.assertEqual(
                run.status,
                C.RunStatus.cancel_requested.value,
                "end_audit 的取消意图必须落库，且不得被其它机制改写",
            )
            self.assertEqual(run.lease_owner, "crash-slow-worker", "进程已死，租约仍应保留待接管")
            audit = audit_repo.get_audit(db, audit_id)
            self.assertEqual(audit.status, C.AuditStatus.closing.value)
            control = audit_repo.get_control(db, receipt.control_id)
            self.assertEqual(control.status, C.ControlStatus.scheduled.value)
            self.assertEqual(control.action, C.Action.end_audit.value)
            self.assertEqual(int(audit.question_count), 1, "旧生成不得追加下一问")
            questions = audit_repo.questions_for_audit(db, audit_id)
            self.assertEqual(len(questions), 1)
            self.assertEqual(
                questions[0].assessment,
                C.Assessment.awaiting_answer.value,
                "旧生成不得写入评估",
            )
            self.assertEqual(
                audit.dimension_states_json,
                baseline_dimension_states,
                "旧生成不得写入维度评估",
            )
            messages = session_repo.list_messages(db, self.session_id)
            self.assertEqual(len(messages), baseline_messages, "旧生成不得写入消息")

        # 5) 模拟重启：租约过期后由新 Worker 接管。
        with database.session_scope() as db:
            run = run_repo.get_run(db, run_id)
            run.lease_expires_at = int(time.time()) - 60

        fast = self._spawn_worker("crash-fast-worker", 2, "fast")
        out, err = fast.communicate(timeout=180)
        self.assertEqual(fast.returncode, 0, f"重启后的 Worker 失败 rc={fast.returncode}\n{out}\n{err[-2000:]}")
        self.assertIn("WORKER_DONE", out, f"重启后的 Worker 未正常结束：{out}\n{err[-2000:]}")

        with database.session_scope() as db:
            run = run_repo.get_run(db, run_id)
            self.assertEqual(run.status, C.RunStatus.cancelled.value, "被强杀的旧生成应终结为 cancelled")
            runs = run_repo.list_session_runs(db, self.session_id, limit=100)
            closing = [r for r in runs if r.action == C.Action.end_audit.value]
            self.assertEqual(len(closing), 1, f"必须只有唯一 closing Run：{[r.id for r in closing]}")
            self.assertEqual(closing[0].status, C.RunStatus.succeeded.value, "closing Run 必须接续完成报告")
            audit = audit_repo.get_audit(db, audit_id)
            self.assertEqual(audit.status, C.AuditStatus.completed.value, "审计最终必须 completed")
            self.assertEqual(int(audit.question_count), 1, "旧生成始终不得追加下一问")
            self.assertEqual(len(audit_repo.questions_for_audit(db, audit_id)), 1)
            session = session_repo.require_session(db, self.session_id, owner_email=OWNER)
            self.assertEqual(session.phase, C.SessionPhase.audit_completed.value)
            self.assertIsNone(session.active_run_id, "所有 Run 终结后不得残留占位")
            events = run_repo.events_after(db, closing[0].id)
            self.assertEqual(len([e for e in events if e.type == "run.started"]), 1, "closing Run 只被领取一次")
            self.assertEqual(
                len([e for e in events if e.type in C.TERMINAL_EVENT_TYPES]),
                1,
                "closing Run 只有一条终结事件",
            )

    # -- AC-41 ------------------------------------------------------------- #

    def test_retry_rejected_when_newer_audit_same_product_version(self) -> None:
        """开场失败 → 同产品版本新建审计 → 重试旧 Run 必须 409 且不复活旧实例。"""

        self._advance_to_brainstorming()
        command_service = self._command_service()
        now_iso = C.utc_now_iso()

        with database.session_scope() as db:
            session = session_repo.require_session(db, self.session_id, owner_email=OWNER)
            state = session_repo.get_product_state(db, self.session_id)
            product_version = state.version

            # 审计 A：开场失败的旧实例（失败 Run 绑定在它上面）。
            audit_a = audit_repo.create_audit(
                db, session_id=self.session_id, product_version=product_version, now_iso=now_iso
            )
            audit_repo.update_audit(
                db,
                audit_a,
                now_iso=now_iso,
                status=C.AuditStatus.failed,
                end_reason=C.EndReason.failed,
            )
            run_a = run_repo.create_run(
                db,
                session_id=self.session_id,
                owner_email=OWNER,
                request_id=C.new_request_id(),
                action=C.Action.start_audit.value,
                input_hash="crash-open-failed",
                base_session_version=session.session_version,
                base_product_version=product_version,
                audit_id=audit_a.id,
                base_audit_version=0,
                now_iso=now_iso,
                deadline_at=int(time.time()) + C.RUN_TIMEOUT_SECONDS,
            )
            run_a.status = C.RunStatus.failed.value
            run_a.error_code = "LLM_AUTH_FAILED"
            run_a.error_message = "开场失败"
            run_a.finished_at = now_iso
            audit_a_id = audit_a.id
            run_a_id = run_a.id

        # 同一产品版本上再开一个新审计 B（真实命令路径，会把 current_audit_id 指向 B）。
        with database.session_scope() as db:
            receipt_b = command_service.submit_command(
                db,
                owner_email=OWNER,
                session_id=self.session_id,
                command=C.CommandRequest(
                    request_id=C.new_request_id(),
                    action=C.Action.start_audit,
                    payload={"expected_product_version": product_version},
                ),
            )
        with database.session_scope() as db:
            session = session_repo.require_session(db, self.session_id, owner_email=OWNER)
            self.assertIsNotNone(session.current_audit_id)
            self.assertNotEqual(session.current_audit_id, audit_a_id, "新审计必须是另一个实例")
            # 新审计的开场 Run 视为已成功（否则会话被它占住，重试只会先撞 SESSION_BUSY）。
            run_b = run_repo.get_run(db, receipt_b.run_id)
            run_b.status = C.RunStatus.succeeded.value
            run_b.finished_at = now_iso
            session.active_run_id = None
            session_repo.bump_session_version(db, session)
            runs_before = len(run_repo.list_session_runs(db, self.session_id, limit=100))

        # 重试旧 Run：必须按审计上下文变化拒绝。
        with database.session_scope() as db:
            with self.assertRaises(AppError) as ctx:
                command_service.retry_run(
                    db,
                    owner_email=OWNER,
                    run_id=run_a_id,
                    request_id=C.new_request_id(),
                )
        self.assertEqual(ctx.exception.code, ErrorCode.RETRY_AUDIT_CONTEXT_CHANGED)
        self.assertIn("审计", ctx.exception.message)

        # 不复活旧实例、不产生任何新 Run。
        with database.session_scope() as db:
            runs_after = len(run_repo.list_session_runs(db, self.session_id, limit=100))
            self.assertEqual(runs_after, runs_before, "被拒绝的重试不得创建子 Run")
            audit_a = audit_repo.get_audit(db, audit_a_id)
            self.assertEqual(audit_a.status, C.AuditStatus.failed.value, "旧审计不得被复活")
            session = session_repo.require_session(db, self.session_id, owner_email=OWNER)
            self.assertNotEqual(session.current_audit_id, audit_a_id)

    # -- AC-30 回归：取消意图不得被接管重置 --------------------------------- #

    def test_cancel_requested_survives_lease_takeover(self) -> None:
        now_iso = C.utc_now_iso()
        with database.session_scope() as db:
            session = session_repo.require_session(db, self.session_id, owner_email=OWNER)
            run = run_repo.create_run(
                db,
                session_id=self.session_id,
                owner_email=OWNER,
                request_id=C.new_request_id(),
                action=C.Action.clarify.value,
                input_hash="crash-cancel-takeover",
                base_session_version=session.session_version,
                base_product_version=session.current_product_version,
                now_iso=now_iso,
                deadline_at=int(time.time()) + C.RUN_TIMEOUT_SECONDS,
            )
            # 模拟「运行中收到取消 + 进程随后死亡」：cancel_requested + 租约过期。
            run.status = C.RunStatus.cancel_requested.value
            run.lease_owner = "dead-worker"
            run.lease_epoch = 1
            run.lease_expires_at = int(time.time()) - 30
            run_id = run.id

        with database.session_scope() as db:
            claimed = run_repo.claim_next_run(
                db, worker_id="takeover-worker", now_epoch=int(time.time()), lease_seconds=30
            )
            self.assertIsNotNone(claimed, "租约过期的 cancel_requested Run 必须可被接管")
            self.assertEqual(claimed.id, run_id)
            self.assertEqual(
                claimed.status,
                C.RunStatus.cancel_requested.value,
                "接管不得把取消意图重置为 running（AC-30 / PRD 取消优先）",
            )
            self.assertEqual(claimed.lease_owner, "takeover-worker")
            self.assertEqual(int(claimed.lease_epoch), 2)

        # 接管后的执行必须在第一个安全点直接终结为 cancelled，不执行工作流。
        from workflows import runner as runner_module

        runner_module.execute_run(run_id, worker_id="takeover-worker", renew_thread=False)

        with database.session_scope() as db:
            run = run_repo.get_run(db, run_id)
            self.assertEqual(run.status, C.RunStatus.cancelled.value)
            self.assertIn("取消", run.error_message or "")
            self.assertEqual(len(run_repo.list_steps(db, run_id)), 0, "取消优先：不得创建步骤")
            session = session_repo.require_session(db, self.session_id, owner_email=OWNER)
            self.assertIsNone(session.active_run_id, "取消后必须释放会话占位")


if __name__ == "__main__":
    unittest.main()
