"""``services.evidence_guard`` 单元测试（FR-04C 引用归属校验）。

覆盖：合法引用（本会话证据 / 公开知识域证据 / 本会话消息）通过；未知或越权引用被
逐条列出并带出路径；``assert_role_result_refs`` 抛出的错误文案可供模型修复使用。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from domain import contracts as C  # noqa: E402
from services.evidence_guard import (
    normalize_message_refs,  # noqa: E402
    RefViolation,
    assert_role_result_refs,
    check_role_result_refs,
)


def _result(*claims: dict) -> C.RoleResult:
    return C.RoleResult.model_validate(
        {
            "schema_version": 1,
            "role_code": C.RoleCode.cto.value,
            "display_text": "评审意见",
            "claims": list(claims),
            "option_proposals": [],
            "critical_issues": [],
            "uncertainties": [],
            "suggested_experiments": [],
            "simulated_perspective": False,
        }
    )


def _claim(
    local_id: str = "c1",
    *,
    evidence_refs: list[str] | None = None,
    source_message_ids: list[str] | None = None,
) -> dict:
    return {
        "local_id": local_id,
        "kind": "fact",
        "text": f"{local_id} 的关键事实",
        "evidence_refs": list(evidence_refs or []),
        "source_message_ids": list(source_message_ids or []),
    }


class EvidenceGuardTests(unittest.TestCase):
    def test_allowed_references_pass(self) -> None:
        result = _result(
            _claim("c1", evidence_refs=["ev-session", "ev-public"], source_message_ids=["m1"])
        )
        violations = check_role_result_refs(
            result,
            allowed_evidence_ids={"ev-session", "ev-public"},
            allowed_message_ids={"m1", "m2"},
        )
        self.assertEqual(violations, [])

    def test_empty_references_pass(self) -> None:
        violations = check_role_result_refs(
            _result(_claim("c1")), allowed_evidence_ids=set(), allowed_message_ids=set()
        )
        self.assertEqual(violations, [])

    def test_unknown_evidence_is_reported_with_path(self) -> None:
        result = _result(
            _claim("c1"),
            _claim("c2", evidence_refs=["ev-foreign"]),
        )
        violations = check_role_result_refs(
            result, allowed_evidence_ids={"ev-session"}, allowed_message_ids=set()
        )
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0], RefViolation("claims[1].evidence_refs", "ev-foreign", "不属于本会话或公开知识域"))
        self.assertIn("claims[1]", str(violations[0]))

    def test_unknown_message_is_reported(self) -> None:
        result = _result(_claim("c1", source_message_ids=["m-neighbor"]))
        violations = check_role_result_refs(
            result, allowed_evidence_ids=set(), allowed_message_ids={"m1"}
        )
        self.assertEqual(len(violations), 1)
        self.assertEqual(
            violations[0],
            RefViolation("claims[0].source_message_ids", "m-neighbor", "不属于本会话"),
        )

    def test_assert_raises_with_repairable_message(self) -> None:
        result = _result(_claim("c1", evidence_refs=["ghost"]))
        with self.assertRaises(ValueError) as ctx:
            assert_role_result_refs(
                result, allowed_evidence_ids=set(), allowed_message_ids=set()
            )
        message = str(ctx.exception)
        self.assertIn("引用校验失败", message)
        self.assertIn("ghost", message)
        self.assertIn("不要臆造 ID", message)

    def test_assert_truncates_long_violation_lists(self) -> None:
        claims = [
            _claim(f"c{index}", evidence_refs=[f"ghost-{index}"]) for index in range(1, 9)
        ]
        with self.assertRaises(ValueError) as ctx:
            assert_role_result_refs(
                _result(*claims), allowed_evidence_ids=set(), allowed_message_ids=set()
            )
        message = str(ctx.exception)
        self.assertIn("共 8 条", message)

    def test_assert_passes_for_valid_references(self) -> None:
        result = _result(_claim("c1", evidence_refs=["ev-1"], source_message_ids=["m-1"]))
        assert_role_result_refs(
            result, allowed_evidence_ids={"ev-1"}, allowed_message_ids={"m-1"}
        )


if __name__ == "__main__":
    unittest.main()

class NormalizeMessageRefsTests(unittest.TestCase):
    """上下文把消息渲染成 ``[#seq]``，模型据此写 ``#3``；序号必须解析为本会话真实 ID。"""

    def test_ordinal_refs_are_mapped_to_real_ids(self) -> None:
        result = _result(
            _claim("c1", source_message_ids=["#2", "#7"]),
            _claim("c2", source_message_ids=["msg-real"]),
        )
        updated, notes = normalize_message_refs(
            result, message_seq_to_id={"2": "msg-2", "7": "msg-7"}
        )

        self.assertEqual(updated.claims[0].source_message_ids, ["msg-2", "msg-7"])
        self.assertEqual(updated.claims[1].source_message_ids, ["msg-real"])
        self.assertEqual(len(notes), 2)
        self.assertTrue(all("按会话序号解析" in note for note in notes))

    def test_unmapped_and_plain_ids_are_left_to_the_guard(self) -> None:
        result = _result(_claim("c1", source_message_ids=["#99", "msg-real"]))
        updated, notes = normalize_message_refs(result, message_seq_to_id={"2": "msg-2"})

        self.assertEqual(updated.claims[0].source_message_ids, ["#99", "msg-real"])
        self.assertEqual(notes, [])

        violations = check_role_result_refs(
            updated, allowed_evidence_ids=set(), allowed_message_ids={"msg-real"}
        )
        self.assertEqual([item.ref for item in violations], ["#99"])

    def test_no_mapping_returns_same_object(self) -> None:
        result = _result(_claim("c1", source_message_ids=["#2"]))
        updated, notes = normalize_message_refs(result, message_seq_to_id={})

        self.assertIs(updated, result)
        self.assertEqual(notes, [])
