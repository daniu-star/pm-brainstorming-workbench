"""状态机测试（PRD §5.3、FR-03、FR-07）：阶段/动作/迁移/终结事件必须唯一。

对应验收：AC-03（确认后才能共创）、AC-25（第 18 问后禁止第 19 问）、AC-30（取消后不能提交）、
AC-42（cancel 优先于未完成的 end_audit）。
"""
import unittest

from domain import state_machine as SM
from domain.contracts import (
    Action,
    AuditStatus,
    ControlStatus,
    EndReason,
    EventType,
    RunStatus,
    SessionPhase,
    StepStatus,
)
from domain.errors import AppError, ErrorCode


class ActionPhaseTests(unittest.TestCase):
    def test_clarify_only_in_clarifying(self):
        for phase in SessionPhase:
            if phase is SessionPhase.clarifying:
                SM.validate_action_for_phase(Action.clarify, phase)
            else:
                with self.assertRaises(AppError) as ctx:
                    SM.validate_action_for_phase(Action.clarify, phase)
                self.assertEqual(ctx.exception.code, ErrorCode.INVALID_REQUEST)

    def test_brainstorm_allowed_after_brief_confirmed(self):
        SM.validate_action_for_phase(Action.brainstorm, SessionPhase.brainstorming)
        SM.validate_action_for_phase(Action.brainstorm, SessionPhase.audit_completed)

    def test_brainstorm_rejected_during_audit(self):
        with self.assertRaises(AppError):
            SM.validate_action_for_phase(Action.brainstorm, SessionPhase.auditing)

    def test_revise_brief_during_audit_returns_audit_active(self):
        with self.assertRaises(AppError) as ctx:
            SM.validate_action_for_phase(Action.revise_brief, SessionPhase.auditing)
        self.assertEqual(ctx.exception.code, ErrorCode.AUDIT_ACTIVE)

    def test_answer_audit_after_close_returns_audit_closed(self):
        with self.assertRaises(AppError) as ctx:
            SM.validate_action_for_phase(Action.answer_audit, SessionPhase.audit_completed)
        self.assertEqual(ctx.exception.code, ErrorCode.AUDIT_CLOSED)

    def test_start_audit_while_auditing_is_audit_active(self):
        with self.assertRaises(AppError) as ctx:
            SM.validate_action_for_phase(Action.start_audit, SessionPhase.auditing)
        self.assertEqual(ctx.exception.code, ErrorCode.AUDIT_ACTIVE)

    def test_add_evidence_allowed_in_every_phase(self):
        for phase in SessionPhase:
            SM.validate_action_for_phase(Action.add_evidence, phase)

    def test_control_actions_only_during_audit(self):
        for action in (Action.end_audit, Action.cancel_audit):
            SM.validate_action_for_phase(action, SessionPhase.auditing)
            for phase in (SessionPhase.clarifying, SessionPhase.brainstorming, SessionPhase.audit_completed):
                with self.assertRaises(AppError):
                    SM.validate_action_for_phase(action, phase)


class RunTransitionTests(unittest.TestCase):
    def test_queued_cannot_jump_to_succeeded(self):
        self.assertFalse(SM.can_transition_run(RunStatus.queued, RunStatus.succeeded))
        with self.assertRaises(AppError):
            SM.validate_run_transition(RunStatus.queued, RunStatus.succeeded)

    def test_queued_to_running_then_terminal(self):
        self.assertTrue(SM.can_transition_run(RunStatus.queued, RunStatus.running))
        for target in (
            RunStatus.succeeded,
            RunStatus.partial_succeeded,
            RunStatus.failed,
            RunStatus.cancel_requested,
        ):
            self.assertTrue(SM.can_transition_run(RunStatus.running, target))

    def test_cancel_requested_can_only_finish_cancelled_or_failed(self):
        self.assertTrue(SM.can_transition_run(RunStatus.cancel_requested, RunStatus.cancelled))
        self.assertFalse(SM.can_transition_run(RunStatus.cancel_requested, RunStatus.succeeded))

    def test_terminal_states_have_no_outgoing(self):
        for status in RunStatus:
            if SM.is_terminal_run(status):
                self.assertEqual(SM.RUN_TRANSITIONS[status], frozenset(), msg=status.value)

    def test_terminal_statuses(self):
        terminal = {s for s in RunStatus if SM.is_terminal_run(s)}
        self.assertEqual(
            terminal,
            {RunStatus.succeeded, RunStatus.partial_succeeded, RunStatus.failed, RunStatus.cancelled},
        )


class TerminalEventTests(unittest.TestCase):
    def test_every_terminal_run_status_has_exactly_one_event(self):
        for status in RunStatus:
            event = SM.terminal_event_for(status)
            if SM.is_terminal_run(status):
                self.assertIsNotNone(event, msg=status.value)
                self.assertIn(event, {EventType.run_completed, EventType.run_failed, EventType.run_cancelled})
            else:
                self.assertIsNone(event, msg=status.value)

    def test_partial_succeeded_maps_to_run_completed(self):
        self.assertEqual(
            SM.terminal_event_for(RunStatus.partial_succeeded), EventType.run_completed
        )

    def test_cancelled_has_distinct_event(self):
        self.assertEqual(SM.terminal_event_for(RunStatus.cancelled), EventType.run_cancelled)
        self.assertEqual(SM.terminal_event_for(RunStatus.failed), EventType.run_failed)


class StepTransitionTests(unittest.TestCase):
    def test_pending_to_running(self):
        self.assertTrue(SM.can_transition_step(StepStatus.pending, StepStatus.running))
        self.assertFalse(SM.can_transition_step(StepStatus.pending, StepStatus.succeeded))

    def test_running_to_terminal(self):
        for target in (StepStatus.succeeded, StepStatus.failed, StepStatus.interrupted, StepStatus.cancelled):
            self.assertTrue(SM.can_transition_step(StepStatus.running, target))

    def test_interrupted_can_be_retried(self):
        self.assertTrue(SM.can_transition_step(StepStatus.interrupted, StepStatus.running))
        self.assertFalse(SM.can_transition_step(StepStatus.interrupted, StepStatus.pending))


class AuditTransitionTests(unittest.TestCase):
    def test_happy_path(self):
        for current, target in (
            (AuditStatus.created, AuditStatus.opening),
            (AuditStatus.opening, AuditStatus.active),
            (AuditStatus.active, AuditStatus.closing),
            (AuditStatus.closing, AuditStatus.completed),
        ):
            self.assertTrue(SM.can_transition_audit(current, target), msg=f"{current}->{target}")

    def test_opening_failure(self):
        self.assertTrue(SM.can_transition_audit(AuditStatus.opening, AuditStatus.failed))

    def test_closing_failure_can_retry_report(self):
        self.assertTrue(SM.can_transition_audit(AuditStatus.closing, AuditStatus.failed))

    def test_active_can_cancel(self):
        self.assertTrue(SM.can_transition_audit(AuditStatus.active, AuditStatus.cancelled))

    def test_completed_is_terminal(self):
        self.assertEqual(SM.AUDIT_TRANSITIONS[AuditStatus.completed], frozenset())

    def test_answers_only_when_active(self):
        self.assertTrue(SM.audit_accepts_answers(AuditStatus.active))
        for status in (AuditStatus.created, AuditStatus.opening, AuditStatus.closing,
                       AuditStatus.completed, AuditStatus.cancelled, AuditStatus.failed):
            self.assertFalse(SM.audit_accepts_answers(status), msg=status.value)


class ControlTests(unittest.TestCase):
    def test_cancel_supersedes_unfinished_end(self):
        self.assertTrue(SM.cancel_supersedes_end(ControlStatus.scheduled))
        self.assertTrue(SM.cancel_supersedes_end(ControlStatus.accepted))
        self.assertFalse(SM.cancel_supersedes_end(ControlStatus.applied))
        self.assertFalse(SM.cancel_supersedes_end(ControlStatus.failed))


class PhaseHelperTests(unittest.TestCase):
    def test_confirm_moves_to_brainstorming(self):
        self.assertEqual(SM.phase_after_confirm(), SessionPhase.brainstorming)
        self.assertEqual(SM.phase_after_skip(), SessionPhase.brainstorming)

    def test_audit_start_moves_to_auditing(self):
        self.assertEqual(SM.phase_after_audit_start(), SessionPhase.auditing)

    def test_audit_end_moves_to_audit_completed(self):
        self.assertEqual(
            SM.phase_after_audit_end(EndReason.completed), SessionPhase.audit_completed
        )
        # 未完成的取消/失败退回共创，避免锁死会话
        self.assertEqual(
            SM.phase_after_audit_end(EndReason.cancelled), SessionPhase.brainstorming
        )
        self.assertEqual(
            SM.phase_after_audit_end(EndReason.failed), SessionPhase.brainstorming
        )

    def test_after_audit_completed_brainstorming_allowed(self):
        self.assertEqual(
            SM.phase_after_brainstorm(SessionPhase.audit_completed), SessionPhase.brainstorming
        )
        self.assertEqual(
            SM.phase_after_brainstorm(SessionPhase.brainstorming), SessionPhase.brainstorming
        )


if __name__ == "__main__":
    unittest.main()
