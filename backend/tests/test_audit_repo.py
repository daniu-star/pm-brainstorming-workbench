"""``audit_repo`` 的集成测试：真实 SQLite 临时库 + 仓储层事务语义。

覆盖契约 §3.3 的关键不变量：

- 审计实例的创建/读取/归属校验（NOT_FOUND / FORBIDDEN）；
- 开放审计与最近审计的选取；
- 局部更新、状态与序号校验；
- 问题序号 1..18 唯一（含上限）；
- 回答、评估结论以及“修订只追加不覆盖”；
- 报告版本追加；
- 审计快照组装与坏数据容错；
- 控制意图幂等（同 request_id 返回原行，不同动作报错）。
"""

from __future__ import annotations

import contextlib
import json
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import select

from domain.contracts import (
    AUDIT_QUESTION_HARD_LIMIT,
    Assessment,
    AuditStatus,
    EndReason,
    new_id,
)
from domain.errors import AppError, ErrorCode
from repositories import audit_repo, database
from repositories.database import (
    AuditAssessmentRevisionRow,
    ControlIntentRow,
    RunRow,
    SessionRow,
)

NOW = "2026-11-18T03:00:00Z"
NOW_LATER = "2026-11-18T03:05:00Z"
OWNER = "owner@example.com"
OTHER_OWNER = "intruder@example.com"


class AuditRepoTestCase(unittest.TestCase):
    """每个测试一个独立临时 SQLite 文件，避免污染 ``backend/data/app.db``。"""

    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tempdir.name) / "audit.db"
        database.configure(f"sqlite:///{self.db_path}", force=True)
        database.initialize_database()
        with database.session_scope() as db:
            self.session_id = self._create_session(db)

    def tearDown(self) -> None:
        # 恢复全局默认配置：否则后续测试（如 core.auth 的 _ensure_schema）会复用
        # 指向已删除临时库的 engine，报 sqlite3.OperationalError: unable to open database file。
        with contextlib.suppress(Exception):
            database.configure(database.default_database_url(), force=True)
        self._tempdir.cleanup()

    # ------------------------------------------------------------------ #
    # 测试脚手架
    # ------------------------------------------------------------------ #
    @staticmethod
    def _create_session(
        db,
        *,
        owner: str = OWNER,
        session_id: str | None = None,
        phase: str = "auditing",
        deleted_at: str | None = None,
    ) -> str:
        row = SessionRow(
            id=session_id or new_id(),
            owner_email=owner,
            phase=phase,
            session_version=1,
            current_product_version=1,
            problem_statement="测试用问题陈述",
            brief_status="draft",
            next_message_seq=1,
            created_at=NOW,
            updated_at=NOW,
            deleted_at=deleted_at,
        )
        db.add(row)
        db.flush()
        return row.id

    @staticmethod
    def session_id_fallback(db) -> str:
        row = db.execute(select(SessionRow)).scalars().first()
        assert row is not None
        return row.id

    def _audit(self, db, *, product_version: int = 1):
        fallback = self.session_id_fallback(db)
        return audit_repo.create_audit(
            db,
            session_id=fallback,
            product_version=product_version,
            now_iso=NOW,
        )

    # ------------------------------------------------------------------ #
    # 创建 / 读取 / 归属
    # ------------------------------------------------------------------ #
    def test_create_and_get_audit_defaults(self) -> None:
        with database.session_scope() as db:
            audit = self._audit(db)
            self.assertEqual(audit.status, AuditStatus.created.value)
            self.assertEqual(audit.audit_version, 1)
            self.assertEqual(audit.question_count, 0)
            self.assertEqual(audit.audit_version, 1)

            fetched = audit_repo.get_audit(db, audit.id)
            self.assertIsNotNone(fetched)
            self.assertEqual(fetched.id, audit.id)

            self.assertIsNone(audit_repo.get_audit(db, "f" * 32))

    def test_require_audit_enforces_ownership_and_existence(self) -> None:
        with database.session_scope() as db:
            audit = self._audit(db)

            required = audit_repo.require_audit(db, audit.id, owner_email=OWNER)
            self.assertEqual(required.id, audit.id)

            with self.assertRaises(AppError) as ctx:
                audit_repo.require_audit(db, audit.id, owner_email=OTHER_OWNER)
            self.assertEqual(ctx.exception.code, ErrorCode.FORBIDDEN)

            with self.assertRaises(AppError) as ctx:
                audit_repo.require_audit(db, new_id(), owner_email=OWNER)
            self.assertEqual(ctx.exception.code, ErrorCode.NOT_FOUND)

    def test_require_audit_hides_deleted_session(self) -> None:
        with database.session_scope() as db:
            audit = self._audit(db)
            session = db.get(SessionRow, self.session_id_fallback(db))
            assert session is not None
            session.deleted_at = NOW_LATER
            db.flush()

            with self.assertRaises(AppError) as ctx:
                audit_repo.require_audit(db, audit.id, owner_email=OWNER)
            self.assertEqual(ctx.exception.code, ErrorCode.NOT_FOUND)

    # ------------------------------------------------------------------ #
    # 开放 / 最近审计
    # ------------------------------------------------------------------ #
    def test_open_and_latest_audit_selection(self) -> None:
        with database.session_scope() as db:
            closed = audit_repo.create_audit(
                db, session_id=self.session_id_fallback(db), product_version=1, now_iso=NOW
            )
            audit_repo.update_audit(db, closed, now_iso=NOW, status=AuditStatus.completed)

            self.assertIsNone(audit_repo.open_audit_for_session(db, self.session_id_fallback(db)))

            opened = audit_repo.create_audit(
                db, session_id=self.session_id_fallback(db), product_version=2, now_iso=NOW_LATER
            )
            open_row = audit_repo.open_audit_for_session(db, self.session_id_fallback(db))
            self.assertIsNotNone(open_row)
            assert open_row is not None
            self.assertEqual(open_row.id, opened.id)

            latest = audit_repo.latest_audit_for_session(db, self.session_id_fallback(db))
            self.assertIsNotNone(latest)
            assert latest is not None
            self.assertEqual(latest.id, opened.id)

    # ------------------------------------------------------------------ #
    # 局部更新
    # ------------------------------------------------------------------ #
    def test_update_audit_partial_and_validation(self) -> None:
        with database.session_scope() as db:
            audit = self._audit(db)

            audit_repo.update_audit(
                db,
                audit,
                now_iso=NOW_LATER,
                status=AuditStatus.active,
                audit_version=2,
                question_count=3,
                pending_continuation=True,
                close_requested=True,
                active_question_id="q-1",
            )
            self.assertEqual(audit.status, AuditStatus.active.value)
            self.assertEqual(audit.audit_version, 2)
            self.assertEqual(audit.question_count, 3)
            self.assertEqual(audit.pending_continuation, 1)
            self.assertEqual(audit.close_requested, 1)
            self.assertEqual(audit.active_question_id, "q-1")
            self.assertEqual(audit.updated_at, NOW_LATER)

            audit_repo.update_audit(
                db,
                audit,
                now_iso=NOW_LATER,
                status=AuditStatus.completed,
                end_reason=EndReason.user_ended,
                active_question_id=None,
            )
            self.assertEqual(audit.status, AuditStatus.completed.value)
            self.assertEqual(audit.end_reason, EndReason.user_ended.value)
            self.assertIsNone(audit.active_question_id)

            with self.assertRaises(AppError) as ctx:
                audit_repo.update_audit(db, audit, now_iso=NOW, question_count=19)
            self.assertEqual(ctx.exception.code, ErrorCode.INVALID_REQUEST)

            with self.assertRaises(AppError) as ctx:
                audit_repo.update_audit(db, audit, now_iso=NOW, status="not-a-status")
            self.assertEqual(ctx.exception.code, ErrorCode.INVALID_REQUEST)

            with self.assertRaises(AppError) as ctx:
                audit_repo.update_audit(db, audit, now_iso=NOW, audit_version=0)
            self.assertEqual(ctx.exception.code, ErrorCode.INVALID_REQUEST)

    # ------------------------------------------------------------------ #
    # 问题序号
    # ------------------------------------------------------------------ #
    def test_add_question_ordinal_bounds_and_uniqueness(self) -> None:
        with database.session_scope() as db:
            audit = self._audit(db)

            question = audit_repo.add_question(
                db,
                audit,
                ordinal=1,
                dimension="problem_validity",
                issue_refs=["issue-a"],
                question_text="目标用户是谁？",
                question_message_id="m-1",
                now_iso=NOW,
            )
            self.assertEqual(question.ordinal, 1)
            self.assertEqual(question.assessment, Assessment.awaiting_answer.value)
            self.assertEqual(audit.question_count, 1)

            with self.assertRaises(AppError) as ctx:
                audit_repo.add_question(
                    db,
                    audit,
                    ordinal=1,
                    dimension="problem_validity",
                    issue_refs=[],
                    question_text="重复序号",
                    now_iso=NOW,
                )
            self.assertEqual(ctx.exception.code, ErrorCode.INVALID_REQUEST)

            for bad_ordinal in (0, AUDIT_QUESTION_HARD_LIMIT + 1):
                with self.assertRaises(AppError) as ctx:
                    audit_repo.add_question(
                        db,
                        audit,
                        ordinal=bad_ordinal,
                        dimension="problem_validity",
                        issue_refs=[],
                        question_text="越界序号",
                        now_iso=NOW,
                    )
                self.assertEqual(ctx.exception.code, ErrorCode.INVALID_REQUEST)

            with self.assertRaises(AppError) as ctx:
                audit_repo.add_question(
                    db,
                    audit,
                    ordinal=2,
                    dimension="not_a_dimension",
                    issue_refs=[],
                    question_text="未知维度",
                    now_iso=NOW,
                )
            self.assertEqual(ctx.exception.code, ErrorCode.INVALID_REQUEST)

            last = audit_repo.add_question(
                db,
                audit,
                ordinal=AUDIT_QUESTION_HARD_LIMIT,
                dimension="execution_risk",
                issue_refs=[],
                question_text="最后一个问题",
                now_iso=NOW,
            )
            self.assertEqual(last.ordinal, AUDIT_QUESTION_HARD_LIMIT)
            self.assertEqual(audit.question_count, AUDIT_QUESTION_HARD_LIMIT)
            self.assertEqual(len(audit_repo.questions_for_audit(db, audit.id)), 2)

    # ------------------------------------------------------------------ #
    # 回答与评估
    # ------------------------------------------------------------------ #
    def test_answer_and_record_assessment(self) -> None:
        with database.session_scope() as db:
            audit = self._audit(db)
            question = audit_repo.add_question(
                db,
                audit,
                ordinal=1,
                dimension="technical_risk",
                issue_refs=[],
                question_text="技术风险是什么？",
                now_iso=NOW,
            )

            audit_repo.answer_question(db, question, answer_message_id="msg-answer", now_iso=NOW_LATER)
            self.assertEqual(question.answer_message_id, "msg-answer")

            audit_repo.record_assessment(
                db,
                question,
                assessment=Assessment.supported,
                rationale="有证据支撑",
                evidence_refs=["ev-1", "ev-2"],
                now_iso=NOW_LATER,
            )
            self.assertEqual(question.assessment, Assessment.supported.value)
            self.assertEqual(question.rationale, "有证据支撑")
            self.assertEqual(json.loads(question.evidence_refs_json), ["ev-1", "ev-2"])

            with self.assertRaises(AppError) as ctx:
                audit_repo.record_assessment(
                    db,
                    question,
                    assessment="bogus",
                    rationale="",
                    evidence_refs=[],
                    now_iso=NOW_LATER,
                )
            self.assertEqual(ctx.exception.code, ErrorCode.INVALID_REQUEST)

            fetched = audit_repo.get_question(db, audit.id, question.id)
            self.assertIsNotNone(fetched)
            assert fetched is not None
            self.assertEqual(fetched.assessment, Assessment.supported.value)

    # ------------------------------------------------------------------ #
    # 修订只追加
    # ------------------------------------------------------------------ #
    def test_assessment_revisions_are_append_only(self) -> None:
        with database.session_scope() as db:
            audit = self._audit(db)

            first_id = audit_repo.add_assessment_revision(
                db,
                audit_id=audit.id,
                audit_version=1,
                dimension="problem_validity",
                before={"assessment": "awaiting_answer"},
                after={"assessment": "gap"},
                origin="model",
                now_iso=NOW,
            )
            second_id = audit_repo.add_assessment_revision(
                db,
                audit_id=audit.id,
                audit_version=1,
                dimension="problem_validity",
                before={"assessment": "gap"},
                after={"assessment": "supported"},
                origin="human",
                source_message_ids=["m-1"],
                evidence_refs=["ev-1"],
                rationale="人工修订",
                now_iso=NOW_LATER,
            )
            self.assertNotEqual(first_id, second_id)

            rows = list(
                db.execute(
                    select(AuditAssessmentRevisionRow)
                    .where(AuditAssessmentRevisionRow.audit_id == audit.id)
                    .order_by(AuditAssessmentRevisionRow.created_at.asc())
                ).scalars()
            )
            self.assertEqual([row.id for row in rows], [first_id, second_id])
            self.assertEqual(json.loads(rows[0].before_json), {"assessment": "awaiting_answer"})
            self.assertEqual(json.loads(rows[0].after_json), {"assessment": "gap"})
            self.assertEqual(json.loads(rows[1].before_json), {"assessment": "gap"})

            with self.assertRaises(AppError):
                audit_repo.add_assessment_revision(
                    db,
                    audit_id=audit.id,
                    audit_version=1,
                    before={},
                    after={},
                    origin="not-an-origin",
                    now_iso=NOW,
                )

    # ------------------------------------------------------------------ #
    # 报告版本
    # ------------------------------------------------------------------ #
    def test_report_revisions_and_snapshot(self) -> None:
        with database.session_scope() as db:
            audit = self._audit(db)
            report = {
                "audit_id": audit.id,
                "product_version": 1,
                "audit_version": 2,
                "end_reason": "completed",
                "question_count": 1,
                "dimension_states": [{"key": "problem_validity", "assessment": "supported"}],
                "supported_points": ["问题清晰"],
                "unresolved_gaps": [],
                "recommended_experiments": [],
                "limitations": "",
                "human_revised": False,
                "generated_by": "model",
            }

            first = audit_repo.save_report_revision(
                db, audit_id=audit.id, audit_version=1, report=report, now_iso=NOW
            )
            second = audit_repo.save_report_revision(
                db,
                audit_id=audit.id,
                audit_version=2,
                report=report,
                generated_by="fallback",
                now_iso=NOW_LATER,
            )
            revisions = audit_repo.list_report_revisions(db, audit.id)
            self.assertEqual([row.id for row in revisions], [first, second])
            self.assertEqual(revisions[1].generated_by, "fallback")

            audit_repo.update_audit(
                db,
                audit,
                now_iso=NOW_LATER,
                audit_version=2,
                dimension_states=[{"key": "problem_validity", "assessment": "supported"}],
                report=report,
            )
            snapshot = audit_repo.audit_to_snapshot(db, audit)
            self.assertEqual(snapshot.id, audit.id)
            self.assertEqual(snapshot.audit_version, 2)
            self.assertEqual(len(snapshot.dimension_states), 1)
            self.assertEqual(snapshot.dimension_states[0].key, "problem_validity")
            self.assertIsNotNone(snapshot.report)
            assert snapshot.report is not None
            self.assertEqual(snapshot.report.audit_version, 2)

    def test_audit_to_snapshot_tolerates_bad_dimension_data(self) -> None:
        with database.session_scope() as db:
            audit = self._audit(db)
            audit_repo.update_audit(
                db,
                audit,
                now_iso=NOW,
                dimension_states=[
                    {"key": "problem_validity", "assessment": "supported"},
                    {"key": "unknown_dimension"},
                    "not-an-object",
                ],
                report={"audit_id": audit.id},
            )
            snapshot = audit_repo.audit_to_snapshot(db, audit)
            self.assertEqual([state.key for state in snapshot.dimension_states], ["problem_validity"])
            # 不合法报告降级为 None，而不是让读取路径崩溃。
            self.assertIsNone(snapshot.report)

    # ------------------------------------------------------------------ #
    # 失败原因可见（PRD-03）
    # ------------------------------------------------------------------ #
    def test_snapshot_exposes_latest_failed_run_error(self) -> None:
        """PRD-03：审计失败时快照必须带最近失败 Run 的错误码/文案。

        修复前失败原因只存在 Run 上，审计页只能显示「报告未生成」，用户既看不到
        原因也没有重试依据。
        """

        with database.session_scope() as db:
            audit = self._audit(db)
            audit_repo.update_audit(db, audit, now_iso=NOW, status=AuditStatus.failed, end_reason=EndReason.failed)
            session_id = self.session_id_fallback(db)
            for run_id, error_code, error_message, created_at in (
                ("r" * 32, "MODEL_OUTPUT_INVALID", "开场问题未能通过协议校验", NOW),
                ("s" * 32, "MODEL_TIMEOUT", "开场问题生成超时", NOW_LATER),
            ):
                db.add(
                    RunRow(
                        id=run_id,
                        session_id=session_id,
                        owner_email=OWNER,
                        request_id=f"req-{run_id[0]}",
                        action="start_audit",
                        input_hash="",
                        base_session_version=1,
                        base_product_version=1,
                        status="failed",
                        audit_id=audit.id,
                        error_code=error_code,
                        error_message=error_message,
                        created_at=created_at,
                    )
                )
            db.flush()
            snapshot = audit_repo.audit_to_snapshot(db, audit_repo.get_audit(db, audit.id))
            self.assertEqual(snapshot.status, AuditStatus.failed)
            self.assertEqual(snapshot.last_error_code, "MODEL_TIMEOUT")
            self.assertEqual(snapshot.last_error_message, "开场问题生成超时")

    def test_snapshot_omits_error_fields_when_audit_not_failed(self) -> None:
        """非 failed 审计不暴露历史错误（避免把已恢复的失败当现状展示）。"""

        with database.session_scope() as db:
            audit = self._audit(db)
            audit_repo.update_audit(db, audit, now_iso=NOW, status=AuditStatus.active)
            db.add(
                RunRow(
                    id="t" * 32,
                    session_id=self.session_id_fallback(db),
                    owner_email=OWNER,
                    request_id="req-t",
                    action="answer_audit",
                    input_hash="",
                    base_session_version=1,
                    base_product_version=1,
                    status="failed",
                    audit_id=audit.id,
                    error_code="MODEL_OUTPUT_INVALID",
                    error_message="回答未被接受",
                    created_at=NOW,
                )
            )
            db.flush()
            snapshot = audit_repo.audit_to_snapshot(db, audit_repo.get_audit(db, audit.id))
            self.assertIsNone(snapshot.last_error_code)
            self.assertIsNone(snapshot.last_error_message)

    # ------------------------------------------------------------------ #
    # 控制意图
    # ------------------------------------------------------------------ #
    def test_control_intent_idempotency(self) -> None:
        with database.session_scope() as db:
            audit = self._audit(db)
            control = audit_repo.create_control_intent(
                db,
                session_id=self.session_id_fallback(db),
                audit_id=audit.id,
                owner_email=OWNER,
                request_id="req-1",
                action="end_audit",
                now_iso=NOW,
            )
            self.assertEqual(control.status, "accepted")

            again = audit_repo.create_control_intent(
                db,
                session_id=self.session_id_fallback(db),
                audit_id=audit.id,
                owner_email=OWNER,
                request_id="req-1",
                action="end_audit",
                now_iso=NOW_LATER,
            )
            self.assertEqual(again.id, control.id)

            with self.assertRaises(AppError) as ctx:
                audit_repo.create_control_intent(
                    db,
                    session_id=self.session_id_fallback(db),
                    audit_id=audit.id,
                    owner_email=OWNER,
                    request_id="req-1",
                    action="cancel_audit",
                    now_iso=NOW,
                )
            self.assertEqual(ctx.exception.code, ErrorCode.IDEMPOTENCY_KEY_REUSED)

            stored = audit_repo.get_control(db, control.id)
            self.assertIsNotNone(stored)
            by_request = audit_repo.get_control_by_request(db, OWNER.upper(), "req-1")
            self.assertIsNotNone(by_request)
            assert stored is not None and by_request is not None
            self.assertEqual(by_request.id, stored.id)

            pending = audit_repo.pending_end_intent(db, audit.id)
            self.assertIsNotNone(pending)
            assert pending is not None
            self.assertEqual(pending.id, control.id)

            audit_repo.update_control(
                db,
                stored,
                status="applied",
                related_run_id="run-1",
                now_iso=NOW_LATER,
            )
            self.assertEqual(stored.status, "applied")
            self.assertEqual(stored.related_run_id, "run-1")
            self.assertIsNone(audit_repo.pending_end_intent(db, audit.id))

            with self.assertRaises(AppError) as ctx:
                audit_repo.update_control(db, stored, status="bogus", now_iso=NOW)
            self.assertEqual(ctx.exception.code, ErrorCode.INVALID_REQUEST)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()