"""ReportDraft 形状容错（第 17 批）。

真实模型写报告时常带出多余字段或把字符串/数组写反（``limitations`` 写成数组、
``supported_points`` 写成单个字符串）。这些是「形状偏差」而非语义错误：直接判
失败会让每次真实审计都落到确定性兜底报告，所以 ``ReportDraft`` 在校验前做有界
归一。本文件守住四条边界：白名单、数组纠正、字符串纠正、长度上限仍生效。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from pydantic import ValidationError  # noqa: E402

from workflows.audit import ReportDraft  # noqa: E402


class ReportDraftShapeTests(unittest.TestCase):
    def test_extra_fields_are_dropped_not_fatal(self) -> None:
        draft = ReportDraft.model_validate(
            {
                "type": "audit_report",
                "generated_by": "model",
                "audit_version": 3,
                "supported_points": ["访谈 12 家，9 家确认每周对账 6 小时以上"],
                "unresolved_gaps": [],
                "recommended_experiments": [],
                "limitations": "样本集中在华东地区。",
                "summary": "问题成立，但付费链路未验证。",
            }
        )
        self.assertEqual(draft.supported_points, ["访谈 12 家，9 家确认每周对账 6 小时以上"])
        self.assertEqual(draft.limitations, "样本集中在华东地区。")
        self.assertFalse(hasattr(draft, "type"))

    def test_array_fields_accept_single_string_and_drop_blanks(self) -> None:
        draft = ReportDraft.model_validate(
            {
                "supported_points": "只有一条结论",
                "unresolved_gaps": ["   ", "采购周期未验证", 42],
                "recommended_experiments": None,
            }
        )
        self.assertEqual(draft.supported_points, ["只有一条结论"])
        self.assertEqual(draft.unresolved_gaps, ["采购周期未验证", "42"])
        self.assertEqual(draft.recommended_experiments, [])

    def test_text_fields_join_lists_and_coerce_none(self) -> None:
        draft = ReportDraft.model_validate(
            {
                "limitations": ["样本有限", "未覆盖大客户"],
                "summary": None,
            }
        )
        self.assertEqual(draft.limitations, "样本有限；未覆盖大客户")
        self.assertEqual(draft.summary, "")

    def test_length_limits_still_apply(self) -> None:
        with self.assertRaises(ValidationError):
            ReportDraft.model_validate(
                {"supported_points": [f"结论 {index}" for index in range(17)]}
            )
        with self.assertRaises(ValidationError):
            ReportDraft.model_validate({"summary": "字" * 4001})

    def test_valid_payload_is_unchanged(self) -> None:
        payload = {
            "supported_points": ["结论一"],
            "unresolved_gaps": ["未决一"],
            "recommended_experiments": ["实验一"],
            "limitations": "样本有限。",
            "summary": "总结。",
        }
        draft = ReportDraft.model_validate(payload)
        self.assertEqual(draft.model_dump(), payload)


if __name__ == "__main__":
    unittest.main()
