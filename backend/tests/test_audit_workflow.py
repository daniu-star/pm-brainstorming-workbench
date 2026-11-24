"""``workflows.audit`` 的三条审计入口测试（真实 sqlite + 假 LLM，不联网）。

覆盖 PRD FR-07 的关键合同：

* 开场 ``run_audit_open``：写入第 1 个问题、``opening -> active``、
  ``question_count=1``、``audit.updated`` 事件、会话阶段 ``auditing``；
  重复执行幂等；模型两次非法输出 → ``failed(MODEL_OUTPUT_INVALID)`` 且
  审计落 ``failed`` / 阶段回 ``brainstorming``（可重试）；
* 回答 ``run_audit_answer``：评估写入问题行并留下修订记录；``supported``
  必须同时有具体理由与有效证据，否则降级 ``needs_clarification``；
  目标 12 问、硬上限 18 问；第 18 问之后禁止第 19 问，直接 ``closing``
  并在同一 Run 内产出报告；评估失败 → ``pending_continuation`` 标记；
* 报告 ``run_audit_report``：只读结构化记录；模型不可用 → 确定性
  「有限总结」并标 ``generated_by="fallback"`` / limitations；``closing``
  完成 → ``completed``；取消请求优先、不写报告；
* 所有入口 ``product_state`` 恒为 ``None``，不推进产品状态版本；
* ``apply`` 里的原子守卫：Run 已取消 / 审计版本已过期时不写入。
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from core.llm_client import (  # noqa: E402
    LLMAuthError,
    LLMOutputInvalidError,
    LLMResult,
    LLMUsage,
)
from domain import contracts as C  # noqa: E402
from domain.contracts import (  # noqa: E402
    AUDIT_DIMENSIONS,
    AUDIT_QUESTION_HARD_LIMIT,
    AUDIT_QUESTION_TARGET,
    Assessment,
    AuditStatus,
    DimensionState,
    EndReason,
    EventType,
    MessageStage,
    RoleCode,
    RunStatus,
    SenderKind,
    SessionPhase,
    new_id,
    utc_now_iso,
)
from domain.errors import AppError, ErrorCode  # noqa: E402
from repositories import audit_repo, database, run_repo, session_repo  # noqa: E402
from repositories.database import (  # noqa: E402
    AuditAssessmentRevisionRow,
    AuditQuestionRow,
    MessageRow,
)
from services import command_service  # noqa: E402
from services.command_service import apply_outcome  # noqa: E402
from workflows import audit as audit_workflow  # noqa: E402

OWNER = "owner@example.com"
WORKER = "test-worker"
PROMPT_TOKENS = 120
COMPLETION_TOKENS = 60


# --------------------------------------------------------------------------- #
# 假 LLM
# --------------------------------------------------------------------------- #


def _llm_result(
    *,
    prompt: int = PROMPT_TOKENS,
    completion: int = COMPLETION_TOKENS,
) -> LLMResult:
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


class FakeLLM:
    """按 ``purpose`` 出队的假 LLM。

    ``script`` 是 ``{purpose: [item, ...]}``；``item`` 可以是返回的 dict，
    也可以是异常实例（直接抛出）。脚本耗尽时抛 ``AssertionError``，
    避免"意外多调用"被静默吞掉。
    """

    def __init__(self, script: dict[str, list[Any]] | None = None) -> None:
        self.script = {key: list(value) for key, value in (script or {}).items()}
        self.calls: list[dict[str, Any]] = []

    async def complete_json(
        self,
        *,
        messages: list[dict[str, Any]],
        max_output_tokens: int | None = None,
        timeout_seconds: float | None = None,
        purpose: str = "",
        json_schema: dict[str, Any] | None = None,
        temperature: float = 0.2,
    ) -> tuple[Any, LLMResult]:
        self.calls.append(
            {
                "purpose": purpose,
                "messages": list(messages),
                "max_output_tokens": max_output_tokens,
                "json_schema": json_schema,
            }
        )
        queue = self.script.get(purpose)
        if not queue:
            raise AssertionError(f"未预期的模型调用：purpose={purpose}")
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item, _llm_result()

    def purposes(self) -> list[str]:
        return [call["purpose"] for call in self.calls]


# --------------------------------------------------------------------------- #
# 测试基类：真实 sqlite + 真实仓储
# --------------------------------------------------------------------------- #


class AuditWorkflowTestCase(unittest.IsolatedAsyncioTestCase):
    """审计工作流与真实仓储/提交路径的集成测试。"""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="audit-test-")
        db_path = Path(self._tmpdir, "t.db").as_posix()
        database.configure(f"sqlite:///{db_path}", force=True)
        database.initialize_database()
        self.db = database.new_session()
        self.session = session_repo.create_session(
            self.db,
            owner_email=OWNER,
            problem_statement="我想验证这个产品想法是否站得住",
        )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        database.dispose_engine()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    # -- 数据准备 ---------------------------------------------------------- #

    def create_audit(self, *, status: AuditStatus = AuditStatus.opening) -> str:
        """建审计并把状态推进到指定阶段（默认 opening，交给开场步骤）。"""

        audit = audit_repo.create_audit(
            self.db,
            session_id=self.session.id,
            product_version=1,
            now_iso=utc_now_iso(),
        )
        if status is not AuditStatus.created:
            audit_repo.update_audit(self.db, audit, now_iso=utc_now_iso(), status=status)
        # 模拟 command_service.start_audit 的会话侧登记（审计 id 只由命令层维护）。
        session_repo.touch_session(
            self.db, self.session, now_iso=utc_now_iso(), current_audit_id=str(audit.id)
        )
        self.db.commit()
        return str(audit.id)

    def add_question(
        self,
        audit_id: str,
        *,
        ordinal: int,
        dimension: str,
        text: str = "这个判断的依据是什么？",
        answer: str | None = None,
        assessment: Assessment | None = None,
        rationale: str = "用户给出的理由",
        evidence_refs: tuple[str, ...] = (),
    ) -> tuple[str, str | None]:
        """加一个问题；``answer`` 非空时同时写入已提交的回答与评估。"""

        audit = audit_repo.require_audit(self.db, audit_id, owner_email=OWNER)
        question = audit_repo.add_question(
            self.db,
            audit,
            ordinal=ordinal,
            dimension=dimension,
            issue_refs=[],
            question_text=text,
            now_iso=utc_now_iso(),
        )
        answer_message_id: str | None = None
        if answer is not None:
            message = session_repo.add_message(
                self.db,
                self.session,
                sender_kind=SenderKind.human,
                stage=MessageStage.audit,
                content=answer,
            )
            audit_repo.answer_question(
                self.db, question, answer_message_id=message.id, now_iso=utc_now_iso()
            )
            answer_message_id = str(message.id)
            if assessment is not None:
                audit_repo.record_assessment(
                    self.db,
                    question,
                    assessment=assessment,
                    rationale=rationale,
                    evidence_refs=list(evidence_refs),
                    now_iso=utc_now_iso(),
                )
        self.db.commit()
        return str(question.id), answer_message_id

    def add_evidence(self, excerpt: str = "访谈记录：用户确实每周手动整理素材") -> str:
        row = session_repo.add_evidence(
            self.db,
            self.session,
            kind=C.EvidenceKind.user_research,
            title="用户访谈",
            excerpt=excerpt,
            verification_status=C.VerificationStatus.source_checked,
        )
        self.db.commit()
        return str(row.id)

    def create_run(
        self,
        *,
        action: str,
        audit_id: str,
        question_id: str | None = None,
        base_audit_version: int | None = None,
    ) -> tuple[str, int]:
        """建 Run 并由测试 Worker 领取（拿到 lease_epoch）。"""

        audit = audit_repo.require_audit(self.db, audit_id, owner_email=OWNER)
        now = int(time.time())
        run = run_repo.create_run(
            self.db,
            session_id=self.session.id,
            owner_email=OWNER,
            request_id=new_id(),
            action=action,
            input_hash="input-hash",
            base_session_version=1,
            base_product_version=1,
            budget={},
            now_iso=utc_now_iso(),
            deadline_at=now + 300,
            queue_deadline_at=now + 600,
            audit_id=audit_id,
            base_audit_version=(
                int(audit.audit_version) if base_audit_version is None else base_audit_version
            ),
            question_id=question_id,
            run_id=new_id(),
        )
        self.db.commit()
        claimed = run_repo.claim_next_run(
            self.db, worker_id=WORKER, now_epoch=int(time.time()), lease_seconds=30
        )
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.id, run.id)
        self.db.commit()
        return str(run.id), int(claimed.lease_epoch)

    def make_ctx(
        self,
        run_id: str,
        *,
        llm: Any,
        current_message_id: str | None = None,
        task_kind: Any = C.TaskKind.audit_answer,
    ) -> SimpleNamespace:
        run = run_repo.get_run(self.db, run_id)
        self.assertIsNotNone(run)
        session = session_repo.get_session(self.db, self.session.id)
        self.assertIsNotNone(session)
        state = session_repo.get_product_state(
            self.db, self.session.id, version=int(run.base_product_version)
        )
        current = None
        if current_message_id is not None:
            current = session_repo.get_message(self.db, self.session.id, current_message_id)
        return SimpleNamespace(
            db=self.db,
            run=run,
            session=session,
            product_state=state,
            current_message=current,
            task_kind=task_kind,
            llm=llm,
            budget=SimpleNamespace(max_context_input_tokens=C.MAX_CONTEXT_INPUT_TOKENS),
            worker_id=WORKER,
            lease_epoch=int(run.lease_epoch),
            deadline_epoch=time.time() + 60,
        )

    # -- 提交与查询 -------------------------------------------------------- #

    def commit_outcome(self, run_id: str, lease_epoch: int, outcome: Any) -> None:
        apply_outcome(
            self.db,
            run_id=run_id,
            worker_id=WORKER,
            lease_epoch=lease_epoch,
            outcome=outcome,
        )
        self.db.commit()

    def reload_audit(self, audit_id: str) -> Any:
        self.db.expire_all()
        audit = audit_repo.get_audit(self.db, audit_id)
        self.assertIsNotNone(audit)
        return audit

    def questions(self, audit_id: str) -> list[AuditQuestionRow]:
        self.db.expire_all()
        return audit_repo.questions_for_audit(self.db, audit_id)

    def question(self, audit_id: str, question_id: str) -> AuditQuestionRow:
        self.db.expire_all()
        row = audit_repo.get_question(self.db, audit_id, question_id)
        self.assertIsNotNone(row)
        return row

    def report_revisions(self, audit_id: str) -> list[Any]:
        self.db.expire_all()
        return audit_repo.list_report_revisions(self.db, audit_id)

    def assessment_revisions(self, audit_id: str) -> list[AuditAssessmentRevisionRow]:
        self.db.expire_all()
        rows = self.db.execute(
            select(AuditAssessmentRevisionRow).where(
                AuditAssessmentRevisionRow.audit_id == audit_id
            )
        ).scalars()
        return list(rows)

    def messages(self) -> list[MessageRow]:
        self.db.expire_all()
        rows = self.db.execute(
            select(MessageRow).where(MessageRow.session_id == self.session.id)
        ).scalars()
        return list(rows)

    def session_row(self) -> Any:
        self.db.expire_all()
        row = session_repo.get_session(self.db, self.session.id)
        self.assertIsNotNone(row)
        return row

    def run_row(self, run_id: str) -> Any:
        self.db.expire_all()
        row = run_repo.get_run(self.db, run_id)
        self.assertIsNotNone(row)
        return row

    def events(self, run_id: str) -> list[Any]:
        self.db.expire_all()
        return list(run_repo.events_after(self.db, run_id, after_seq=0, limit=200))

    def audit_events(self, run_id: str) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for row in self.events(run_id):
            if str(row.type) == EventType.audit_updated.value:
                import json

                payloads.append(json.loads(row.payload_json))
        return payloads

    def assert_no_product_state_change(self, outcome: Any) -> None:
        self.assertIsNone(outcome.product_state, "审计入口不得返回产品状态")
        self.assertEqual(self.session_row().current_product_version, 1)


# --------------------------------------------------------------------------- #
# 纯函数：维度计划 / 收尾判定 / 评估收敛 / 有限总结
# --------------------------------------------------------------------------- #


def _states(**overrides: Assessment) -> list[DimensionState]:
    return [
        DimensionState(key=key, assessment=overrides.get(key, Assessment.not_discussed))
        for key in AUDIT_DIMENSIONS
    ]


class AuditPureFunctionsTests(unittest.TestCase):
    """确定性规则函数，不依赖数据库。"""

    def test_plan_next_dimension_prefers_uncovered_then_gaps(self) -> None:
        self.assertEqual(audit_workflow.plan_next_dimension([]), AUDIT_DIMENSIONS[0])
        states = _states(**{AUDIT_DIMENSIONS[0]: Assessment.supported})
        self.assertEqual(audit_workflow.plan_next_dimension(states), AUDIT_DIMENSIONS[1])

        covered = [
            DimensionState(key=AUDIT_DIMENSIONS[0], assessment=Assessment.supported),
            DimensionState(key=AUDIT_DIMENSIONS[1], assessment=Assessment.gap),
            DimensionState(key=AUDIT_DIMENSIONS[2], assessment=Assessment.supported),
            DimensionState(key=AUDIT_DIMENSIONS[3], assessment=Assessment.not_applicable),
            DimensionState(key=AUDIT_DIMENSIONS[4], assessment=Assessment.supported),
            DimensionState(key=AUDIT_DIMENSIONS[5], assessment=Assessment.supported),
        ]
        self.assertEqual(audit_workflow.plan_next_dimension(covered), AUDIT_DIMENSIONS[1])

        all_settled = [
            DimensionState(
                key=key,
                assessment=Assessment.supported
                if index % 2 == 0
                else Assessment.not_applicable,
            )
            for index, key in enumerate(AUDIT_DIMENSIONS)
        ]
        self.assertIsNone(audit_workflow.plan_next_dimension(all_settled))

    def test_should_close_hard_limit_target_and_coverage(self) -> None:
        uncovered = _states()
        self.assertTrue(
            audit_workflow.should_close(
                ordinal=AUDIT_QUESTION_HARD_LIMIT,
                question_count=AUDIT_QUESTION_HARD_LIMIT,
                states=uncovered,
            ),
            "第 18 问的回答被接收后必须收尾",
        )
        self.assertFalse(
            audit_workflow.should_close(ordinal=3, question_count=3, states=uncovered)
        )
        self.assertTrue(
            audit_workflow.should_close(
                ordinal=AUDIT_QUESTION_TARGET,
                question_count=AUDIT_QUESTION_TARGET,
                states=uncovered,
            ),
            "达到目标问题数即收尾",
        )
        all_settled = [
            DimensionState(key=key, assessment=Assessment.supported)
            for key in AUDIT_DIMENSIONS
        ]
        self.assertTrue(
            audit_workflow.should_close(ordinal=6, question_count=6, states=all_settled),
            "六个维度都有结论时提前收尾",
        )

    def test_build_dimension_states_is_ordered_and_prefers_strongest(self) -> None:
        class Row:
            def __init__(self, *, id: str, ordinal: int, dimension: str, assessment: Any,
                         answer_message_id: str | None = None, rationale: str = "",
                         evidence_refs_json: str = "[]"):
                self.id = id
                self.ordinal = ordinal
                self.dimension = dimension
                self.assessment = assessment
                self.answer_message_id = answer_message_id
                self.rationale = rationale
                self.evidence_refs_json = evidence_refs_json

        rows = [
            Row(id="q2", ordinal=2, dimension=AUDIT_DIMENSIONS[0],
                assessment="needs_clarification", answer_message_id="m2"),
            Row(id="q1", ordinal=1, dimension=AUDIT_DIMENSIONS[0],
                assessment="supported", answer_message_id="m1", rationale="有证据"),
            Row(id="q3", ordinal=3, dimension=AUDIT_DIMENSIONS[2], assessment="gap"),
        ]
        states = audit_workflow.build_dimension_states(rows)
        self.assertEqual([state.key for state in states], list(AUDIT_DIMENSIONS))
        first = states[0]
        self.assertEqual(first.assessment, Assessment.supported)
        self.assertEqual(first.asked_question_ids, ["q1", "q2"])
        self.assertEqual(first.answered_question_ids, ["q1", "q2"])
        self.assertEqual(first.rationale, "有证据")
        self.assertEqual(states[2].assessment, Assessment.gap)
        self.assertEqual(states[1].assessment, Assessment.not_discussed)

    def test_normalize_assessment_downgrades_supported_without_evidence(self) -> None:
        warnings: list[str] = []
        assessment, rationale, refs = audit_workflow.normalize_assessment(
            Assessment.supported,
            rationale="我认为可行",
            evidence_refs=["ghost-evidence"],
            allowed_evidence=[],
            warnings=warnings,
        )
        self.assertIs(assessment, Assessment.needs_clarification)
        self.assertEqual(refs, [])
        self.assertTrue(warnings, "越权引用与降级都应留下 warning")

    def test_normalize_assessment_keeps_supported_with_valid_evidence(self) -> None:
        warnings: list[str] = []
        assessment, rationale, refs = audit_workflow.normalize_assessment(
            Assessment.supported,
            rationale="访谈记录支持这个判断",
            evidence_refs=["ev-1", "ghost"],
            allowed_evidence=["ev-1"],
            warnings=warnings,
        )
        self.assertIs(assessment, Assessment.supported)
        self.assertEqual(refs, ["ev-1"])
        self.assertEqual(rationale, "访谈记录支持这个判断")

    def test_normalize_assessment_rejects_unanswered_values(self) -> None:
        warnings: list[str] = []
        assessment, _rationale, _refs = audit_workflow.normalize_assessment(
            Assessment.awaiting_answer,
            rationale="",
            evidence_refs=[],
            allowed_evidence=[],
            warnings=warnings,
        )
        self.assertIs(assessment, Assessment.needs_clarification)

    def test_build_fallback_report_marks_limitations(self) -> None:
        states = [
            DimensionState(
                key=AUDIT_DIMENSIONS[0],
                assessment=Assessment.gap,
                rationale="缺少真实用户样本",
                asked_question_ids=["q1"],
                answered_question_ids=["q1"],
            ),
            DimensionState(
                key=AUDIT_DIMENSIONS[1],
                assessment=Assessment.supported,
                rationale="技术路线清晰",
                asked_question_ids=["q2"],
                answered_question_ids=["q2"],
            ),
        ]
        audit = SimpleNamespace(
            id="audit-1",
            product_version=3,
            audit_version=4,
            question_count=2,
            end_reason=EndReason.completed,
        )
        report = audit_workflow.build_fallback_report(audit=audit, states=states)
        self.assertEqual(report.audit_id, "audit-1")
        self.assertEqual(report.product_version, 3)
        self.assertEqual(report.end_reason, EndReason.completed)
        self.assertEqual(report.generated_by, "fallback")
        self.assertTrue(report.limitations)
        self.assertTrue(any("缺少真实用户样本" in point for point in report.unresolved_gaps))
        self.assertTrue(any("技术路线清晰" in point for point in report.supported_points))
        self.assertEqual(len(report.dimension_states), 2)

        uncovered = audit_workflow.build_fallback_report(
            audit=audit, states=_states(**{AUDIT_DIMENSIONS[0]: Assessment.supported})
        )
        self.assertTrue(
            any("未覆盖" in point for point in uncovered.unresolved_gaps),
            "未讨论的维度必须明确标出",
        )

    def test_render_report_message_contains_counts(self) -> None:
        audit = SimpleNamespace(
            id="audit-1",
            product_version=1,
            audit_version=2,
            question_count=5,
            end_reason=EndReason.completed,
        )
        report = audit_workflow.build_fallback_report(audit=audit, states=[])
        text = audit_workflow.render_report_message(report)
        self.assertIn("审计已完成", text)
        self.assertIn("5", text)
        self.assertLessEqual(len(text), C.MAX_DISPLAY_TEXT_CHARS)


# --------------------------------------------------------------------------- #
# 入口：开场
# --------------------------------------------------------------------------- #


class AuditOpenTests(AuditWorkflowTestCase):
    async def test_open_writes_first_question_and_activates_audit(self) -> None:
        audit_id = self.create_audit(status=AuditStatus.opening)
        run_id, lease_epoch = self.create_run(action="start_audit", audit_id=audit_id)
        llm = FakeLLM(
            {
                audit_workflow.PURPOSE_OPEN: [
                    {
                        "dimension": AUDIT_DIMENSIONS[0],
                        "question_text": "谁最需要这个工具？为什么？",
                        "issue_refs": [],
                        "evidence_refs": [],
                        "rationale": "先锁定问题真实性",
                    }
                ]
            }
        )
        ctx = self.make_ctx(run_id, llm=llm, task_kind=C.TaskKind.audit_open)

        outcome = await audit_workflow.run_audit_open(ctx)
        self.assertEqual(outcome.status, RunStatus.succeeded)
        self.assertEqual(outcome.session_phase, SessionPhase.auditing)
        self.assertIsNone(outcome.brief_status)
        self.assertEqual(len(outcome.messages), 1)
        self.assertTrue(
            outcome.messages[0].content.startswith("我们开始一次结构化的产品评审"),
            "开场消息需要确定性说明前缀",
        )
        self.assertIn("谁最需要这个工具？为什么？", outcome.messages[0].content)
        self.assert_no_product_state_change(outcome)

        self.commit_outcome(run_id, lease_epoch, outcome)

        audit = self.reload_audit(audit_id)
        self.assertEqual(audit.status, AuditStatus.active.value)
        self.assertEqual(int(audit.question_count), 1)
        self.assertEqual(int(audit.audit_version), 2)
        self.assertIsNotNone(audit.active_question_id)

        rows = self.questions(audit_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(int(rows[0].ordinal), 1)
        self.assertEqual(rows[0].dimension, AUDIT_DIMENSIONS[0])
        self.assertEqual(rows[0].question_text, "谁最需要这个工具？为什么？")
        self.assertEqual(rows[0].question_message_id, outcome.messages[0].message_id)
        self.assertEqual(rows[0].assessment, Assessment.awaiting_answer.value)

        message = [row for row in self.messages() if row.id == rows[0].question_message_id]
        self.assertEqual(len(message), 1)
        self.assertEqual(message[0].stage, MessageStage.audit.value)
        self.assertEqual(message[0].role_code, RoleCode.interviewer.value)

        events = self.audit_events(run_id)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["status"], AuditStatus.active.value)
        self.assertEqual(events[0]["question_count"], 1)
        self.assertEqual(events[0]["dimension"], AUDIT_DIMENSIONS[0])

        session = self.session_row()
        self.assertEqual(session.phase, SessionPhase.auditing.value)
        self.assertEqual(session.current_audit_id, audit_id)
        self.assertEqual(self.run_row(run_id).status, RunStatus.succeeded.value)

    async def test_open_is_idempotent_when_question_already_exists(self) -> None:
        audit_id = self.create_audit(status=AuditStatus.active)
        self.add_question(audit_id, ordinal=1, dimension=AUDIT_DIMENSIONS[0])
        run_id, lease_epoch = self.create_run(action="start_audit", audit_id=audit_id)
        llm = FakeLLM()
        ctx = self.make_ctx(run_id, llm=llm, task_kind=C.TaskKind.audit_open)

        outcome = await audit_workflow.run_audit_open(ctx)
        self.assertEqual(outcome.status, RunStatus.succeeded)
        self.assertEqual(llm.calls, [], "已有问题时不调用模型")
        self.assertTrue(any("不再重复执行" in warning for warning in outcome.warnings))

        before = len(self.messages())
        self.commit_outcome(run_id, lease_epoch, outcome)
        self.assertEqual(len(self.messages()), before)
        audit = self.reload_audit(audit_id)
        self.assertEqual(int(audit.question_count), 1)
        self.assertEqual(len(self.questions(audit_id)), 1)

    async def test_open_failure_marks_audit_failed_and_phase_back_to_brainstorming(
        self,
    ) -> None:
        audit_id = self.create_audit(status=AuditStatus.opening)
        run_id, lease_epoch = self.create_run(action="start_audit", audit_id=audit_id)
        llm = FakeLLM(
            {
                audit_workflow.PURPOSE_OPEN: [
                    LLMOutputInvalidError("bad json"),
                    LLMOutputInvalidError("bad json again"),
                ]
            }
        )
        ctx = self.make_ctx(run_id, llm=llm, task_kind=C.TaskKind.audit_open)

        outcome = await audit_workflow.run_audit_open(ctx)
        self.assertEqual(outcome.status, RunStatus.failed)
        self.assertEqual(outcome.error_code, ErrorCode.MODEL_OUTPUT_INVALID)
        self.assertEqual(len(llm.calls), 2, "非法输出只修复一次")
        self.assertIsNone(outcome.product_state)

        self.commit_outcome(run_id, lease_epoch, outcome)

        audit = self.reload_audit(audit_id)
        self.assertEqual(audit.status, AuditStatus.failed.value)
        self.assertEqual(audit.end_reason, EndReason.failed.value)
        self.assertEqual(int(audit.question_count), 0)
        self.assertEqual(self.questions(audit_id), [])
        self.assertEqual(self.session_row().phase, SessionPhase.brainstorming.value)
        self.assertEqual(self.run_row(run_id).status, RunStatus.failed.value)

    async def test_open_replays_after_transport_failure(self) -> None:
        """开场传输失败后重放同一审计：failed → opening → active。

        这是「重试开场」恢复路径的后端契约（P2-2）：失败审计不再返回
        「开场步骤不再重复执行」的 succeeded 空转结果，而是真的重建第 1 问。
        """

        audit_id = self.create_audit(status=AuditStatus.opening)
        run_id, lease_epoch = self.create_run(action="start_audit", audit_id=audit_id)
        llm = FakeLLM(
            {audit_workflow.PURPOSE_OPEN: [LLMAuthError("上游 401：API Key 无效")]}
        )
        ctx = self.make_ctx(run_id, llm=llm, task_kind=C.TaskKind.audit_open)

        failed = await audit_workflow.run_audit_open(ctx)
        self.assertEqual(failed.status, RunStatus.failed)
        self.commit_outcome(run_id, lease_epoch, failed)
        self.assertEqual(self.reload_audit(audit_id).status, AuditStatus.failed.value)

        # 真实恢复入口：「重试该步骤」= POST /runs/{id}/retry → retry_run 建子 Run，
        # 子 Run 复制父 Run 的 base_audit_version。失败 apply 不推进 audit_version，
        # 重试才不会被 RETRY_AUDIT_CONTEXT_CHANGED 拒绝。
        self.assertEqual(int(self.reload_audit(audit_id).audit_version), 1)
        receipt = command_service.retry_run(
            self.db,
            owner_email=OWNER,
            run_id=run_id,
            request_id=new_id(),
        )
        self.db.commit()
        self.assertNotEqual(receipt.run_id, run_id)
        claimed = run_repo.claim_next_run(
            self.db, worker_id=WORKER, now_epoch=int(time.time()), lease_seconds=30
        )
        self.assertIsNotNone(claimed)
        self.db.commit()
        replay_run, replay_lease = str(claimed.id), int(claimed.lease_epoch)
        self.assertEqual(replay_run, receipt.run_id)
        replay_llm = FakeLLM(
            {
                audit_workflow.PURPOSE_OPEN: [
                    {
                        "dimension": AUDIT_DIMENSIONS[0],
                        "question_text": "谁最需要这个工具？为什么？",
                        "issue_refs": [],
                        "evidence_refs": [],
                        "rationale": "先锁定问题真实性",
                    }
                ]
            }
        )
        replay_ctx = self.make_ctx(
            replay_run, llm=replay_llm, task_kind=C.TaskKind.audit_open
        )
        outcome = await audit_workflow.run_audit_open(replay_ctx)

        self.assertEqual(outcome.status, RunStatus.succeeded)
        self.assertEqual(len(replay_llm.calls), 1, "重放必须真的调用模型重建开场")
        self.assertFalse(
            any("不再重复执行" in warning for warning in outcome.warnings),
            "重放不应退化成 succeeded 警告",
        )
        self.commit_outcome(replay_run, replay_lease, outcome)

        audit = self.reload_audit(audit_id)
        self.assertEqual(audit.status, AuditStatus.active.value)
        self.assertEqual(int(audit.question_count), 1)
        self.assertIsNotNone(audit.active_question_id)
        rows = self.questions(audit_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(int(rows[0].ordinal), 1)
        self.assertEqual(self.session_row().phase, SessionPhase.auditing.value)
        self.assertEqual(self.run_row(replay_run).status, RunStatus.succeeded.value)

    async def test_open_uses_planned_dimension_when_model_disagrees(self) -> None:
        audit_id = self.create_audit(status=AuditStatus.opening)
        run_id, lease_epoch = self.create_run(action="start_audit", audit_id=audit_id)
        llm = FakeLLM(
            {
                audit_workflow.PURPOSE_OPEN: [
                    {
                        "dimension": AUDIT_DIMENSIONS[3],
                        "question_text": "商业闭环怎么走？",
                        "issue_refs": [],
                        "evidence_refs": [],
                        "rationale": "",
                    }
                ]
            }
        )
        ctx = self.make_ctx(run_id, llm=llm, task_kind=C.TaskKind.audit_open)

        outcome = await audit_workflow.run_audit_open(ctx)
        self.assertTrue(any("按计划维度" in warning for warning in outcome.warnings))
        self.commit_outcome(run_id, lease_epoch, outcome)

        rows = self.questions(audit_id)
        self.assertEqual(rows[0].dimension, AUDIT_DIMENSIONS[0])

    async def test_open_skips_writes_when_cancel_requested(self) -> None:
        audit_id = self.create_audit(status=AuditStatus.opening)
        run_id, lease_epoch = self.create_run(action="start_audit", audit_id=audit_id)
        with database.session_scope() as db:
            command_service.cancel_run(db, owner_email=OWNER, run_id=run_id)
        llm = FakeLLM()
        ctx = self.make_ctx(run_id, llm=llm, task_kind=C.TaskKind.audit_open)

        outcome = await audit_workflow.run_audit_open(ctx)
        self.assertEqual(llm.calls, [])
        self.assertEqual(outcome.status, RunStatus.succeeded)
        self.commit_outcome(run_id, lease_epoch, outcome)

        self.assertEqual(self.questions(audit_id), [])
        self.assertEqual(self.reload_audit(audit_id).status, AuditStatus.opening.value)
        self.assertEqual(self.run_row(run_id).status, RunStatus.cancelled.value)


# --------------------------------------------------------------------------- #
# 入口：回答
# --------------------------------------------------------------------------- #


def _answer_payload(
    *,
    assessment: str = "gap",
    rationale: str = "目前缺少真实用户数据",
    evidence_refs: list[str] | None = None,
    next_dimension: str = AUDIT_DIMENSIONS[1],
    next_question: str | None = "如果只做一个功能，先做哪个？",
) -> dict[str, Any]:
    return {
        "assessment": assessment,
        "rationale": rationale,
        "evidence_refs": evidence_refs or [],
        "next_question": (
            None
            if next_question is None
            else {
                "dimension": next_dimension,
                "question_text": next_question,
                "issue_refs": [],
                "evidence_refs": [],
                "rationale": "",
            }
        ),
        "message": "已记录你的判断。",
    }


def _report_payload(
    *,
    supported: list[str] | None = None,
    gaps: list[str] | None = None,
    summary: str | None = None,
) -> dict[str, Any]:
    return {
        "supported_points": supported or ["技术路线清晰"],
        "unresolved_gaps": gaps or ["缺少真实用户样本"],
        "recommended_experiments": ["用 5 个用户做一次可用性测试"],
        "limitations": "只覆盖了技术与用户两个维度。",
        "summary": summary or "整体判断：方向可行，但用户证据仍需补齐。",
    }


class AuditAnswerTests(AuditWorkflowTestCase):
    async def test_answer_records_assessment_and_next_question(self) -> None:
        audit_id = self.create_audit(status=AuditStatus.active)
        question_id, answer_id = self.add_question(
            audit_id, ordinal=1, dimension=AUDIT_DIMENSIONS[0], answer="给独立设计师用"
        )
        run_id, lease_epoch = self.create_run(
            action="answer_audit", audit_id=audit_id, question_id=question_id
        )
        llm = FakeLLM({audit_workflow.PURPOSE_ANSWER: [_answer_payload()]})
        ctx = self.make_ctx(
            run_id, llm=llm, current_message_id=answer_id, task_kind=C.TaskKind.audit_answer
        )

        outcome = await audit_workflow.run_audit_answer(ctx)
        self.assertEqual(outcome.status, RunStatus.succeeded)
        self.commit_outcome(run_id, lease_epoch, outcome)

        audit = self.reload_audit(audit_id)
        self.assertEqual(audit.status, AuditStatus.active.value)
        self.assertEqual(int(audit.question_count), 2)
        self.assertEqual(int(audit.audit_version), 2)

        answered = self.question(audit_id, question_id)
        self.assertEqual(answered.assessment, Assessment.gap.value)
        self.assertEqual(answered.rationale, "目前缺少真实用户数据")

        rows = self.questions(audit_id)
        self.assertEqual([int(row.ordinal) for row in rows], [1, 2])
        self.assertEqual(rows[1].question_text, "如果只做一个功能，先做哪个？")
        self.assertEqual(audit.active_question_id, rows[1].id)
        self.assertEqual(rows[1].question_message_id, outcome.messages[0].message_id)

        revisions = self.report_revisions(audit_id)
        self.assertEqual(revisions, [], "中途不应产生报告修订")

        assessment_revisions = self.assessment_revisions(audit_id)
        self.assertEqual(len(assessment_revisions), 1, "评估必须留下不可覆盖的修订记录")
        revision = assessment_revisions[0]
        self.assertEqual(revision.question_id, question_id)
        self.assertEqual(revision.origin, "model")
        self.assertEqual(int(revision.audit_version), 2)
        import json

        self.assertEqual(json.loads(revision.before_json)["assessment"], "awaiting_answer")
        self.assertEqual(json.loads(revision.after_json)["assessment"], "gap")

        events = self.audit_events(run_id)
        self.assertEqual(events[-1]["status"], AuditStatus.active.value)
        self.assertEqual(events[-1]["question_count"], 2)
        self.assertEqual(events[-1]["ordinal"], 2)
        self.assertEqual(events[-1]["assessment"], "gap", "事件需带上本次评估结果")
        self.assertEqual(self.session_row().phase, SessionPhase.auditing.value)
        self.assert_no_product_state_change(outcome)

    async def test_answer_supported_stays_supported_with_valid_evidence(self) -> None:
        evidence_id = self.add_evidence()
        audit_id = self.create_audit(status=AuditStatus.active)
        question_id, answer_id = self.add_question(
            audit_id, ordinal=1, dimension=AUDIT_DIMENSIONS[0], answer="用户访谈里已经确认过"
        )
        run_id, lease_epoch = self.create_run(
            action="answer_audit", audit_id=audit_id, question_id=question_id
        )
        llm = FakeLLM(
            {
                audit_workflow.PURPOSE_ANSWER: [
                    _answer_payload(
                        assessment="supported",
                        rationale="访谈证据支持这个判断",
                        evidence_refs=[evidence_id],
                    )
                ]
            }
        )
        ctx = self.make_ctx(
            run_id, llm=llm, current_message_id=answer_id, task_kind=C.TaskKind.audit_answer
        )

        outcome = await audit_workflow.run_audit_answer(ctx)
        self.commit_outcome(run_id, lease_epoch, outcome)

        answered = self.question(audit_id, question_id)
        self.assertEqual(answered.assessment, Assessment.supported.value)
        self.assertEqual(answered.rationale, "访谈证据支持这个判断")
        import json

        self.assertEqual(json.loads(answered.evidence_refs_json), [evidence_id])

    async def test_answer_supported_without_evidence_is_downgraded(self) -> None:
        audit_id = self.create_audit(status=AuditStatus.active)
        question_id, answer_id = self.add_question(
            audit_id, ordinal=1, dimension=AUDIT_DIMENSIONS[0], answer="我觉得挺有需求的"
        )
        run_id, lease_epoch = self.create_run(
            action="answer_audit", audit_id=audit_id, question_id=question_id
        )
        llm = FakeLLM(
            {
                audit_workflow.PURPOSE_ANSWER: [
                    _answer_payload(assessment="supported", rationale="我觉得挺有需求")
                ]
            }
        )
        ctx = self.make_ctx(
            run_id, llm=llm, current_message_id=answer_id, task_kind=C.TaskKind.audit_answer
        )

        outcome = await audit_workflow.run_audit_answer(ctx)
        self.assertTrue(
            any("降级" in warning for warning in outcome.warnings),
            f"应记录降级 warning，实际 {outcome.warnings}",
        )
        self.commit_outcome(run_id, lease_epoch, outcome)

        answered = self.question(audit_id, question_id)
        self.assertEqual(answered.assessment, Assessment.needs_clarification.value)

    async def test_answer_at_hard_limit_closes_and_never_asks_nineteenth(self) -> None:
        audit_id = self.create_audit(status=AuditStatus.active)
        for ordinal in range(1, AUDIT_QUESTION_HARD_LIMIT):
            self.add_question(
                audit_id,
                ordinal=ordinal,
                dimension=AUDIT_DIMENSIONS[(ordinal - 1) % len(AUDIT_DIMENSIONS)],
                answer="已回答",
                assessment=Assessment.gap,
            )
        question_id, answer_id = self.add_question(
            audit_id,
            ordinal=AUDIT_QUESTION_HARD_LIMIT,
            dimension=AUDIT_DIMENSIONS[0],
            answer="最后一问的回答",
        )
        run_id, lease_epoch = self.create_run(
            action="answer_audit", audit_id=audit_id, question_id=question_id
        )
        llm = FakeLLM(
            {
                audit_workflow.PURPOSE_ANSWER: [_answer_payload(next_question=None)],
                audit_workflow.PURPOSE_REPORT: [_report_payload()],
            }
        )
        ctx = self.make_ctx(
            run_id, llm=llm, current_message_id=answer_id, task_kind=C.TaskKind.audit_answer
        )

        outcome = await audit_workflow.run_audit_answer(ctx)
        self.assertEqual(outcome.status, RunStatus.succeeded)
        self.commit_outcome(run_id, lease_epoch, outcome)

        audit = self.reload_audit(audit_id)
        self.assertEqual(audit.status, AuditStatus.completed.value)
        self.assertEqual(int(audit.question_count), AUDIT_QUESTION_HARD_LIMIT)
        self.assertEqual(int(audit.audit_version), 2)
        self.assertIsNone(audit.active_question_id)
        self.assertIn("审计已完成", outcome.messages[0].content)

        rows = self.questions(audit_id)
        self.assertEqual(len(rows), AUDIT_QUESTION_HARD_LIMIT)
        self.assertNotIn(AUDIT_QUESTION_HARD_LIMIT + 1, [int(row.ordinal) for row in rows])

        revisions = self.report_revisions(audit_id)
        self.assertEqual(len(revisions), 1)
        self.assertEqual(revisions[0].generated_by, "model")
        self.assertIsNotNone(audit.report_json)

        events = self.audit_events(run_id)
        self.assertEqual(events[-1]["status"], AuditStatus.completed.value)
        self.assertEqual(self.session_row().phase, SessionPhase.audit_completed.value)
        self.assert_no_product_state_change(outcome)

    async def test_answer_at_question_target_closes_audit(self) -> None:
        audit_id = self.create_audit(status=AuditStatus.active)
        for ordinal in range(1, AUDIT_QUESTION_TARGET):
            self.add_question(
                audit_id,
                ordinal=ordinal,
                dimension=AUDIT_DIMENSIONS[(ordinal - 1) % len(AUDIT_DIMENSIONS)],
                answer="已回答",
                assessment=Assessment.gap,
            )
        question_id, answer_id = self.add_question(
            audit_id,
            ordinal=AUDIT_QUESTION_TARGET,
            dimension=AUDIT_DIMENSIONS[1],
            answer="第 12 问的回答",
        )
        run_id, lease_epoch = self.create_run(
            action="answer_audit", audit_id=audit_id, question_id=question_id
        )
        llm = FakeLLM(
            {
                audit_workflow.PURPOSE_ANSWER: [_answer_payload(next_question=None)],
                audit_workflow.PURPOSE_REPORT: [
                    LLMOutputInvalidError("bad"),
                    LLMOutputInvalidError("bad again"),
                ],
            }
        )
        ctx = self.make_ctx(
            run_id, llm=llm, current_message_id=answer_id, task_kind=C.TaskKind.audit_answer
        )

        outcome = await audit_workflow.run_audit_answer(ctx)
        self.commit_outcome(run_id, lease_epoch, outcome)

        audit = self.reload_audit(audit_id)
        self.assertEqual(audit.status, AuditStatus.completed.value)
        self.assertEqual(int(audit.question_count), AUDIT_QUESTION_TARGET)
        revisions = self.report_revisions(audit_id)
        self.assertEqual(len(revisions), 1)
        self.assertEqual(
            revisions[0].generated_by,
            "fallback",
            "模型两次非法输出时应使用确定性有限总结",
        )
        report = __import__("json").loads(audit.report_json)
        self.assertTrue(report["limitations"])
        self.assertTrue(report["summary"], "兜底报告也必须有摘要（结构化计数）")
        self.assertIn("结构化审计记录", report["summary"])
        self.assertEqual(report["generated_by"], "fallback")

    async def test_answer_failure_marks_pending_continuation(self) -> None:
        audit_id = self.create_audit(status=AuditStatus.active)
        question_id, answer_id = self.add_question(
            audit_id, ordinal=1, dimension=AUDIT_DIMENSIONS[0], answer="一个回答"
        )
        run_id, lease_epoch = self.create_run(
            action="answer_audit", audit_id=audit_id, question_id=question_id
        )
        llm = FakeLLM(
            {
                audit_workflow.PURPOSE_ANSWER: [
                    LLMOutputInvalidError("bad"),
                    LLMOutputInvalidError("bad again"),
                ]
            }
        )
        ctx = self.make_ctx(
            run_id, llm=llm, current_message_id=answer_id, task_kind=C.TaskKind.audit_answer
        )

        outcome = await audit_workflow.run_audit_answer(ctx)
        self.assertEqual(outcome.status, RunStatus.failed)
        self.assertEqual(outcome.error_code, ErrorCode.MODEL_OUTPUT_INVALID)
        self.commit_outcome(run_id, lease_epoch, outcome)

        audit = self.reload_audit(audit_id)
        self.assertEqual(audit.status, AuditStatus.active.value)
        self.assertEqual(int(audit.pending_continuation), 1)
        self.assertIsNone(audit.active_question_id)
        self.assertEqual(int(audit.question_count), 1)
        self.assertEqual(
            self.question(audit_id, question_id).assessment,
            Assessment.awaiting_answer.value,
            "评估失败不得改写已提交回答",
        )
        self.assertEqual(self.session_row().phase, SessionPhase.auditing.value)

    async def test_answer_replays_after_transport_failure_via_retry(self) -> None:
        """回答步骤传输失败后「重试该步骤」：重新评估同一问题（P1-1 恢复路径）。

        失败 apply 只标记 pending_continuation、不推进 audit_version；retry_run
        建子 Run 时继承 question_id，重放才能读到已提交的回答。
        """

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

        failed = await audit_workflow.run_audit_answer(ctx)
        self.assertEqual(failed.status, RunStatus.failed)
        self.assertEqual(failed.error_code, LLMAuthError.code)
        self.commit_outcome(run_id, lease_epoch, failed)

        audit = self.reload_audit(audit_id)
        self.assertEqual(audit.status, AuditStatus.active.value)
        self.assertEqual(int(audit.pending_continuation), 1)
        self.assertEqual(int(audit.audit_version), 1, "失败不得推进 audit_version")

        receipt = command_service.retry_run(
            self.db,
            owner_email=OWNER,
            run_id=run_id,
            request_id=new_id(),
        )
        self.db.commit()
        claimed = run_repo.claim_next_run(
            self.db, worker_id=WORKER, now_epoch=int(time.time()), lease_seconds=30
        )
        self.assertIsNotNone(claimed)
        self.db.commit()
        self.assertEqual(str(claimed.id), receipt.run_id)
        self.assertEqual(
            str(run_repo.get_run(self.db, str(claimed.id)).question_id),
            question_id,
            "重试子 Run 必须继承父 Run 绑定的审计问题",
        )

        replay_llm = FakeLLM({audit_workflow.PURPOSE_ANSWER: [_answer_payload()]})
        replay_ctx = self.make_ctx(
            str(claimed.id),
            llm=replay_llm,
            current_message_id=answer_id,
            task_kind=C.TaskKind.audit_answer,
        )
        outcome = await audit_workflow.run_audit_answer(replay_ctx)
        self.assertEqual(outcome.status, RunStatus.succeeded)
        self.commit_outcome(str(claimed.id), int(claimed.lease_epoch), outcome)

        audit = self.reload_audit(audit_id)
        self.assertEqual(audit.status, AuditStatus.active.value)
        self.assertEqual(int(audit.pending_continuation), 0)
        self.assertEqual(int(audit.audit_version), 2)
        self.assertEqual(self.question(audit_id, question_id).assessment, Assessment.gap.value)
        self.assertEqual(self.session_row().phase, SessionPhase.auditing.value)

    async def test_answer_ignored_when_audit_not_active(self) -> None:
        audit_id = self.create_audit(status=AuditStatus.closing)
        question_id, answer_id = self.add_question(
            audit_id, ordinal=1, dimension=AUDIT_DIMENSIONS[0], answer="一个回答"
        )
        run_id, lease_epoch = self.create_run(
            action="answer_audit", audit_id=audit_id, question_id=question_id
        )
        llm = FakeLLM()
        ctx = self.make_ctx(
            run_id, llm=llm, current_message_id=answer_id, task_kind=C.TaskKind.audit_answer
        )

        outcome = await audit_workflow.run_audit_answer(ctx)
        self.assertEqual(llm.calls, [])
        self.assertEqual(outcome.status, RunStatus.succeeded)
        self.assertTrue(any("不再写入" in warning for warning in outcome.warnings))
        self.commit_outcome(run_id, lease_epoch, outcome)

        self.assertEqual(self.reload_audit(audit_id).status, AuditStatus.closing.value)
        self.assertEqual(len(self.questions(audit_id)), 1)

    async def test_answer_reads_question_id_from_run_row_when_ctx_lacks_it(self) -> None:
        """Run 恢复路径下 ctx.run 可能是过期快照，需回落到 Run 行里的 question_id。"""

        audit_id = self.create_audit(status=AuditStatus.active)
        question_id, answer_id = self.add_question(
            audit_id, ordinal=1, dimension=AUDIT_DIMENSIONS[0], answer="用户是独立设计师"
        )
        run_id, lease_epoch = self.create_run(
            action="answer_audit", audit_id=audit_id, question_id=question_id
        )
        llm = FakeLLM({audit_workflow.PURPOSE_ANSWER: [_answer_payload()]})
        ctx = self.make_ctx(
            run_id, llm=llm, current_message_id=answer_id, task_kind=C.TaskKind.audit_answer
        )
        stale = SimpleNamespace(**{**vars(ctx.run), "question_id": None})
        ctx.run = stale
        self.assertIsNone(getattr(ctx.run, "question_id", None))

        outcome = await audit_workflow.run_audit_answer(ctx)
        self.assertEqual(outcome.status, RunStatus.succeeded)
        self.commit_outcome(run_id, lease_epoch, outcome)

        answered = self.question(audit_id, question_id)
        self.assertEqual(answered.assessment, Assessment.gap.value)
        self.assertEqual(int(self.reload_audit(audit_id).question_count), 2)

    async def test_answer_without_question_id_fails(self) -> None:
        audit_id = self.create_audit(status=AuditStatus.active)
        run_id, lease_epoch = self.create_run(action="answer_audit", audit_id=audit_id)
        llm = FakeLLM()
        ctx = self.make_ctx(run_id, llm=llm, task_kind=C.TaskKind.audit_answer)

        outcome = await audit_workflow.run_audit_answer(ctx)
        self.assertEqual(outcome.status, RunStatus.failed)
        self.assertEqual(outcome.error_code, ErrorCode.INVALID_REQUEST)
        self.commit_outcome(run_id, lease_epoch, outcome)
        self.assertEqual(self.reload_audit(audit_id).status, AuditStatus.active.value)

    async def test_answer_rejects_unknown_question(self) -> None:
        audit_id = self.create_audit(status=AuditStatus.active)
        run_id, lease_epoch = self.create_run(
            action="answer_audit", audit_id=audit_id, question_id="missing-question"
        )
        llm = FakeLLM()
        ctx = self.make_ctx(run_id, llm=llm, task_kind=C.TaskKind.audit_answer)

        outcome = await audit_workflow.run_audit_answer(ctx)
        self.assertEqual(outcome.status, RunStatus.failed)
        self.assertEqual(outcome.error_code, ErrorCode.NOT_FOUND)
        self.commit_outcome(run_id, lease_epoch, outcome)
        self.assertEqual(self.run_row(run_id).status, RunStatus.failed.value)


# --------------------------------------------------------------------------- #
# 入口：报告
# --------------------------------------------------------------------------- #


class AuditReportTests(AuditWorkflowTestCase):
    def seed_closing_audit(self) -> tuple[str, str]:
        """建一个处于 closing、且有结构化评估记录的审计。"""

        audit_id = self.create_audit(status=AuditStatus.active)
        self.add_question(
            audit_id,
            ordinal=1,
            dimension=AUDIT_DIMENSIONS[0],
            answer="有真实用户访谈",
            assessment=Assessment.supported,
            rationale="访谈证据支持",
        )
        self.add_question(
            audit_id,
            ordinal=2,
            dimension=AUDIT_DIMENSIONS[1],
            answer="还没验证",
            assessment=Assessment.gap,
            rationale="缺少可用性测试",
        )
        audit = audit_repo.require_audit(self.db, audit_id, owner_email=OWNER)
        audit_repo.update_audit(
            self.db,
            audit,
            now_iso=utc_now_iso(),
            status=AuditStatus.closing,
            end_reason=EndReason.completed,
            close_requested=1,
        )
        self.db.commit()
        return audit_id, "closing"

    async def test_report_generates_from_structured_records(self) -> None:
        audit_id, _ = self.seed_closing_audit()
        run_id, lease_epoch = self.create_run(action="end_audit", audit_id=audit_id)
        llm = FakeLLM({audit_workflow.PURPOSE_REPORT: [_report_payload()]})
        ctx = self.make_ctx(run_id, llm=llm, task_kind=C.TaskKind.audit_report)

        outcome = await audit_workflow.run_audit_report(ctx)
        self.assertEqual(outcome.status, RunStatus.succeeded)
        self.assertEqual(outcome.session_phase, SessionPhase.audit_completed)
        self.assert_no_product_state_change(outcome)
        self.commit_outcome(run_id, lease_epoch, outcome)

        audit = self.reload_audit(audit_id)
        self.assertEqual(audit.status, AuditStatus.completed.value)
        self.assertEqual(int(audit.audit_version), 2)
        self.assertIsNone(audit.active_question_id)
        report = __import__("json").loads(audit.report_json)
        self.assertEqual(report["generated_by"], "model")
        self.assertEqual(report["question_count"], 2)
        self.assertEqual(int(report["product_version"]), 1)
        self.assertIn("技术路线清晰", report["supported_points"])
        self.assertEqual(
            report["summary"],
            "整体判断：方向可行，但用户证据仍需补齐。",
            "模型给的摘要必须原样落库（第 18 批：之前被丢弃）",
        )

        revisions = self.report_revisions(audit_id)
        self.assertEqual(len(revisions), 1)
        self.assertEqual(revisions[0].generated_by, "model")
        self.assertEqual(self.session_row().phase, SessionPhase.audit_completed.value)

    async def test_report_marks_numbers_absent_from_sources(self) -> None:
        """报告正文里的数字也要能回到来源（第 28 批）：12/6 可追溯，2/66 标注未验证。"""

        audit_id = self.create_audit(status=AuditStatus.active)
        self.add_question(
            audit_id,
            ordinal=1,
            dimension=AUDIT_DIMENSIONS[0],
            answer="访谈了 12 家客户，他们原来的对账耗时是每周 6 小时",
            assessment=Assessment.supported,
            rationale="访谈证据支持",
        )
        audit = audit_repo.require_audit(self.db, audit_id, owner_email=OWNER)
        audit_repo.update_audit(
            self.db,
            audit,
            now_iso=utc_now_iso(),
            status=AuditStatus.closing,
            end_reason=EndReason.completed,
            close_requested=1,
        )
        self.db.commit()

        run_id, lease_epoch = self.create_run(action="end_audit", audit_id=audit_id)
        llm = FakeLLM(
            {
                audit_workflow.PURPOSE_REPORT: [
                    _report_payload(
                        supported=["对账时间从每周 6 小时降到 2 小时，节省 66%"],
                        summary="结论：12 家客户的访谈支持方向可行，2 小时目标需再验证。",
                    )
                ]
            }
        )
        ctx = self.make_ctx(run_id, llm=llm, task_kind=C.TaskKind.audit_report)
        outcome = await audit_workflow.run_audit_report(ctx)
        self.assertEqual(outcome.status, RunStatus.succeeded)
        self.commit_outcome(run_id, lease_epoch, outcome)

        audit = self.reload_audit(audit_id)
        report = __import__("json").loads(audit.report_json)
        self.assertEqual(report["generated_by"], "model")
        unverified = report["unverified_numbers"]
        self.assertEqual(unverified[:2], ["2", "66"], "只出现在模型文字里的数字必须标注")
        self.assertIn("5", unverified, "建议实验里的「5 个用户」同样是模型给的数字，也要标")
        self.assertNotIn("12", unverified, "12 出现在用户回答里（可追溯），不能误标")
        self.assertNotIn("6", unverified, "6 出现在用户回答里（可追溯），不能误标")
        self.assertTrue(report["verification_suggestion"])
        self.assertIn("未验证数字", report["verification_suggestion"])
        self.assertTrue(
            any("未验证数字" in warning for warning in outcome.warnings),
            "标注同时要进 warnings，便于前端提示与日志排查",
        )

    async def test_report_falls_back_when_model_unavailable(self) -> None:
        audit_id, _ = self.seed_closing_audit()
        run_id, lease_epoch = self.create_run(action="end_audit", audit_id=audit_id)
        llm = FakeLLM(
            {
                audit_workflow.PURPOSE_REPORT: [
                    LLMOutputInvalidError("bad"),
                    LLMOutputInvalidError("bad again"),
                ]
            }
        )
        ctx = self.make_ctx(run_id, llm=llm, task_kind=C.TaskKind.audit_report)

        outcome = await audit_workflow.run_audit_report(ctx)
        self.commit_outcome(run_id, lease_epoch, outcome)

        audit = self.reload_audit(audit_id)
        self.assertEqual(audit.status, AuditStatus.completed.value)
        report = __import__("json").loads(audit.report_json)
        self.assertEqual(report["generated_by"], "fallback")
        self.assertTrue(report["limitations"])
        self.assertTrue(report["dimension_states"])
        revisions = self.report_revisions(audit_id)
        self.assertEqual(revisions[0].generated_by, "fallback")
        self.assertTrue(any("有限总结" in warning for warning in outcome.warnings))

    async def test_report_cancel_takes_priority_over_generation(self) -> None:
        audit_id, _ = self.seed_closing_audit()
        run_id, lease_epoch = self.create_run(action="end_audit", audit_id=audit_id)
        with database.session_scope() as db:
            command_service.cancel_run(db, owner_email=OWNER, run_id=run_id)
        llm = FakeLLM({audit_workflow.PURPOSE_REPORT: [_report_payload()]})
        ctx = self.make_ctx(run_id, llm=llm, task_kind=C.TaskKind.audit_report)

        outcome = await audit_workflow.run_audit_report(ctx)
        self.assertEqual(outcome.status, RunStatus.succeeded)
        self.assertTrue(any("取消" in warning for warning in outcome.warnings))
        self.commit_outcome(run_id, lease_epoch, outcome)

        self.assertEqual(llm.calls, [], "取消时不调用模型")
        audit = self.reload_audit(audit_id)
        self.assertEqual(audit.status, AuditStatus.closing.value)
        self.assertEqual(self.report_revisions(audit_id), [])
        self.assertEqual(self.run_row(run_id).status, RunStatus.cancelled.value)

    async def test_report_is_idempotent_when_already_completed(self) -> None:
        audit_id = self.create_audit(status=AuditStatus.completed)
        self.add_question(
            audit_id,
            ordinal=1,
            dimension=AUDIT_DIMENSIONS[0],
            answer="已回答",
            assessment=Assessment.gap,
        )
        run_id, lease_epoch = self.create_run(action="end_audit", audit_id=audit_id)
        llm = FakeLLM()
        ctx = self.make_ctx(run_id, llm=llm, task_kind=C.TaskKind.audit_report)

        outcome = await audit_workflow.run_audit_report(ctx)
        self.assertEqual(outcome.status, RunStatus.succeeded)
        self.assertTrue(any("不再重写" in warning for warning in outcome.warnings))
        self.commit_outcome(run_id, lease_epoch, outcome)

        self.assertEqual(llm.calls, [])
        self.assertEqual(self.report_revisions(audit_id), [])
        self.assertEqual(self.reload_audit(audit_id).status, AuditStatus.completed.value)

    async def test_report_recovers_from_failed_audit(self) -> None:
        audit_id = self.create_audit(status=AuditStatus.failed)
        self.add_question(
            audit_id,
            ordinal=1,
            dimension=AUDIT_DIMENSIONS[0],
            answer="已回答",
            assessment=Assessment.gap,
        )
        audit = audit_repo.require_audit(self.db, audit_id, owner_email=OWNER)
        audit_repo.update_audit(
            self.db, audit, now_iso=utc_now_iso(), end_reason=EndReason.failed
        )
        self.db.commit()
        run_id, lease_epoch = self.create_run(action="end_audit", audit_id=audit_id)
        llm = FakeLLM({audit_workflow.PURPOSE_REPORT: [_report_payload()]})
        ctx = self.make_ctx(run_id, llm=llm, task_kind=C.TaskKind.audit_report)

        outcome = await audit_workflow.run_audit_report(ctx)
        self.commit_outcome(run_id, lease_epoch, outcome)

        audit = self.reload_audit(audit_id)
        self.assertEqual(audit.status, AuditStatus.completed.value)
        self.assertEqual(audit.end_reason, EndReason.failed.value)

    async def test_report_failure_marks_closing_failure_and_recovers(self) -> None:
        """closing 阶段报告失败：落 failed + 错误码、保留草稿，再 start_audit 可恢复。"""

        audit_id, _ = self.seed_closing_audit()
        run_id, lease_epoch = self.create_run(action="end_audit", audit_id=audit_id)
        llm = FakeLLM({audit_workflow.PURPOSE_REPORT: [_report_payload()]})
        ctx = self.make_ctx(run_id, llm=llm, task_kind=C.TaskKind.audit_report)

        async def _boom(*_args: Any, **_kwargs: Any) -> Any:
            raise AppError(ErrorCode.MODEL_OUTPUT_INVALID, "报告生成失败")

        with mock.patch.object(audit_workflow, "_generate_report", new=_boom):
            outcome = await audit_workflow.run_audit_report(ctx)

        self.assertEqual(outcome.status, RunStatus.failed)
        self.assertEqual(outcome.error_code, ErrorCode.MODEL_OUTPUT_INVALID)
        self.assertEqual(outcome.session_phase, SessionPhase.auditing)
        self.assert_no_product_state_change(outcome)
        self.commit_outcome(run_id, lease_epoch, outcome)

        audit = self.reload_audit(audit_id)
        self.assertEqual(audit.status, AuditStatus.failed.value, "closing 失败应落 failed")
        self.assertEqual(audit.end_reason, ErrorCode.MODEL_OUTPUT_INVALID)
        self.assertEqual(int(audit.audit_version), 1, "失败不得 bump audit_version")
        self.assertEqual(
            self.report_revisions(audit_id), [], "失败不得写报告修订"
        )
        self.assertEqual(self.session_row().phase, SessionPhase.auditing.value)

        # 再次 start_audit：应返回「待完成报告」回执，而不是新建审计、重置开场。
        session_id = self.session_row().id
        with database.session_scope() as db:
            session = session_repo.get_session(db, session_id)
            self.assertIsNotNone(session)
            command = C.CommandRequest(
                request_id=new_id(),
                action=C.Action.start_audit,
                payload={},
                expected_session_version=int(session.session_version),
            )
            recovery = command_service.submit_command(
                db,
                owner_email=OWNER,
                session_id=session_id,
                command=command,
            )
        self.assertEqual(recovery.audit_id, audit_id)
        self.assertIs(recovery.reused, True)
        self.assertIs(recovery.status, AuditStatus.failed)
        self.assertEqual(recovery.latest_run_id, run_id)
        self.assertEqual(
            self.reload_audit(audit_id).status, AuditStatus.failed.value, "不得重置开场"
        )

    async def test_stale_audit_version_skips_writes(self) -> None:
        audit_id, _ = self.seed_closing_audit()
        run_id, lease_epoch = self.create_run(
            action="end_audit", audit_id=audit_id, base_audit_version=99
        )
        llm = FakeLLM({audit_workflow.PURPOSE_REPORT: [_report_payload()]})
        ctx = self.make_ctx(run_id, llm=llm, task_kind=C.TaskKind.audit_report)

        outcome = await audit_workflow.run_audit_report(ctx)
        self.assertEqual(outcome.status, RunStatus.succeeded)
        self.commit_outcome(run_id, lease_epoch, outcome)

        audit = self.reload_audit(audit_id)
        self.assertEqual(audit.status, AuditStatus.closing.value, "过期版本不得写入")
        self.assertEqual(self.report_revisions(audit_id), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()