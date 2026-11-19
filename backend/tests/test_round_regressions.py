"""第 5 轮验收缺口的产品级回归（修复前红、修复后绿）。

对应评测组 P1-1 / P1-4 与审计发现 AUD-01 / BRIEF-01 / CANVAS-01：

* 审计开场/回答阶段的模型鉴权失败（401）必须落库为 ``audit.failed`` 并给出
  可恢复状态，而不是只终结 Run、把审计永久留在 ``opening``；
* 已确认简报修订必填字段后，再确认入口（页面「确认字段修订」）复用
  ``revise_brief`` 显式提交 ``confirmed``，必须把字段并入已确认版本并推进版本号；
* 画布对 ``needs_review`` 决策的再次确认必须清除复核标记（用户可见路径）。

这些用例复用现有真实 sqlite 测试基类，不联网、不起 Worker 进程。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.llm_client import LLMAuthError  # noqa: E402
from domain import contracts as C  # noqa: E402
from domain.contracts import (  # noqa: E402
    AUDIT_DIMENSIONS,
    Action,
    Assessment,
    AuditStatus,
    BriefFieldStatus,
    BriefStatus,
    DecisionItem,
    DecisionStatus,
    OptionItem,
    RunStatus,
    SessionPhase,
)
from repositories import session_repo  # noqa: E402
from repositories.database import session_scope  # noqa: E402
from tests.test_audit_workflow import (  # noqa: E402
    OWNER,
    AuditWorkflowTestCase,
    FakeLLM,
)
from tests.test_command_service import CommandTestCase  # noqa: E402
from workflows import audit as audit_workflow  # noqa: E402


class AuditOpenTransportFailureTests(AuditWorkflowTestCase):
    """开场阶段上游鉴权失败必须变成可恢复的审计失败（P1-1 / AUD-01）。"""

    async def test_open_llm_auth_error_marks_audit_failed_and_returns_to_brainstorming(
        self,
    ) -> None:
        audit_id = self.create_audit(status=AuditStatus.opening)
        run_id, lease_epoch = self.create_run(action="start_audit", audit_id=audit_id)
        llm = FakeLLM(
            {audit_workflow.PURPOSE_OPEN: [LLMAuthError("上游 401：API Key 无效")]}
        )
        ctx = self.make_ctx(run_id, llm=llm, task_kind=C.TaskKind.audit_open)

        outcome = await audit_workflow.run_audit_open(ctx)

        self.assertEqual(outcome.status, RunStatus.failed)
        self.assertEqual(outcome.error_code, LLMAuthError.code)
        self.assertIn("API Key", outcome.error_message)
        self.assertIsNone(outcome.product_state)

        self.commit_outcome(run_id, lease_epoch, outcome)

        audit = self.reload_audit(audit_id)
        self.assertEqual(audit.status, AuditStatus.failed.value)
        self.assertEqual(audit.end_reason, C.EndReason.failed.value)
        self.assertEqual(int(audit.question_count), 0)
        self.assertEqual(self.questions(audit_id), [])
        self.assertEqual(self.session_row().phase, SessionPhase.brainstorming.value)
        self.assertEqual(self.run_row(run_id).status, RunStatus.failed.value)


class AuditAnswerTransportFailureTests(AuditWorkflowTestCase):
    """回答阶段上游鉴权失败必须留下可重试的 pending_continuation（P1-1 / AUD-01）。"""

    async def test_answer_llm_auth_error_marks_pending_continuation(self) -> None:
        audit_id = self.create_audit(status=AuditStatus.active)
        question_id, answer_id = self.add_question(
            audit_id, ordinal=1, dimension=AUDIT_DIMENSIONS[0], answer="一个回答"
        )
        run_id, lease_epoch = self.create_run(
            action="answer_audit", audit_id=audit_id, question_id=question_id
        )
        llm = FakeLLM(
            {audit_workflow.PURPOSE_ANSWER: [LLMAuthError("上游 401：API Key 无效")]}
        )
        ctx = self.make_ctx(
            run_id, llm=llm, current_message_id=answer_id, task_kind=C.TaskKind.audit_answer
        )

        outcome = await audit_workflow.run_audit_answer(ctx)

        self.assertEqual(outcome.status, RunStatus.failed)
        self.assertEqual(outcome.error_code, LLMAuthError.code)
        self.assertIn("API Key", outcome.error_message)

        self.commit_outcome(run_id, lease_epoch, outcome)

        audit = self.reload_audit(audit_id)
        self.assertEqual(audit.status, AuditStatus.active.value)
        self.assertEqual(int(audit.pending_continuation), 1)
        self.assertIsNone(audit.active_question_id)
        self.assertEqual(int(audit.question_count), 1)
        self.assertEqual(
            self.question(audit_id, question_id).assessment,
            Assessment.awaiting_answer.value,
            "鉴权失败不得改写已提交回答",
        )
        self.assertEqual(self.session_row().phase, SessionPhase.auditing.value)


class BriefReconfirmTests(CommandTestCase):
    """已确认简报修订后的再确认闭环（P1-4 / BRIEF-01）。

    前端在 ``brainstorming`` 阶段没有 ``confirm_brief`` 入口（状态机决定），
    再确认走的是 ``revise_brief``：用户点「确认字段修订」时显式提交
    ``status=confirmed``，后端必须把字段并入已确认版本并推进产品版本，
    不能出现「头部已确认 / 字段待确认」并存。
    """

    def test_confirm_drifted_fields_after_confirmed_brief_revision(self) -> None:
        session_id, initial_version = self.confirmed_session()
        with session_scope() as db:
            state = session_repo.get_product_state(db, session_id)
            self.assertEqual(state.brief.status, BriefStatus.confirmed.value)
            self.assertEqual(
                state.brief.target_users.status, BriefFieldStatus.confirmed.value
            )

        # 旧客户端/历史数据：修订已确认简报时按缺省 proposed 落库，产生字段漂移。
        revise = self.submit(
            session_id,
            Action.revise_brief,
            {
                "expected_product_version": initial_version,
                "fields": {
                    "target_users": {
                        "value": "改为中小企业 HR",
                        "status": BriefFieldStatus.proposed.value,
                    }
                },
            },
        )
        self.assertEqual(revise.status, RunStatus.succeeded.value)

        with session_scope() as db:
            drifted = session_repo.get_product_state(db, session_id)
            self.assertEqual(drifted.brief.status, BriefStatus.confirmed.value)
            self.assertEqual(
                drifted.brief.target_users.status,
                BriefFieldStatus.proposed.value,
                "缺省提交应保留待确认状态，页面据此渲染「确认字段修订」",
            )
            drift_version = drifted.version

        # 页面「确认字段修订」按钮：复用 revise_brief，把漂移字段显式提交 confirmed。
        receipt = self.submit(
            session_id,
            Action.revise_brief,
            {
                "expected_product_version": drift_version,
                "fields": {
                    "target_users": {
                        "value": "改为中小企业 HR",
                        "status": BriefFieldStatus.confirmed.value,
                    }
                },
            },
        )
        self.assertEqual(receipt.status, RunStatus.succeeded.value)

        with session_scope() as db:
            state = session_repo.get_product_state(db, session_id)
            session = session_repo.require_session(db, session_id, owner_email=OWNER)
            self.assertEqual(state.brief.status, BriefStatus.confirmed.value)
            self.assertEqual(state.brief.target_users.value, "改为中小企业 HR")
            self.assertEqual(
                state.brief.target_users.status, BriefFieldStatus.confirmed.value
            )
            self.assertEqual(state.version, drift_version + 1)
            self.assertEqual(session.phase, SessionPhase.brainstorming.value)


class DecisionReviewClearTests(CommandTestCase):
    """画布 needs_review 的再次确认必须清除标记（P1-2 / CANVAS-01 后端合同）。"""

    def test_confirm_decision_clears_needs_review_for_confirmed_decision(self) -> None:
        session_id, _version = self.confirmed_session()
        with session_scope() as db:
            session = session_repo.require_session(db, session_id, owner_email=OWNER)
            state = session_repo.get_product_state(db, session_id)
            state.options = [
                OptionItem(id="o" * 32, title="先做网页版", summary="复用现有 Web 技术栈")
            ]
            state.decisions = [
                DecisionItem(
                    id="d" * 32,
                    status=DecisionStatus.confirmed,
                    rationale="用户主要在电脑上写周报，先做网页版",
                    option_id="o" * 32,
                )
            ]
            state.version = state.version + 1
            session_repo.save_product_state(db, session, state, now_iso="2026-11-19T00:00:00Z")
            version = state.version

        revise = self.submit(
            session_id,
            Action.revise_brief,
            {
                "expected_product_version": version,
                "fields": {"target_users": {"value": "改为中小企业 HR", "status": "proposed"}},
            },
        )
        self.assertEqual(revise.status, RunStatus.succeeded.value)

        with session_scope() as db:
            state = session_repo.get_product_state(db, session_id)
            self.assertTrue(state.decisions[0].needs_review)
            review_version = state.version

        receipt = self.submit(
            session_id,
            Action.confirm_decision,
            {
                "expected_product_version": review_version,
                "decision_id": "d" * 32,
                "option_id": "o" * 32,
                "rationale": "复核后维持网页版",
            },
            expected_version=self.read_session(session_id).session_version,
        )
        self.assertEqual(receipt.status, RunStatus.succeeded.value)

        with session_scope() as db:
            state = session_repo.get_product_state(db, session_id)
            self.assertFalse(state.decisions[0].needs_review)
            self.assertEqual(state.decisions[0].status, DecisionStatus.confirmed.value)
            self.assertEqual(state.decisions[0].option_id, "o" * 32)


if __name__ == "__main__":
    unittest.main()
