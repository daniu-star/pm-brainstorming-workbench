"""AC-20：模型编造数字的标注与验证建议（``services.number_guard``）。"""

from __future__ import annotations

import unittest

from domain import contracts as C
from services import number_guard as guard
from services.reducer import merge_role_results, RoleOutput

SESSION_ID = "a" * 32


def _claim(text: str, *, source_message_ids: list[str] | None = None) -> C.Claim:
    return C.Claim(
        id="claim-" + str(abs(hash(text)) % 10**6),
        kind=C.ClaimKind.fact,
        text=text,
        source_message_ids=list(source_message_ids or []),
    )


def _role_result(
    claims: list[C.Claim], *, role: C.RoleCode = C.RoleCode.cto
) -> C.RoleResult:
    return C.RoleResult(
        role_code=role,
        display_text="评审意见",
        claims=[
            C.CandidateClaim(
                local_id=f"c{index}",
                kind=claim.kind,
                text=claim.text,
                evidence_refs=list(claim.evidence_refs),
                source_message_ids=list(claim.source_message_ids),
            )
            for index, claim in enumerate(claims, start=1)
        ],
    )


class ExtractNumbersTests(unittest.TestCase):
    """数字抽取：只认独立数值，不把版本号/编号当数据。"""

    def test_extracts_numbers_in_order_without_duplicates(self) -> None:
        text = "已访谈 12 家客户，每周节省 6 小时，3.5% 提升，12 家客户里 9 家愿意付费"
        self.assertEqual(guard.extract_numbers(text), ["12", "6", "3.5", "9"])

    def test_ignores_version_and_identifier_like_tokens(self) -> None:
        # v2 / Q3 / a1b2 里的数字属于标识符，不是统计数据
        self.assertEqual(guard.extract_numbers("v2 版本在 Q3 上线，提交 a1b2c3"), [])

    def test_normalizes_decimal_and_leading_zero_tokens(self) -> None:
        self.assertEqual(guard.extract_numbers("转化率 3.50%，样本 012 份"), ["3.5", "12"])

    def test_extracts_date_parts_so_dates_are_checkable(self) -> None:
        # 日期整体也算来源信息：拆成 2026/11/24 后仍可逐段比对
        self.assertEqual(guard.extract_numbers("统计日期 2026-11-24"), ["2026", "11", "24"])

    def test_empty_text_returns_empty_list(self) -> None:
        self.assertEqual(guard.extract_numbers(""), [])
        self.assertEqual(guard.extract_numbers("没有数字的主张"), [])


class TraceabilityTests(unittest.TestCase):
    """可追溯性：来源原文里出现过的数字才允许当事实。"""

    def test_number_present_in_source_is_traceable(self) -> None:
        self.assertTrue(guard.number_is_traceable("12", "我们访谈了 12 家客户"))

    def test_number_absent_from_source_is_not_traceable(self) -> None:
        self.assertFalse(guard.number_is_traceable("40", "我们访谈了 12 家客户"))

    def test_boundary_numbers_are_not_confused(self) -> None:
        # 12 不能被 120 / 112 里的子串误判为可追溯
        self.assertFalse(guard.number_is_traceable("12", "共 120 家客户"))
        self.assertFalse(guard.number_is_traceable("12", "编号 1120"))
        self.assertTrue(guard.number_is_traceable("12", "共 12 家"))

    def test_decimal_normalization_applies_to_source(self) -> None:
        self.assertTrue(guard.number_is_traceable("3.5", "提升 3.50%"))
        self.assertTrue(guard.number_is_traceable("3.50", "提升 3.5%"))

    def test_verification_suggestion_mentions_numbers_and_hint(self) -> None:
        suggestion = guard.verification_suggestion(["40", "9"])
        assert suggestion is not None
        self.assertIn("40", suggestion)
        self.assertIn("9", suggestion)
        self.assertTrue(suggestion.endswith(guard.VERIFICATION_HINT))

    def test_verification_suggestion_is_none_without_numbers(self) -> None:
        self.assertIsNone(guard.verification_suggestion([]))


class AuditRoleResultTests(unittest.TestCase):
    """角色结果标注：只标注不否决，且能区分消息来源与证据来源。"""

    def test_marks_only_untraceable_numbers(self) -> None:
        result = _role_result(
            [
                _claim("12 家客户平均节省 40% 对账时间", source_message_ids=["m1"]),
            ]
        )
        audited, warnings = guard.audit_role_result(
            result,
            message_texts={"m1": "我们访谈了 12 家客户"},
            evidence_texts={},
        )
        self.assertEqual(audited.claims[0].unverified_numbers, ["40"])
        self.assertEqual(len(warnings), 1)
        self.assertIn("cto", warnings[0])
        self.assertIn("40", warnings[0])
        self.assertIn("未验证", warnings[0])

    def test_evidence_texts_count_as_source(self) -> None:
        result = _role_result(
            [
                C.CandidateClaim(
                    local_id="c1",
                    kind=C.ClaimKind.fact,
                    text="试点客户每周节省 6 小时",
                    evidence_refs=["ev-1"],
                )
            ]
        )
        audited, warnings = guard.audit_role_result(
            result, message_texts={}, evidence_texts={"ev-1": "访谈记录：每周节省 6 小时"}
        )
        self.assertEqual(audited.claims[0].unverified_numbers, [])
        self.assertEqual(warnings, [])

    def test_claim_without_sources_marks_all_numbers(self) -> None:
        result = _role_result([_claim("市场空间 300 亿，年增长 25%")])
        audited, warnings = guard.audit_role_result(
            result, message_texts={}, evidence_texts={}
        )
        self.assertEqual(audited.claims[0].unverified_numbers, ["300", "25"])
        self.assertEqual(len(warnings), 1)

    def test_claim_without_numbers_is_untouched(self) -> None:
        result = _role_result([_claim("财务团队需要更快的对账工具")])
        audited, warnings = guard.audit_role_result(
            result, message_texts={}, evidence_texts={}
        )
        self.assertEqual(audited.claims[0].unverified_numbers, [])
        self.assertEqual(warnings, [])

    def test_audit_claim_returns_same_object_when_nothing_changes(self) -> None:
        claim = _claim("没有数字")
        self.assertIs(guard.audit_claim(claim, source_text=""), claim)


class ReducerAndProjectionTests(unittest.TestCase):
    """标注必须一路带到产品状态与画布投影。"""

    def _merged_state(self, text: str) -> C.ProductState:
        result = _role_result([_claim(text, source_message_ids=["m1"])])
        audited, _ = guard.audit_role_result(
            result, message_texts={"m1": "我们访谈了 12 家客户"}, evidence_texts={}
        )
        state = C.ProductState(session_id=SESSION_ID, version=1)
        return merge_role_results(
            state,
            [RoleOutput(C.RoleCode.cto, "brainstorm.role.cto", audited)],
            run_id="run-1",
            id_factory=lambda kind, local_id, role: f"{kind}-{local_id}-{role}",
        )

    def test_unverified_numbers_reach_product_state(self) -> None:
        state = self._merged_state("12 家客户平均节省 40% 对账时间")
        self.assertEqual(len(state.claims), 1)
        self.assertEqual(state.claims[0].unverified_numbers, ["40"])

    def test_unverified_numbers_reach_canvas_node(self) -> None:
        from services import canvas_projector as projector

        projection = projector.project_canvas(self._merged_state("12 家客户节省 40% 时间"))
        nodes = [node for branch in projection.branches for node in branch.nodes]
        self.assertEqual(len(nodes), 1)
        node = nodes[0]
        self.assertEqual(node.unverified_numbers, ["40"])
        assert node.verification_suggestion is not None
        self.assertIn("40", node.verification_suggestion)
        self.assertIn("未验证数字：40", node.detail)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
