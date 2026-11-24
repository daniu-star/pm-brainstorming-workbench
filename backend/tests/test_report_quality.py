"""第 30 批：`/health/detail` 的报告质量块（模型是否老在编数字）。

分两层测：
* 纯函数 `_report_quality_status`：阈值语义（无数据 / 样本不足 / 偶发 / 频繁 / 干净）；
* 端到端：真实 sqlite + 直接写两条报告行，确认 `report_quality_stats` 统计正确、
  `/health/detail` 把它透出且仍不泄露密钥。
"""
from __future__ import annotations

import json
import pathlib
import sys
import unittest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:  # pragma: no cover
    sys.path.insert(0, str(BACKEND))

import main  # noqa: E402
from domain import contracts as C  # noqa: E402
from repositories import audit_repo, database, session_repo  # noqa: E402
from tests.test_api_v2 import V2ApiTestBase  # noqa: E402

OWNER = "quality-owner@example.com"


class ReportQualityStatusTests(unittest.TestCase):
    """纯函数：只判断，不查库。"""

    def test_no_reports_says_no_data(self) -> None:
        status = main._report_quality_status({"scanned": 0})
        self.assertEqual(status["verdict"], "no_data")
        self.assertIn("无法判断", status["hint"])

    def test_small_sample_is_insufficient(self) -> None:
        status = main._report_quality_status(
            {"scanned": 2, "reports_with_unverified": 2, "unverified_numbers": 5}
        )
        self.assertEqual(status["verdict"], "insufficient")
        self.assertIn("样本不足", status["hint"])

    def test_clean_reports(self) -> None:
        status = main._report_quality_status({"scanned": 4, "reports_with_unverified": 0})
        self.assertEqual(status["verdict"], "clean")
        self.assertIn("都能在消息/证据里找到", status["hint"])

    def test_occasional_vs_frequent(self) -> None:
        occasional = main._report_quality_status(
            {"scanned": 4, "reports_with_unverified": 1, "unverified_numbers": 2}
        )
        self.assertEqual(occasional["verdict"], "occasional")

        frequent = main._report_quality_status(
            {"scanned": 4, "reports_with_unverified": 2, "unverified_numbers": 6}
        )
        self.assertEqual(frequent["verdict"], "frequent")
        self.assertIn("提示词", frequent["hint"])


class ReportQualityApiTests(V2ApiTestBase):
    """真实库：写两条报告 → 统计 → 接口透出。"""

    def test_stats_count_annotated_reports(self) -> None:
        db = database.new_session()
        self.addCleanup(db.close)
        session = session_repo.create_session(
            db, owner_email=OWNER, problem_statement="报告质量统计"
        )
        for index, numbers in enumerate((["2"], [])):
            audit = audit_repo.create_audit(
                db,
                session_id=session.id,
                product_version=1,
                now_iso=C.utc_now_iso(),
            )
            report = C.AuditReport.model_validate(
                {
                    "audit_id": audit.id,
                    "product_version": 1,
                    "audit_version": audit.audit_version,
                    "question_count": 1,
                    "dimension_states": [],
                    "supported_points": [f"第 {index} 份报告"],
                    "unverified_numbers": numbers,
                    "generated_by": "model",
                }
            )
            audit_repo.update_audit(
                db,
                audit,
                now_iso=C.utc_now_iso(),
                status=C.AuditStatus.completed,
                report=report.model_dump(mode="json"),
            )
        db.commit()

        stats = audit_repo.report_quality_stats(db, since_iso="2000-01-01T00:00:00Z")
        self.assertEqual(stats["scanned"], 2)
        self.assertEqual(stats["reports_with_unverified"], 1)
        self.assertEqual(stats["unverified_numbers"], 1)
        self.assertEqual(stats["samples"][0]["unverified_numbers"], ["2"])

        resp = self.get("/health/detail")
        self.assertEqual(resp.status_code, 200, resp.text)
        block = resp.json().get("report_quality")
        self.assertIsInstance(block, dict, "必须暴露 report_quality 块")
        self.assertTrue(block.get("db"))
        for key in ("verdict", "hint", "scanned", "reports_with_unverified", "window"):
            self.assertIn(key, block)
        self.assertEqual(block["verdict"], "insufficient")
        # 仍然不能把密钥带出去
        raw = json.dumps(block, ensure_ascii=False).lower()
        self.assertNotIn("sk-", raw)
        self.assertNotIn("api_key", raw)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
