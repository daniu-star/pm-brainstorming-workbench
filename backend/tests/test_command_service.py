"""命令服务集成测试：同步/异步命令、幂等、版本、限流、控制意图、取消与重试。

对应验收：
- AC-03 确认/跳过定义后进入 brainstorming 且不自动触发四角色
- AC-06 同 request_id 幂等（一个 Run / 一条消息 / 同一 receipt；异体同键 409）
- AC-07 并发修改同会话（SESSION_BUSY / VERSION_CONFLICT）
- AC-30 取消 Run 后旧结果不能提交
- AC-37 产品状态变化后重试被拒（RETRY_CONTEXT_CHANGED）
- AC-40 人工更正审计评估写入 Revision 且不调模型
- AC-42 cancel_audit 优先于未完成的 end_audit
"""
import time
import unittest

from domain import state_machine as SM
from domain.contracts import (
    Action,
    AuditStatus,
    BriefStatus,
    CommandRequest,
    EventType,
    MessageStatus,
    RunStatus,
    SessionPhase,
    new_request_id,
    utc_now_iso,
)
from domain.errors import AppError, ErrorCode
from repositories import audit_repo, run_repo, session_repo
from repositories.database import dispose_engine, reset_for_tests, session_scope
from services import command_service
from services.command_service import OutgoingMessage, RunOutcome

OWNER = "owner@example.com"
NOW_ISO = "2026-11-19T00:00:00Z"
# 固定 epoch：同一个用例内所有命令落在同一限流窗口，避免测试抖动。
NOW_EPOCH = 1_763_510_400


class CommandTestCase(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        self._tmp = tempfile.TemporaryDirectory()
        db_path = Path(self._tmp.name, "test.db").as_posix()
        reset_for_tests(f"sqlite+pysqlite:///{db_path}")

    def tearDown(self):
        dispose_engine()
        self._tmp.cleanup()

    # -- helpers ---------------------------------------------------------- #
    def make_session(self, owner: str = OWNER) -> str:
        with session_scope() as db:
            session = session_repo.create_session(
                db, owner_email=owner, problem_statement="做一个自动写周报的工具"
            )
            return session.id

    def submit(
        self,
        session_id: str,
        action: Action,
        payload: dict | None = None,
        *,
        request_id: str | None = None,
        expected_version: int | None = None,
        owner: str = OWNER,
        now_epoch: int = NOW_EPOCH,
    ):
        command = CommandRequest(
            request_id=request_id or new_request_id(),
            action=action,
            expected_session_version=expected_version,
            payload=payload or {},
        )
        with session_scope() as db:
            return command_service.submit_command(
                db,
                owner_email=owner,
                session_id=session_id,
                command=command,
                now_iso=NOW_ISO,
                now_epoch=now_epoch,
            )

    def read_session(self, session_id: str):
        with session_scope() as db:
            return session_repo.require_session(db, session_id, owner_email=OWNER)

    def fill_required_brief(self, session_id: str) -> str:
        """用 revise_brief 填满四个必需字段，返回产品状态版本。"""
        with session_scope() as db:
            state = session_repo.get_product_state(db, session_id)
        fields = {
            "target_users": {"value": "需要每周汇报的项目经理", "status": "proposed"},
            "usage_scenario": {"value": "每周五下午自动汇总本周进展", "status": "proposed"},
            "core_problem": {"value": "手工写周报耗时且遗漏重点", "status": "proposed"},
            "success_criteria": {"value": "写周报时间从 40 分钟降到 5 分钟", "status": "proposed"},
        }
        receipt = self.submit(
            session_id,
            Action.revise_brief,
            {"expected_product_version": state.version, "fields": fields},
        )
        self.assertEqual(receipt.status, RunStatus.succeeded)
        with session_scope() as db:
            return session_repo.get_product_state(db, session_id).version

    def confirmed_session(self) -> tuple[str, int]:
        """澄清完成、已进入 brainstorming 的会话。"""
        session_id = self.make_session()
        version = self.fill_required_brief(session_id)
        self.submit(
            session_id,
            Action.confirm_brief,
            {"expected_product_version": version, "fields": {}},
            expected_version=self.read_session(session_id).session_version,
        )
        with session_scope() as db:
            state = session_repo.get_product_state(db, session_id)
            return session_id, state.version


class AsyncCommandTests(CommandTestCase):
    def test_clarify_creates_queued_run_and_user_message(self):
        session_id = self.make_session()
        receipt = self.submit(session_id, Action.clarify, {"content": "目标用户是谁？"})
        self.assertEqual(receipt.status, RunStatus.queued.value)
        self.assertEqual(receipt.action, Action.clarify)
        self.assertTrue(receipt.events_url.endswith(f"/runs/{receipt.run_id}/events"))

        with session_scope() as db:
            session = session_repo.require_session(db, session_id, owner_email=OWNER)
            self.assertEqual(session.active_run_id, receipt.run_id)
            self.assertEqual(session.session_version, 2)
            messages = session_repo.list_messages(db, session_id)
            # seq=1 是创建会话时的原始消息；提交命令只追加一条人类消息。
            self.assertEqual([m.seq for m in messages], [1, 2])
            self.assertEqual(messages[-1].content, "目标用户是谁？")
            self.assertEqual(messages[-1].sender_kind, "human")

            run = run_repo.get_run(db, receipt.run_id)
            events = run_repo.events_after(db, receipt.run_id)
            self.assertEqual(events[0].type, EventType.run_queued.value)

    def test_clarify_rejects_too_long_input(self):
        session_id = self.make_session()
        with self.assertRaises(AppError) as ctx:
            self.submit(session_id, Action.clarify, {"content": "字" * 8001})
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_REQUEST)

    def test_second_command_while_run_active_is_session_busy(self):
        session_id = self.make_session()
        first = self.submit(session_id, Action.clarify, {"content": "第一问"})
        with self.assertRaises(AppError) as ctx:
            self.submit(session_id, Action.clarify, {"content": "第二问"})
        self.assertEqual(ctx.exception.code, ErrorCode.SESSION_BUSY)
        self.assertEqual(ctx.exception.details["active_run_id"], first.run_id)

    def test_pending_run_does_not_block_control_actions(self):
        session_id = self.make_session()
        self.submit(session_id, Action.clarify, {"content": "第一问"})
        # 会话仍处于 clarifying，end_audit 是控制动作但阶段不允许 -> 依旧是阶段错误而非 SESSION_BUSY
        with self.assertRaises(AppError) as ctx:
            self.submit(
                session_id,
                Action.end_audit,
                {"audit_id": "x" * 32},
            )
        self.assertNotEqual(ctx.exception.code, ErrorCode.SESSION_BUSY)


class IdempotencyTests(CommandTestCase):
    def test_same_request_id_replays_receipt(self):
        session_id = self.make_session()
        request_id = new_request_id()
        first = self.submit(session_id, Action.clarify, {"content": "同一个问题"}, request_id=request_id)
        second = self.submit(session_id, Action.clarify, {"content": "同一个问题"}, request_id=request_id)
        self.assertEqual(first.run_id, second.run_id)
        self.assertTrue(second.reused)
        with session_scope() as db:
            runs = db.execute(
                run_repo.select(run_repo.RunRow).where(run_repo.RunRow.session_id == session_id)
            ).scalars().all()
            self.assertEqual(len(runs), 1)
            messages = session_repo.list_messages(db, session_id)
            self.assertEqual(len(messages), 2)

    def test_same_request_id_with_different_body_conflicts(self):
        session_id = self.make_session()
        request_id = new_request_id()
        self.submit(session_id, Action.clarify, {"content": "原请求"}, request_id=request_id)
        with self.assertRaises(AppError) as ctx:
            self.submit(session_id, Action.clarify, {"content": "不同请求"}, request_id=request_id)
        self.assertEqual(ctx.exception.code, ErrorCode.IDEMPOTENCY_KEY_REUSED)

    def test_idempotent_replay_survives_later_version_change(self):
        session_id = self.make_session()
        request_id = new_request_id()
        first = self.submit(
            session_id,
            Action.add_evidence,
            {"kind": "benchmark", "title": "行业报告", "excerpt": "写周报平均耗时 40 分钟"},
            request_id=request_id,
        )
        second = self.submit(
            session_id,
            Action.add_evidence,
            {"kind": "benchmark", "title": "行业报告", "excerpt": "写周报平均耗时 40 分钟"},
            request_id=request_id,
        )
        self.assertEqual(first.run_id, second.run_id)
        self.assertTrue(second.reused)
        with session_scope() as db:
            rows = session_repo.list_evidence(db, session_id)
            self.assertEqual(len(rows), 1)


class VersionTests(CommandTestCase):
    def test_stale_expected_session_version_conflicts(self):
        session_id = self.make_session()
        with self.assertRaises(AppError) as ctx:
            self.submit(
                session_id,
                Action.skip_clarification,
                {"reason": "先跳过"},
                expected_version=99,
            )
        self.assertEqual(ctx.exception.code, ErrorCode.VERSION_CONFLICT)

    def test_stale_expected_product_version_conflicts_on_revise(self):
        session_id = self.make_session()
        with self.assertRaises(AppError) as ctx:
            self.submit(
                session_id,
                Action.revise_brief,
                {"expected_product_version": 99, "fields": {"core_problem": {"value": "x"}}},
            )
        self.assertEqual(ctx.exception.code, ErrorCode.VERSION_CONFLICT)

    def test_rate_limit_blocks_21st_command(self):
        session_id = self.make_session()
        for index in range(20):
            self.submit(
                session_id,
                Action.add_evidence,
                {"kind": "benchmark", "title": f"证据{index}", "excerpt": "内容"},
            )
        with self.assertRaises(AppError) as ctx:
            self.submit(
                session_id,
                Action.add_evidence,
                {"kind": "benchmark", "title": "第21条", "excerpt": "内容"},
            )
        self.assertEqual(ctx.exception.code, ErrorCode.RATE_LIMITED)

    def test_queue_limit_rejects_fourth_queued_run(self):
        session_ids = [self.make_session() for _ in range(3)]
        for session_id in session_ids:
            self.submit(session_id, Action.clarify, {"content": "问题"})
        fourth = self.make_session()
        with self.assertRaises(AppError) as ctx:
            self.submit(fourth, Action.clarify, {"content": "问题"})
        self.assertEqual(ctx.exception.code, ErrorCode.QUEUE_LIMIT_EXCEEDED)


class SyncCommandTests(CommandTestCase):
    def test_add_evidence_registers_unverified_evidence(self):
        session_id = self.make_session()
        receipt = self.submit(
            session_id,
            Action.add_evidence,
            {"kind": "user_research", "title": "5 位用户访谈", "excerpt": "4 人反馈周报耗时"},
        )
        self.assertEqual(receipt.status, RunStatus.succeeded.value)
        with session_scope() as db:
            rows = session_repo.list_evidence(db, session_id)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].verification_status, "unverified")
            self.assertEqual(len(rows[0].content_hash), 64)
            messages = session_repo.list_messages(db, session_id)
            self.assertTrue(any("已登记证据" in m.content for m in messages))
            runs = run_repo.list_steps(db, receipt.run_id)
            # 同步命令不产生模型步骤
            self.assertEqual(runs, [])

    def test_confirm_brief_requires_required_fields(self):
        session_id = self.make_session()
        with session_scope() as db:
            version = session_repo.get_product_state(db, session_id).version
        with self.assertRaises(AppError) as ctx:
            self.submit(
                session_id,
                Action.confirm_brief,
                {"expected_product_version": version, "fields": {}},
            )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_REQUEST)
        self.assertIn("target_users", ctx.exception.details["missing_fields"])

    def test_confirm_brief_advances_to_brainstorming(self):
        session_id = self.make_session()
        version = self.fill_required_brief(session_id)
        receipt = self.submit(
            session_id,
            Action.confirm_brief,
            {"expected_product_version": version, "fields": {}},
        )
        self.assertEqual(receipt.status, RunStatus.succeeded.value)
        with session_scope() as db:
            session = session_repo.require_session(db, session_id, owner_email=OWNER)
            self.assertEqual(session.phase, SessionPhase.brainstorming.value)
            self.assertEqual(session.brief_status, BriefStatus.confirmed.value)
            state = session_repo.get_product_state(db, session_id)
            self.assertEqual(state.brief.status, BriefStatus.confirmed)
            for field in ("target_users", "usage_scenario", "core_problem", "success_criteria"):
                self.assertEqual(state.brief.known(field), True)
            messages = session_repo.list_messages(db, session_id)
            self.assertTrue(any("产品定义已确认" in m.content for m in messages))

    def test_skip_clarification_marks_brief_skipped(self):
        session_id = self.make_session()
        receipt = self.submit(session_id, Action.skip_clarification, {"reason": "已有明确方案"})
        self.assertEqual(receipt.status, RunStatus.succeeded.value)
        with session_scope() as db:
            session = session_repo.require_session(db, session_id, owner_email=OWNER)
            self.assertEqual(session.phase, SessionPhase.brainstorming.value)
            state = session_repo.get_product_state(db, session_id)
            self.assertEqual(state.brief.status, BriefStatus.skipped)
            self.assertTrue(state.brief.skipped)

    def test_confirm_decision_marks_needs_review_on_revise(self):
        session_id, _version = self.confirmed_session()
        # 直接写入一条已确认决策（模拟 reducer 提交的结果）
        with session_scope() as db:
            session = session_repo.require_session(db, session_id, owner_email=OWNER)
            state = session_repo.get_product_state(db, session_id)
            from domain.contracts import DecisionItem, DecisionStatus

            state.decisions = [
                DecisionItem(
                    id="d" * 32,
                    status=DecisionStatus.confirmed,
                    rationale="用户主要在电脑上写周报，先做网页版",
                )
            ]
            state.version = state.version + 1
            session_repo.save_product_state(db, session, state, now_iso=NOW_ISO)
            version = state.version

        receipt = self.submit(
            session_id,
            Action.revise_brief,
            {
                "expected_product_version": version,
                "fields": {"target_users": {"value": "改为中小企业 HR", "status": "proposed"}},
            },
        )
        self.assertEqual(receipt.status, RunStatus.succeeded.value)
        with session_scope() as db:
            new_state = session_repo.get_product_state(db, session_id)
            self.assertTrue(new_state.decisions[0].needs_review)
            self.assertEqual(new_state.brief.status, BriefStatus.ready)
            self.assertEqual(new_state.brief.known("target_users"), True)
            self.assertEqual(
                new_state.brief.target_users.value, "改为中小企业 HR"
            )


class CancelTests(CommandTestCase):
    def test_cancel_queued_run_terminates_immediately(self):
        session_id = self.make_session()
        receipt = self.submit(session_id, Action.clarify, {"content": "问题"})
        with session_scope() as db:
            view = command_service.cancel_run(db, owner_email=OWNER, run_id=receipt.run_id, now_iso=NOW_ISO)
        self.assertEqual(view.status, RunStatus.cancelled.value)
        with session_scope() as db:
            run = run_repo.get_run(db, receipt.run_id)
            self.assertEqual(run.status, RunStatus.cancelled.value)
            session = session_repo.require_session(db, session_id, owner_email=OWNER)
            self.assertIsNone(session.active_run_id)
            events = run_repo.events_after(db, receipt.run_id)
            self.assertEqual(events[-1].type, EventType.run_cancelled.value)
            self.assertEqual(len([e for e in events if e.type == EventType.run_cancelled.value]), 1)

    def test_cancelled_run_result_cannot_be_committed(self):
        session_id = self.make_session()
        receipt = self.submit(session_id, Action.clarify, {"content": "问题"})
        with session_scope() as db:
            run = run_repo.claim_next_run(db, worker_id="w1", now_epoch=NOW_EPOCH)
            self.assertEqual(run.id, receipt.run_id)
        with session_scope() as db:
            command_service.cancel_run(db, owner_email=OWNER, run_id=receipt.run_id, now_iso=NOW_ISO)

        
        outcome = RunOutcome(
            status=RunStatus.succeeded,
            messages=[
                OutgoingMessage(
                    content="不该被写入的回答",
                    sender_kind="agent",
                    stage="clarify",
                )
            ],
        )
        with session_scope() as db:
            command_service.apply_outcome(
                db,
                run_id=receipt.run_id,
                worker_id="w1",
                lease_epoch=1,
                outcome=outcome,
                now_iso=NOW_ISO,
            )
        with session_scope() as db:
            run = run_repo.get_run(db, receipt.run_id)
            self.assertEqual(run.status, RunStatus.cancelled.value)
            messages = session_repo.list_messages(db, session_id)
            self.assertFalse(any("不该被写入" in m.content for m in messages))
            session = session_repo.require_session(db, session_id, owner_email=OWNER)
            self.assertIsNone(session.active_run_id)

    def test_cancel_deleted_session_is_not_found(self):
        session_id = self.make_session()
        with session_scope() as db:
            session = session_repo.require_session(db, session_id, owner_email=OWNER)
            session_repo.soft_delete_session(db, session, now_iso=NOW_ISO)
        with self.assertRaises(AppError) as ctx:
            self.submit(session_id, Action.clarify, {"content": "问题"})
        self.assertEqual(ctx.exception.code, ErrorCode.NOT_FOUND)


class OwnershipTests(CommandTestCase):
    def test_other_owner_cannot_submit(self):
        session_id = self.make_session()
        with self.assertRaises(AppError) as ctx:
            self.submit(session_id, Action.clarify, {"content": "问题"}, owner="other@example.com")
        self.assertEqual(ctx.exception.code, ErrorCode.FORBIDDEN)


class RetryTests(CommandTestCase):
    def _failed_run(self, session_id: str) -> str:
        receipt = self.submit(session_id, Action.clarify, {"content": "问题"})
        with session_scope() as db:
            run_repo.claim_next_run(db, worker_id="w1", now_epoch=NOW_EPOCH)
        from services.command_service import RunOutcome

        with session_scope() as db:
            command_service.apply_outcome(
                db,
                run_id=receipt.run_id,
                worker_id="w1",
                lease_epoch=1,
                outcome=RunOutcome(status=RunStatus.failed, error_code=ErrorCode.MODEL_OUTPUT_INVALID),
                now_iso=NOW_ISO,
            )
        return receipt.run_id

    def test_retry_failed_run_creates_child_run(self):
        session_id = self.make_session()
        failed_id = self._failed_run(session_id)
        with session_scope() as db:
            receipt = command_service.retry_run(
                db,
                owner_email=OWNER,
                run_id=failed_id,
                request_id=new_request_id(),
                now_iso=NOW_ISO,
                now_epoch=NOW_EPOCH,
            )
        self.assertNotEqual(receipt.run_id, failed_id)
        with session_scope() as db:
            child = run_repo.get_run(db, receipt.run_id)
            self.assertEqual(child.parent_run_id, failed_id)
            self.assertEqual(child.status, RunStatus.queued.value)

    def test_retry_rejects_when_product_state_changed(self):
        session_id = self.make_session()
        failed_id = self._failed_run(session_id)
        # 推进产品状态版本
        self.fill_required_brief(session_id)
        with self.assertRaises(AppError) as ctx:
            with session_scope() as db:
                command_service.retry_run(
                    db,
                    owner_email=OWNER,
                    run_id=failed_id,
                    request_id=new_request_id(),
                    now_iso=NOW_ISO,
                    now_epoch=NOW_EPOCH,
                )
        self.assertEqual(ctx.exception.code, ErrorCode.RETRY_CONTEXT_CHANGED)

    def test_retry_rejects_succeeded_run(self):
        session_id = self.make_session()
        receipt = self.submit(
            session_id,
            Action.add_evidence,
            {"kind": "benchmark", "title": "证据", "excerpt": "内容"},
        )
        with self.assertRaises(AppError) as ctx:
            with session_scope() as db:
                command_service.retry_run(
                    db,
                    owner_email=OWNER,
                    run_id=receipt.run_id,
                    request_id=new_request_id(),
                    now_iso=NOW_ISO,
                    now_epoch=NOW_EPOCH,
                )
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_REQUEST)


class ApplyOutcomeTests(CommandTestCase):
    def test_apply_outcome_writes_state_message_and_single_terminal_event(self):
        session_id = self.make_session()
        receipt = self.submit(session_id, Action.clarify, {"content": "问题"})
        with session_scope() as db:
            run_repo.claim_next_run(db, worker_id="w1", now_epoch=NOW_EPOCH)

        
        with session_scope() as db:
            state = session_repo.get_product_state(db, session_id)
            state.version = state.version + 1
            state.brief.target_users.value = "项目经理"
            state.brief.target_users.status = "proposed"
            outcome = RunOutcome(
                status=RunStatus.succeeded,
                product_state=state,
                messages=[
                    OutgoingMessage(
                        content="根据你的描述，目标用户应该是项目经理。",
                        sender_kind="agent",
                        role_code="coach",
                        stage="clarify",
                    )
                ],
                result_product_version=state.version,
            )
            command_service.apply_outcome(
                db,
                run_id=receipt.run_id,
                worker_id="w1",
                lease_epoch=1,
                outcome=outcome,
                now_iso=NOW_ISO,
            )

        with session_scope() as db:
            run = run_repo.get_run(db, receipt.run_id)
            self.assertEqual(run.status, RunStatus.succeeded.value)
            events = run_repo.events_after(db, receipt.run_id)
            terminal = [
                e
                for e in events
                if e.type in {EventType.run_completed.value, EventType.run_failed.value, EventType.run_cancelled.value}
            ]
            self.assertEqual(len(terminal), 1)
            self.assertEqual(terminal[0].type, EventType.run_completed.value)
            self.assertTrue(any(e.type == EventType.product_state_updated.value for e in events))
            # 事件序号连续
            self.assertEqual([e.seq for e in events], list(range(1, len(events) + 1)))
            session = session_repo.require_session(db, session_id, owner_email=OWNER)
            self.assertIsNone(session.active_run_id)
            self.assertEqual(session.current_product_version, 2)
            state = session_repo.get_product_state(db, session_id)
            self.assertEqual(state.brief.target_users.value, "项目经理")

    def test_apply_outcome_twice_applies_only_once(self):
        session_id = self.make_session()
        receipt = self.submit(session_id, Action.clarify, {"content": "问题"})
        with session_scope() as db:
            run_repo.claim_next_run(db, worker_id="w1", now_epoch=NOW_EPOCH)
        
        outcome = RunOutcome(
            status=RunStatus.succeeded,
            messages=[OutgoingMessage(content="唯一回答", sender_kind="agent", stage="clarify")],
        )
        for _ in range(2):
            with session_scope() as db:
                command_service.apply_outcome(
                    db,
                    run_id=receipt.run_id,
                    worker_id="w1",
                    lease_epoch=1,
                    outcome=outcome,
                    now_iso=NOW_ISO,
                )
        with session_scope() as db:
            messages = session_repo.list_messages(db, session_id)
            self.assertEqual(sum(1 for m in messages if "唯一回答" in m.content), 1)

    def test_apply_outcome_with_stale_lease_is_rejected(self):
        session_id = self.make_session()
        receipt = self.submit(session_id, Action.clarify, {"content": "问题"})
        with session_scope() as db:
            run_repo.claim_next_run(db, worker_id="w1", now_epoch=NOW_EPOCH)
        from services.command_service import RunOutcome

        with self.assertRaises(AppError) as ctx:
            with session_scope() as db:
                command_service.apply_outcome(
                    db,
                    run_id=receipt.run_id,
                    worker_id="w2",
                    lease_epoch=9,
                    outcome=RunOutcome(status=RunStatus.succeeded),
                    now_iso=NOW_ISO,
                )
        self.assertEqual(ctx.exception.code, ErrorCode.LEASE_LOST)


class ControlIntentTests(CommandTestCase):
    def _auditing_session(self) -> tuple[str, str]:
        session_id, _version = self.confirmed_session()
        with session_scope() as db:
            audit = audit_repo.create_audit(
                db, session_id=session_id, product_version=1, now_iso=NOW_ISO
            )
            audit_repo.update_audit(db, audit, now_iso=NOW_ISO, status=AuditStatus.active)
            session = session_repo.require_session(db, session_id, owner_email=OWNER)
            session_repo.touch_session(
                db, session, now_iso=NOW_ISO, phase=SessionPhase.auditing, current_audit_id=audit.id
            )
            return session_id, audit.id

    def test_end_audit_schedules_closing_run_when_idle(self):
        session_id, audit_id = self._auditing_session()
        receipt = self.submit(session_id, Action.end_audit, {"audit_id": audit_id})
        self.assertIsNotNone(receipt.control_id)
        self.assertEqual(receipt.status, "scheduled")
        with session_scope() as db:
            audit = audit_repo.get_audit(db, audit_id)
            self.assertEqual(audit.status, AuditStatus.closing.value)
            closing = run_repo.get_run(db, receipt.related_run_id)
            self.assertEqual(closing.action, Action.end_audit.value)
            session = session_repo.require_session(db, session_id, owner_email=OWNER)
            self.assertEqual(session.active_run_id, closing.id)

    def test_cancel_audit_supersedes_pending_end(self):
        session_id, audit_id = self._auditing_session()
        end_receipt = self.submit(session_id, Action.end_audit, {"audit_id": audit_id})
        closing_run_id = end_receipt.related_run_id
        self.assertIsNotNone(closing_run_id)

        # AC-42：end 已接收但未完成时，cancel 必须优先 —— 旧意图失效、审计取消。
        cancel_receipt = self.submit(session_id, Action.cancel_audit, {"audit_id": audit_id})
        self.assertIn(cancel_receipt.status, ("scheduled", "applied"))
        with session_scope() as db:
            old = audit_repo.get_control(db, end_receipt.control_id)
            self.assertEqual(old.status, "failed")
            self.assertEqual(old.error_code, ErrorCode.CONTROL_SUPERSEDED)
            audit = audit_repo.get_audit(db, audit_id)
            self.assertEqual(audit.status, AuditStatus.cancelled.value)
            # 排队中的 closing Run 没有人会再领取，必须当场终结（FR-03 queued → cancelled）
            closing_run = run_repo.get_run(db, closing_run_id)
            self.assertEqual(closing_run.status, RunStatus.cancelled.value)
            terminal = [
                e
                for e in run_repo.events_after(db, closing_run_id)
                if e.type == EventType.run_cancelled.value
            ]
            self.assertEqual(len(terminal), 1)

        # 扫描器把取消意图落实并把阶段推回 brainstorming。
        with session_scope() as db:
            command_service.reconcile_control_intents(db, now_iso=NOW_ISO, now_epoch=NOW_EPOCH)
        with session_scope() as db:
            applied = audit_repo.get_control(db, cancel_receipt.control_id)
            self.assertEqual(applied.status, "applied")
            session = session_repo.require_session(db, session_id, owner_email=OWNER)
            self.assertIsNone(session.active_run_id)
            self.assertEqual(session.phase, SessionPhase.brainstorming.value)

    def test_answer_audit_requires_open_question(self):
        session_id, audit_id = self._auditing_session()
        with self.assertRaises(AppError) as ctx:
            self.submit(
                session_id,
                Action.answer_audit,
                {"audit_id": audit_id, "question_id": "q" * 32, "answer": "有证据"},
            )
        self.assertEqual(ctx.exception.code, ErrorCode.NOT_FOUND)


if __name__ == "__main__":
    unittest.main()
