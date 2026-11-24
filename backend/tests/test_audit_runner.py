"""``workflows.runner`` 执行审计工作流的端到端回归（FR-03 / FR-07）。

覆盖三条 model 路径，均从 ``command_service.submit_command`` 提交开始，
由真实 runner（``execute_run``）领取并提交结果，只把 LLM 换成假件：

* ``audit_open``  → ``workflows.audit.run_audit_open``：写第 1 个问题、
  audit 进入 active、会话进入 auditing；
* ``audit_answer`` → ``run_audit_answer``：问题评估落库、生成下一个问题；
* ``audit_report``（closing Run，由 ``end_audit`` 控制意图创建）→
  ``run_audit_report``：模型生成报告、audit completed、会话 audit_completed。

全部使用临时 sqlite 与假 LLM，不联网、不起真实 Worker 进程。
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from domain.contracts import (  # noqa: E402
    AUDIT_DIMENSIONS,
    Action,
    AuditStatus,
    CommandRequest,
    RunStatus,
    SessionPhase,
    StepStatus,
    new_id,
)
from repositories import audit_repo, database, run_repo, session_repo  # noqa: E402
from services import command_service  # noqa: E402
from tests.test_audit_workflow import (  # noqa: E402
    FakeLLM,
    _answer_payload,
    _report_payload,
)
from tests.test_runner import OWNER, RunnerTestCase  # noqa: E402
from workflows import audit as audit_workflow  # noqa: E402

OPEN_EVENT_SEQUENCE = [
    "run.queued",
    "run.started",
    "step.started",
    "audit.updated",
    "run.completed",
]


def _open_payload() -> dict[str, Any]:
    """构造一份合法的审计开场问题 JSON。"""

    return {
        "dimension": AUDIT_DIMENSIONS[0],
        "question_text": "谁最需要这个工具？为什么？",
        "issue_refs": [],
        "evidence_refs": [],
        "rationale": "先锁定问题真实性",
    }


class AuditRunnerTestCase(RunnerTestCase):
    """在 runner 层驱动审计命令的公共脚手架。"""

    def _submit(self, session_id: str, action: Action, payload: dict[str, Any]):
        with database.session_scope() as db:
            session = session_repo.get_session(db, session_id)
            self.assertIsNotNone(session, "会话应存在")
            command = CommandRequest(
                request_id=new_id(),
                action=action,
                payload=payload,
                expected_session_version=int(session.session_version),
            )
            return command_service.submit_command(
                db,
                owner_email=OWNER,
                session_id=session_id,
                command=command,
            )

    def _open_audit(self) -> tuple[str, str, str, str]:
        """跑完开场 Run，返回 ``(session_id, run_id, audit_id, question_id)``。"""

        session_id = self.create_session()
        self._submit(session_id, Action.skip_clarification, {"reason": "测试跳过澄清"})
        receipt = self._submit(session_id, Action.start_audit, {})
        self.claim(receipt.run_id)

        fake = FakeLLM({audit_workflow.PURPOSE_OPEN: [_open_payload()]})
        self.run_once(receipt.run_id, llm=fake)

        with database.session_scope() as db:
            run = run_repo.get_run(db, receipt.run_id)
            self.assertIsNotNone(run)
            audit = audit_repo.get_audit(db, run.audit_id)
            self.assertIsNotNone(audit)
            question_id = audit.active_question_id
        return session_id, receipt.run_id, run.audit_id, question_id

    def _answer_question(self, session_id: str, audit_id: str, question_id: str) -> str:
        """回答当前问题，返回 Run id。"""

        receipt = self._submit(
            session_id,
            Action.answer_audit,
            {
                "audit_id": audit_id,
                "question_id": question_id,
                "answer": "给独立设计师用，他们需要在没有灵感时快速获得方向。",
            },
        )
        self.claim(receipt.run_id)
        fake = FakeLLM({audit_workflow.PURPOSE_ANSWER: [_answer_payload()]})
        self.run_once(receipt.run_id, llm=fake)
        return receipt.run_id

    def _reload_audit(self, audit_id: str):
        with database.session_scope() as db:
            return audit_repo.get_audit(db, audit_id)

    def _questions(self, audit_id: str):
        with database.session_scope() as db:
            return list(audit_repo.questions_for_audit(db, audit_id))

    def _session_phase(self, session_id: str) -> str:
        with database.session_scope() as db:
            session = session_repo.get_session(db, session_id)
            self.assertIsNotNone(session)
            return session.phase


class AuditOpenRunnerTests(AuditRunnerTestCase):
    def test_audit_open_via_runner_commits_question_and_activates_audit(self) -> None:
        session_id, run_id, audit_id, question_id = self._open_audit()

        with database.session_scope() as db:
            run = run_repo.get_run(db, run_id)
        self.assertEqual(run.status, RunStatus.succeeded.value)
        self.assertIsNone(run.error_code)
        self.assertEqual(run.audit_id, audit_id)

        step = self.get_step(run_id, "audit.question")
        self.assertIsNotNone(step, "开场步骤必须可审计")
        self.assertEqual(step.status, StepStatus.succeeded.value)
        self.assertEqual(step.attempt_count, 1)

        audit = self._reload_audit(audit_id)
        self.assertEqual(audit.status, AuditStatus.active.value)
        self.assertEqual(int(audit.question_count), 1)
        questions = self._questions(audit_id)
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0].id, question_id)
        self.assertEqual(questions[0].dimension, AUDIT_DIMENSIONS[0])
        self.assertEqual(self._session_phase(session_id), SessionPhase.auditing.value)

        self.assertEqual(
            [str(event.type) for event in self.events(run_id)],
            OPEN_EVENT_SEQUENCE,
            "开场 Run 的事件序列必须稳定",
        )
        self.assertEqual(len(self.terminal_events(run_id)), 1, "终结事件必须恰好一个")


class AuditAnswerRunnerTests(AuditRunnerTestCase):
    def test_audit_answer_via_runner_records_assessment_and_next_question(self) -> None:
        session_id, _, audit_id, question_id = self._open_audit()

        run_id = self._answer_question(session_id, audit_id, question_id)

        with database.session_scope() as db:
            run = run_repo.get_run(db, run_id)
        self.assertEqual(run.status, RunStatus.succeeded.value)
        self.assertIsNone(run.error_code)

        step = self.get_step(run_id, "audit.assess")
        self.assertIsNotNone(step)
        self.assertEqual(step.status, StepStatus.succeeded.value)

        audit = self._reload_audit(audit_id)
        self.assertEqual(audit.status, AuditStatus.active.value)
        self.assertEqual(int(audit.question_count), 2, "应追问下一个问题")
        self.assertIsNotNone(audit.active_question_id)

        questions = self._questions(audit_id)
        self.assertEqual(len(questions), 2)
        first = questions[0]
        self.assertEqual(first.assessment, "gap")
        self.assertTrue(str(first.rationale or "").strip(), "评估理由必须落库")
        self.assertTrue(first.answer_message_id, "回答消息必须落库")
        self.assertEqual(self._session_phase(session_id), SessionPhase.auditing.value)

        self.assertEqual(len(self.terminal_events(run_id)), 1, "终结事件必须恰好一个")


class AuditReportRunnerTests(AuditRunnerTestCase):
    def test_closing_run_generates_model_report_and_completes_audit(self) -> None:
        session_id, _, audit_id, question_id = self._open_audit()
        self._answer_question(session_id, audit_id, question_id)

        control = self._submit(session_id, Action.end_audit, {"audit_id": audit_id})
        self.assertIsNotNone(control.related_run_id, "end_audit 必须创建 closing Run")
        self.claim(control.related_run_id)

        fake = FakeLLM({audit_workflow.PURPOSE_REPORT: [_report_payload()]})
        self.run_once(control.related_run_id, llm=fake)

        with database.session_scope() as db:
            run = run_repo.get_run(db, control.related_run_id)
        self.assertEqual(run.status, RunStatus.succeeded.value)
        self.assertIsNone(run.error_code)

        step = self.get_step(control.related_run_id, "audit.report")
        self.assertIsNotNone(step)
        self.assertEqual(step.status, StepStatus.succeeded.value)

        audit = self._reload_audit(audit_id)
        self.assertEqual(audit.status, AuditStatus.completed.value)
        self.assertIsNone(audit.active_question_id)
        self.assertIsNotNone(audit.report_json)
        report = json.loads(audit.report_json)
        self.assertEqual(report["generated_by"], "model")
        self.assertTrue(str(report.get("limitations") or "").strip(), "报告必须写局限性")
        self.assertTrue(str(report.get("summary") or "").strip(), "报告必须写摘要")
        self.assertEqual(int(report["product_version"]), int(audit.product_version))

        self.assertEqual(self._session_phase(session_id), SessionPhase.audit_completed.value)
        self.assertEqual(len(self.terminal_events(control.related_run_id)), 1, "终结事件必须恰好一个")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
