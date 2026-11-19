"""``services.loop_harness`` 纯函数测试（契约 §5.5）。

这些用例不碰数据库、不调模型：只断言确定性校验/评分规则本身。
"""
from __future__ import annotations

import unittest

from pydantic import ValidationError

from domain import contracts as C
from services import loop_harness as harness


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #


def _feature(
    feature_id: str = "f1",
    *,
    priority: C.FeaturePriority = C.FeaturePriority.must,
    acceptance: str = "可验收：打开页面能看到结果",
) -> C.MvpFeature:
    return C.MvpFeature(
        id=feature_id,
        title=f"功能 {feature_id}",
        description="完成一个最小可交付结果",
        acceptance=acceptance,
        priority=priority,
        effort=C.EffortSize.m,
    )


def _spec(**overrides) -> C.LoopMvpSpec:
    values = {
        "title": "MVP 规格",
        "summary": "最小可交付闭环",
        "features": [_feature()],
        "assumptions": [],
        "not_in_scope": [],
    }
    values.update(overrides)
    return C.LoopMvpSpec(**values)


def _check(
    check_id: str,
    feature_id: str | None,
    result: C.CheckResult,
    *,
    question: str = "这个假设成立吗？",
) -> C.ValidationCheck:
    return C.ValidationCheck(
        id=check_id,
        feature_id=feature_id,
        question=question,
        result=result,
    )


def _validation(checks: list[C.ValidationCheck], **overrides) -> C.LoopValidation:
    values = {"checks": checks, "summary": "验证结论", "unknowns": []}
    values.update(overrides)
    return C.LoopValidation(**values)


def _case(
    case_id: str,
    feature_id: str | None,
    status: C.TestCaseStatus,
    *,
    expected: str = "预期结果符合验收标准",
) -> C.LoopTestCase:
    return C.LoopTestCase(
        id=case_id,
        feature_id=feature_id,
        title=f"用例 {case_id}",
        expected=expected,
        status=status,
    )


def _report(cases: list[C.LoopTestCase], **overrides) -> C.LoopTestReport:
    counted = {
        "total": len(cases),
        "passed": sum(1 for case in cases if case.status == C.TestCaseStatus.passed),
        "failed": sum(1 for case in cases if case.status == C.TestCaseStatus.failed),
        "skipped": sum(1 for case in cases if case.status == C.TestCaseStatus.skipped),
        "simulated": True,
        "cases": cases,
        "notes": "本轮为模拟执行，未实际执行",
    }
    counted.update(overrides)
    return C.LoopTestReport(**counted)


def _card(
    *,
    scores: dict[str, int] | None = None,
    blockers: list[str] | None = None,
    total: int | None = None,
) -> C.LoopScoreCard:
    scores = scores or {key.value: max_score for key, max_score in C.LOOP_SCORE_MAX.items()}
    dimensions = [
        C.LoopScoreDimension(
            key=C.LoopScoreKey(key),
            score=int(scores.get(key, max_score)),
            max=max_score,
            reason="按实际产物打分",
        )
        for key, max_score in C.LOOP_SCORE_MAX.items()
    ]
    computed = sum(dimension.score for dimension in dimensions)
    return C.LoopScoreCard(
        total=computed if total is None else total,
        dimensions=dimensions,
        verdict=C.LoopVerdict.pending,
        blockers=list(blockers or []),
        highlights=[],
        next_actions=[],
        generated_by="model",
        hard_capped=False,
    )


# --------------------------------------------------------------------------- #
# MVP 规格校验
# --------------------------------------------------------------------------- #


class MvpSpecValidationTests(unittest.TestCase):
    def test_valid_spec_has_no_blockers(self) -> None:
        self.assertEqual(harness.validate_mvp_spec(_spec()), [])

    def test_empty_features_is_blocker(self) -> None:
        spec = _spec().model_copy(update={"features": []})
        blockers = harness.validate_mvp_spec(spec)
        self.assertTrue(any("features 为空" in blocker for blocker in blockers), blockers)

    def test_duplicate_feature_id_is_blocker(self) -> None:
        spec = _spec().model_copy(
            update={"features": [_feature("f1"), _feature("f1", acceptance="另一个验收")]}
        )
        blockers = harness.validate_mvp_spec(spec)
        self.assertTrue(any("功能 id 重复：f1" in blocker for blocker in blockers), blockers)

    def test_missing_acceptance_is_blocker(self) -> None:
        feature = _feature("f1").model_copy(update={"acceptance": "   "})
        spec = _spec().model_copy(update={"features": [feature]})
        blockers = harness.validate_mvp_spec(spec)
        self.assertTrue(any("缺少验收标准" in blocker for blocker in blockers), blockers)

    def test_no_must_feature_is_blocker(self) -> None:
        spec = _spec().model_copy(
            update={
                "features": [
                    _feature("f1", priority=C.FeaturePriority.should),
                    _feature("f2", priority=C.FeaturePriority.could),
                ]
            }
        )
        blockers = harness.validate_mvp_spec(spec)
        self.assertTrue(any("must" in blocker for blocker in blockers), blockers)


# --------------------------------------------------------------------------- #
# 验证阶段校验
# --------------------------------------------------------------------------- #


class ValidationStageTests(unittest.TestCase):
    def test_empty_checks_is_blocker(self) -> None:
        blockers = harness.validate_validation(_validation([]), _spec())
        self.assertTrue(any("checks 为空" in blocker for blocker in blockers), blockers)

    def test_must_feature_without_check_is_blocker(self) -> None:
        spec = _spec(
            features=[
                _feature("f1"),
                _feature("f2", acceptance="可验收：导出结果"),
            ]
        )
        validation = _validation(
            [_check("c1", "f1", C.CheckResult.pass_), _check("c2", None, C.CheckResult.pass_)]
        )
        blockers = harness.validate_validation(validation, spec)
        self.assertTrue(any("must 功能 f2" in blocker for blocker in blockers), blockers)

    def test_failed_check_is_blocker(self) -> None:
        validation = _validation(
            [
                _check("c1", "f1", C.CheckResult.fail, question="数据源可用吗？"),
                _check("c2", "f1", C.CheckResult.pass_),
            ]
        )
        blockers = harness.validate_validation(validation, _spec())
        self.assertTrue(any("结果为 fail" in blocker for blocker in blockers), blockers)

    def test_all_unknown_checks_is_blocker(self) -> None:
        validation = _validation([_check("c1", "f1", C.CheckResult.unknown)])
        blockers = harness.validate_validation(validation, _spec())
        self.assertTrue(any("全部为 unknown" in blocker for blocker in blockers), blockers)

    def test_reference_to_missing_feature_is_blocker(self) -> None:
        validation = _validation(
            [
                _check("c1", "f1", C.CheckResult.pass_),
                _check("c2", "f9", C.CheckResult.pass_),
            ]
        )
        blockers = harness.validate_validation(validation, _spec())
        self.assertTrue(any("不存在的功能：f9" in blocker for blocker in blockers), blockers)


# --------------------------------------------------------------------------- #
# 原型阶段校验
# --------------------------------------------------------------------------- #


class PrototypeValidationTests(unittest.TestCase):
    def test_entry_file_must_exist_in_files(self) -> None:
        prototype = C.PrototypeSpec(
            title="原型",
            summary="最小原型",
            entry_file="app.py",
            run_command="python app.py",
            files=[
                C.PrototypeFile(path="lib/helper.py", language="python", content="x = 1"),
            ],
        )
        blockers = harness.validate_prototype(prototype, _spec())
        self.assertTrue(any("入口文件不在 files 中" in blocker for blocker in blockers), blockers)

    def test_oversized_file_content_is_rejected_by_model(self) -> None:
        # 长度上限是模型层的第一道防线：pydantic 直接拒绝，harness 收不到该对象。
        # 因此这里断言模型层拒绝，而不是断言 harness blocker。
        with self.assertRaises(ValidationError):
            C.PrototypeFile(
                path="app.py",
                language="python",
                content="x" * (C.MAX_LOOP_FILE_CHARS + 1),
            )

    def test_empty_files_is_blocker(self) -> None:
        prototype = C.PrototypeSpec(
            title="原型",
            summary="最小原型",
            entry_file="app.py",
            run_command="python app.py",
            files=[],
        )
        blockers = harness.validate_prototype(prototype, _spec())
        self.assertTrue(any("files 为空" in blocker for blocker in blockers), blockers)

    def test_duplicate_paths_is_blocker(self) -> None:
        prototype = C.PrototypeSpec(
            title="原型",
            summary="最小原型",
            entry_file="app.py",
            run_command="python app.py",
            files=[
                C.PrototypeFile(path="app.py", language="python", content="x = 1"),
                C.PrototypeFile(path="app.py", language="python", content="x = 2"),
            ],
        )
        blockers = harness.validate_prototype(prototype, _spec())
        self.assertTrue(any("重复" in blocker for blocker in blockers), blockers)

    def test_valid_prototype_has_no_blockers(self) -> None:
        prototype = C.PrototypeSpec(
            title="原型",
            summary="最小原型",
            entry_file="app.py",
            run_command="python app.py",
            files=[C.PrototypeFile(path="app.py", language="python", content="print('hi')")],
        )
        self.assertEqual(harness.validate_prototype(prototype, _spec()), [])


# --------------------------------------------------------------------------- #
# 测试报告校验
# --------------------------------------------------------------------------- #


class TestReportValidationTests(unittest.TestCase):
    def test_must_feature_without_case_is_blocker(self) -> None:
        report = _report([_case("t1", "f1", C.TestCaseStatus.passed)])
        spec = _spec(
            features=[
                _feature("f1"),
                _feature("f2", acceptance="可验收：导出结果"),
            ]
        )
        blockers = harness.validate_test_report(report, spec)
        self.assertTrue(any("must 功能 f2" in blocker for blocker in blockers), blockers)

    def test_failed_case_is_blocker(self) -> None:
        report = _report(
            [_case("t1", "f1", C.TestCaseStatus.failed, expected="导出失败")]
        )
        blockers = harness.validate_test_report(report, _spec())
        self.assertTrue(any("状态为 failed" in blocker for blocker in blockers), blockers)

    def test_simulated_report_must_state_not_executed(self) -> None:
        report = _report([_case("t1", "f1", C.TestCaseStatus.passed)], notes="一切正常")
        blockers = harness.validate_test_report(report, _spec())
        self.assertTrue(any("未实际执行" in blocker for blocker in blockers), blockers)

    def test_valid_report_has_no_blockers(self) -> None:
        report = _report([_case("t1", "f1", C.TestCaseStatus.passed)])
        self.assertEqual(harness.validate_test_report(report, _spec()), [])


# --------------------------------------------------------------------------- #
# 评分硬规则
# --------------------------------------------------------------------------- #


class ClampScoreTests(unittest.TestCase):
    def test_blockers_cap_total_at_70(self) -> None:
        card = _card()  # total 100
        clamped = harness.clamp_score(
            card, blockers=["关键假设未验证"], p0=0, p1=0
        )
        self.assertEqual(clamped.total, 70)
        self.assertTrue(clamped.hard_capped)
        self.assertIn("关键假设未验证", clamped.blockers)
        self.assertEqual(clamped.generated_by, "harness")

    def test_p0_caps_total_at_70(self) -> None:
        card = _card(scores={"functionality": 10})
        clamped = harness.clamp_score(card, blockers=[], p0=1, p1=0)
        self.assertEqual(clamped.total, 70)
        self.assertTrue(clamped.hard_capped)

    def test_three_p1_cap_total_at_85(self) -> None:
        card = _card()
        clamped = harness.clamp_score(
            card, blockers=[], p0=0, p1=3
        )
        self.assertEqual(clamped.total, 85)
        self.assertTrue(clamped.hard_capped)

    def test_missing_dimensions_are_blockers_and_zero_score(self) -> None:
        only_functionality = C.LoopScoreCard(
            total=25,
            dimensions=[
                C.LoopScoreDimension(
                    key=C.LoopScoreKey.functionality, score=25, max=25, reason="功能完整"
                )
            ],
            verdict=C.LoopVerdict.pending,
            blockers=[],
            highlights=[],
            next_actions=[],
            generated_by="model",
            hard_capped=False,
        )
        clamped = harness.clamp_score(only_functionality, blockers=[], p0=0, p1=0)
        self.assertEqual(len(clamped.dimensions), len(C.LOOP_SCORE_MAX))
        self.assertEqual(clamped.total, 25)
        self.assertTrue(any("缺少维度" in blocker for blocker in clamped.blockers))
        self.assertTrue(clamped.hard_capped)

    def test_clean_card_keeps_total_and_dimension_order(self) -> None:
        clamped = harness.clamp_score(_card(), blockers=[], p0=0, p1=0)
        self.assertEqual(clamped.total, 100)
        self.assertFalse(clamped.hard_capped)
        self.assertEqual(clamped.blockers, [])
        keys = [dimension.key for dimension in clamped.dimensions]
        self.assertEqual(keys, list(C.LOOP_SCORE_MAX.keys()))


# --------------------------------------------------------------------------- #
# 结论推导
# --------------------------------------------------------------------------- #


class DeriveVerdictTests(unittest.TestCase):
    def test_reaching_target_without_blockers_passes(self) -> None:
        card = _card()
        verdict = harness.derive_verdict(
            card, target_score=85, round_ordinal=1, max_rounds=3
        )
        self.assertEqual(verdict, C.LoopVerdict.pass_)

    def test_exhausted_rounds_returns_iterate(self) -> None:
        card = _card(scores={"functionality": 10, "user_value": 5})
        verdict = harness.derive_verdict(
            card, target_score=85, round_ordinal=3, max_rounds=3
        )
        self.assertEqual(verdict, C.LoopVerdict.iterate)

    def test_three_blockers_block(self) -> None:
        card = _card(scores={"functionality": 10, "user_value": 5}, blockers=["a", "b", "c"])
        verdict = harness.derive_verdict(
            card, target_score=85, round_ordinal=1, max_rounds=3
        )
        self.assertEqual(verdict, C.LoopVerdict.blocked)

    def test_target_met_with_blocker_does_not_pass(self) -> None:
        card = _card(blockers=["关键假设未验证"])
        verdict = harness.derive_verdict(
            card, target_score=85, round_ordinal=1, max_rounds=5
        )
        self.assertEqual(verdict, C.LoopVerdict.iterate)


# --------------------------------------------------------------------------- #
# 覆盖矩阵与确定性
# --------------------------------------------------------------------------- #


class CoverageAndDeterminismTests(unittest.TestCase):
    def test_feature_coverage_matrix(self) -> None:
        spec = _spec(
            features=[
                _feature("f1"),
                _feature("f2", acceptance="可验收：导出结果"),
            ]
        )
        validation = _validation(
            [
                _check("c1", "f1", C.CheckResult.pass_),
                _check("c2", "f2", C.CheckResult.unknown),
            ]
        )
        report = _report(
            [
                _case("t1", "f1", C.TestCaseStatus.passed),
                _case("t2", "f2", C.TestCaseStatus.untested),
            ]
        )
        coverage = harness.feature_coverage(
            spec, validation=validation, test_report=report
        )
        self.assertEqual(set(coverage), {"f1", "f2"})
        self.assertTrue(coverage["f1"]["covered"])
        self.assertEqual(coverage["f1"]["validation"], "pass")
        self.assertEqual(coverage["f1"]["test"], "passed")
        self.assertFalse(coverage["f2"]["covered"])
        self.assertEqual(coverage["f2"]["validation"], "unknown")
        self.assertEqual(coverage["f2"]["test"], "untested")
        self.assertEqual(coverage["f1"]["priority"], "must")

    def test_harness_is_deterministic(self) -> None:
        spec = _spec()
        validation = _validation([_check("c1", "f1", C.CheckResult.unknown)])
        report = _report([_case("t1", "f1", C.TestCaseStatus.failed)])
        card = _card(blockers=["两个阻塞项"])

        first = [
            harness.validate_mvp_spec(spec),
            harness.validate_validation(validation, spec),
            harness.validate_prototype(
                C.PrototypeSpec(
                    title="原型",
                    summary="最小原型",
                    entry_file="app.py",
                    files=[C.PrototypeFile(path="app.py", content="print('hi')")],
                ),
                spec,
            ),
            harness.validate_test_report(report, spec),
            harness.clamp_score(card, blockers=["a", "b"], p0=1, p1=2),
            harness.feature_coverage(spec, validation=validation, test_report=report),
        ]
        second = copy_results = [
            harness.validate_mvp_spec(spec),
            harness.validate_validation(validation, spec),
            harness.validate_prototype(
                C.PrototypeSpec(
                    title="原型",
                    summary="最小原型",
                    entry_file="app.py",
                    files=[C.PrototypeFile(path="app.py", content="print('hi')")],
                ),
                spec,
            ),
            harness.validate_test_report(report, spec),
            harness.clamp_score(card, blockers=["a", "b"], p0=1, p1=2),
            harness.feature_coverage(spec, validation=validation, test_report=report),
        ]

        def _canonical(values) -> str:
            serializable = []
            for value in values:
                if hasattr(value, "model_dump"):
                    serializable.append(value.model_dump(mode="json"))
                else:
                    serializable.append(value)
            return C.canonical_json(serializable)

        self.assertEqual(_canonical(first), _canonical(second))


if __name__ == "__main__":
    unittest.main()
