"""第 29 批：报告数字标注在读取时按最新证据重算（零模型成本）。

``audit_workflow._apply_report_draft`` 只在落库那一刻标注；用户之后补的证据
不会自动反映到已落库的报告上。``audit_repo._remark_report_numbers`` 在
``audit_to_snapshot`` 读取时重算，所以「补充来源」之后徽标应当立即消失，
不需要重新生成报告（也就不消耗模型额度）。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from domain import contracts as C  # noqa: E402
from repositories import audit_repo, database, session_repo  # noqa: E402
from repositories.database import AuditRow  # noqa: E402

OWNER = "recompute-owner@example.com"


class ReportNumberRecomputeTests(unittest.TestCase):
    """真实 sqlite + 真实仓储；不涉及 LLM。"""

    def setUp(self) -> None:
        tmp_root = Path(__file__).resolve().parents[1] / ".tmp_tests"
        tmp_root.mkdir(exist_ok=True)
        self._tmpdir = tempfile.mkdtemp(prefix="audit-recompute-", dir=str(tmp_root))
        db_path = Path(self._tmpdir, "t.db").as_posix()
        database.configure(f"sqlite:///{db_path}", force=True)
        database.initialize_database()
        self.db = database.new_session()
        self.session = session_repo.create_session(
            self.db,
            owner_email=OWNER,
            problem_statement="AC-20 报告数字重算：为财务团队做对账助手",
        )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        database.dispose_engine()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _make_audit(self, *, generated_by: str = "model") -> AuditRow:
        audit = audit_repo.create_audit(
            self.db,
            session_id=self.session.id,
            product_version=1,
            now_iso=C.utc_now_iso(),
        )
        report = C.AuditReport.model_validate(
            {
                "audit_id": audit.id,
                "product_version": 1,
                "audit_version": audit.audit_version,
                "end_reason": C.EndReason.completed.value,
                "question_count": 1,
                "dimension_states": [
                    {"key": "problem_validity", "assessment": "supported", "rationale": "已确认"}
                ],
                "supported_points": ["财务团队每周对账 6 小时。"],
                "unresolved_gaps": ["每周节省 2 小时的依据不足。"],
                "recommended_experiments": [],
                "limitations": "",
                "summary": "方向可行，但节省 2 小时需要实测证据。",
                "generated_by": generated_by,
                "unverified_numbers": ["2"] if generated_by == "model" else [],
                "verification_suggestion": "未验证数字：2。请补充这条数字的来源与统计日期。"
                if generated_by == "model"
                else None,
            }
        )
        audit_repo.update_audit(
            self.db,
            audit,
            now_iso=C.utc_now_iso(),
            status=C.AuditStatus.completed,
            question_count=1,
            report=report.model_dump(mode="json"),
        )
        self.db.commit()
        return audit

    def test_snapshot_recomputes_and_drops_annotation_once_evidence_covers_it(self) -> None:
        audit = self._make_audit()
        before = audit_repo.audit_to_snapshot(self.db, audit)
        # 落库时只标了 "2"；读取时按「有来源才算可追溯」重算，此时两条数字都没来源
        self.assertEqual(before.report.unverified_numbers, ["6", "2"])

        session_repo.add_evidence(
            self.db,
            self.session,
            kind=C.EvidenceKind.user_statement,
            title="试点实测",
            excerpt="2026-11 在 3 家客户试点两周：对账从每周 6 小时降到每周 2 小时。",
            created_by=OWNER,
        )
        self.db.commit()

        after = audit_repo.audit_to_snapshot(self.db, audit)
        self.assertEqual(after.report.unverified_numbers, [])
        self.assertIsNone(after.report.verification_suggestion)

    def test_fallback_report_is_never_annotated(self) -> None:
        # 兜底报告的数字来自结构化记录，读取时不应被重算成「未验证」
        audit = self._make_audit(generated_by="fallback")
        snapshot = audit_repo.audit_to_snapshot(self.db, audit)
        self.assertEqual(snapshot.report.unverified_numbers, [])
        self.assertIsNone(snapshot.report.verification_suggestion)

    def test_annotation_survives_when_number_still_untraceable(self) -> None:
        audit = self._make_audit()
        session_repo.add_evidence(
            self.db,
            self.session,
            kind=C.EvidenceKind.user_statement,
            title="现状记录",
            excerpt="财务团队目前每周对账 6 小时。",
            created_by=OWNER,
        )
        self.db.commit()

        snapshot = audit_repo.audit_to_snapshot(self.db, audit)
        # "6" 已经有来源，"2 小时" 仍缺实测证据 → 只剩它被标
        self.assertEqual(snapshot.report.unverified_numbers, ["2"])
        self.assertIsNotNone(snapshot.report.verification_suggestion)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
