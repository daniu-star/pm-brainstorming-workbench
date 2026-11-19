"""Loop Studio 确定性校验与评分（对应契约 §5.1）。

本模块**只做纯计算**：不读数据库、不调用模型、不依赖时间或随机数。
同样的输入必须得到逐字节相同的结果（测试会断言两次调用 ``canonical_json`` 相等），
因此所有返回值只由入参决定，阻塞项按固定顺序生成、按固定顺序去重截断。

公开函数（签名固定，勿改）：

* :func:`validate_mvp_spec` —— ideate 阶段产物的确定性校验；
* :func:`validate_validation` —— validate 阶段产物校验（对照 MVP 规格）；
* :func:`validate_prototype` —— develop 阶段产物校验（入口文件必须存在）；
* :func:`validate_test_report` —— test 阶段产物校验（must 功能必须有已执行用例）；
* :func:`clamp_score` —— 用硬规则覆盖模型自评分（blockers / P0 / P1）；
* :func:`derive_verdict` —— 由总分、阻塞项、轮次与阈值推导本轮结论；
* :func:`feature_coverage` —— must 功能在验证/测试两个阶段的覆盖矩阵。
"""
from __future__ import annotations

from typing import Any

from domain import contracts as C

__all__ = [
    "validate_mvp_spec",
    "validate_validation",
    "validate_prototype",
    "validate_test_report",
    "clamp_score",
    "derive_verdict",
    "feature_coverage",
    "score_max_for",
]

#: 单个阻塞项的展示上限（避免把整段模型输出塞进事件负载）。
_BLOCKER_CHARS = 300

#: 引用文字上限（只用来把模型原文摘进阻塞项）。
_QUOTE_CHARS = 120

#: 评分维度顺序与满分表（顺序即 ``clamp_score`` 输出的顺序）。
SCORE_MAX: dict[str, int] = {
    str(key.value if isinstance(key, C.LoopScoreKey) else key): int(value)
    for key, value in C.LOOP_SCORE_MAX.items()
}


# --------------------------------------------------------------------------- #
# 内部辅助
# --------------------------------------------------------------------------- #


def score_max_for(key: str) -> int | None:
    """返回评分维度满分；未知维度返回 ``None``。"""

    return SCORE_MAX.get(str(key))


def _clip(text: Any, limit: int) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def _dedupe(items: list[str]) -> list[str]:
    """按出现顺序去重，单项截断到 :data:`_BLOCKER_CHARS`。"""

    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        text = _clip(item, _BLOCKER_CHARS)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _cap(items: list[str]) -> list[str]:
    return _dedupe(items)[: C.MAX_LOOP_BLOCKERS]


def _feature_ids(spec: C.LoopMvpSpec) -> set[str]:
    return {str(feature.id) for feature in list(spec.features or [])}


def _must_features(spec: C.LoopMvpSpec) -> list[C.MvpFeature]:
    return [
        feature
        for feature in list(spec.features or [])
        if feature.priority == C.FeaturePriority.must
    ]


def _dimension_key(dimension: C.LoopScoreDimension) -> str:
    key = dimension.key
    return str(key.value if isinstance(key, C.LoopScoreKey) else key)


# --------------------------------------------------------------------------- #
# 阶段产物校验
# --------------------------------------------------------------------------- #


def validate_mvp_spec(spec: C.LoopMvpSpec) -> list[str]:
    """校验 ideate 产出的 MVP 规格；返回阻塞项（空列表表示通过）。"""

    blockers: list[str] = []
    features = list(spec.features or [])
    if not features:
        blockers.append("MVP 规格缺少功能（features 为空），无法界定最小可交付范围")
    if len(features) > C.MAX_LOOP_FEATURES:
        blockers.append(f"MVP 功能数超过上限 {C.MAX_LOOP_FEATURES}：当前 {len(features)}")

    seen: set[str] = set()
    for index, feature in enumerate(features, start=1):
        fid = str(feature.id or "").strip()
        if not fid:
            blockers.append(f"第 {index} 个功能缺少 id")
        elif fid in seen:
            blockers.append(f"功能 id 重复：{fid}")
        else:
            seen.add(fid)
        label = fid or f"#{index}"
        if not str(feature.title or "").strip():
            blockers.append(f"功能 {label} 缺少标题（title）")
        if not str(feature.description or "").strip():
            blockers.append(f"功能 {label} 缺少描述（description）")
        if not str(feature.acceptance or "").strip():
            blockers.append(f"功能 {label} 缺少验收标准（acceptance）")

    if features and not _must_features(spec):
        blockers.append("MVP 规格没有任何 must 优先级功能，无法界定最小交付范围")

    assumptions = list(spec.assumptions or [])
    if len(assumptions) > C.MAX_LOOP_ASSUMPTIONS:
        blockers.append(
            f"假设数超过上限 {C.MAX_LOOP_ASSUMPTIONS}：当前 {len(assumptions)}"
        )
    assumption_ids: set[str] = set()
    for assumption in assumptions:
        aid = str(assumption.id or "").strip()
        if not aid:
            blockers.append("存在缺少 id 的假设")
        elif aid in assumption_ids:
            blockers.append(f"假设 id 重复：{aid}")
        else:
            assumption_ids.add(aid)

    if not str(spec.summary or "").strip():
        blockers.append("MVP 规格缺少摘要（summary）")

    return _cap(blockers)


def validate_validation(
    validation: C.LoopValidation, spec: C.LoopMvpSpec
) -> list[str]:
    """校验 validate 产出；must 功能必须各有检查，且不能全部 unknown。"""

    blockers: list[str] = []
    checks = list(validation.checks or [])
    if not checks:
        blockers.append("验证阶段没有任何检查项（checks 为空），无法支撑结论")
    if len(checks) > C.MAX_LOOP_CHECKS:
        blockers.append(f"检查项超过上限 {C.MAX_LOOP_CHECKS}：当前 {len(checks)}")

    spec_ids = _feature_ids(spec)
    check_ids: set[str] = set()
    for index, check in enumerate(checks, start=1):
        cid = str(check.id or "").strip()
        if not cid:
            blockers.append(f"第 {index} 个检查缺少 id")
        elif cid in check_ids:
            blockers.append(f"检查 id 重复：{cid}")
        else:
            check_ids.add(cid)
        label = cid or f"#{index}"
        feature_id = str(check.feature_id or "").strip()
        if feature_id and spec_ids and feature_id not in spec_ids:
            blockers.append(f"检查 {label} 引用了不存在的功能：{feature_id}")
        if check.result == C.CheckResult.fail:
            blockers.append(f"检查 {label} 结果为 fail：{_clip(check.question, _QUOTE_CHARS)}")

    for feature in _must_features(spec):
        related = [
            check
            for check in checks
            if str(check.feature_id or "").strip() == str(feature.id)
        ]
        if not related:
            blockers.append(f"must 功能 {feature.id} 缺少任何验证检查")
            continue
        if all(check.result == C.CheckResult.unknown for check in related):
            blockers.append(
                f"must 功能 {feature.id} 的验证结果全部为 unknown，结论不可用"
            )

    if not str(validation.summary or "").strip():
        blockers.append("验证阶段缺少总结（summary）")

    return _cap(blockers)


def validate_prototype(
    prototype: C.PrototypeSpec, spec: C.LoopMvpSpec
) -> list[str]:
    """校验 develop 产出；入口文件必须出现在 files 中，文件大小受硬上限约束。"""

    del spec  # 原型与规格的对应关系由 feature_coverage / evaluate 阶段负责
    blockers: list[str] = []

    files = list(prototype.files or [])
    if not files:
        blockers.append("原型没有任何文件（files 为空）")
    if len(files) > C.MAX_LOOP_FILES:
        blockers.append(f"原型文件数超过上限 {C.MAX_LOOP_FILES}：当前 {len(files)}")

    paths: set[str] = set()
    for index, proto_file in enumerate(files, start=1):
        path = str(proto_file.path or "").strip()
        if not path:
            blockers.append(f"第 {index} 个原型文件缺少 path")
            label = f"#{index}"
        else:
            label = path
            if path in paths:
                blockers.append(f"原型文件路径重复：{path}")
            else:
                paths.add(path)
        content = str(proto_file.content or "")
        if not content.strip():
            blockers.append(f"原型文件内容为空：{label}")
        if len(content) > C.MAX_LOOP_FILE_CHARS:
            blockers.append(
                f"原型文件超过 {C.MAX_LOOP_FILE_CHARS} 字符上限：{label}"
                f"（当前 {len(content)} 字符）"
            )

    entry_file = str(prototype.entry_file or "").strip()
    if not entry_file:
        blockers.append("原型缺少入口文件（entry_file）")
    elif entry_file not in paths:
        blockers.append(f"入口文件不在 files 中：{entry_file}")

    if not str(prototype.summary or "").strip():
        blockers.append("原型缺少摘要（summary）")

    return _cap(blockers)


def validate_test_report(
    report: C.LoopTestReport, spec: C.LoopMvpSpec
) -> list[str]:
    """校验 test 产出；must 功能必须有已执行（非 untested）用例。"""

    blockers: list[str] = []
    cases = list(report.cases or [])
    if not cases:
        blockers.append("测试报告没有任何用例（cases 为空）")
    if len(cases) > C.MAX_LOOP_CASES:
        blockers.append(f"测试用例超过上限 {C.MAX_LOOP_CASES}：当前 {len(cases)}")

    spec_ids = _feature_ids(spec)
    case_ids: set[str] = set()
    for index, case in enumerate(cases, start=1):
        cid = str(case.id or "").strip()
        if not cid:
            blockers.append(f"第 {index} 个测试用例缺少 id")
        elif cid in case_ids:
            blockers.append(f"测试用例 id 重复：{cid}")
        else:
            case_ids.add(cid)
        label = cid or f"#{index}"
        feature_id = str(case.feature_id or "").strip()
        if feature_id and spec_ids and feature_id not in spec_ids:
            blockers.append(f"测试用例 {label} 引用了不存在的功能：{feature_id}")
        if case.status == C.TestCaseStatus.failed:
            blockers.append(
                f"测试用例 {label} 状态为 failed：{_clip(case.expected, _QUOTE_CHARS)}"
            )

    for feature in _must_features(spec):
        related = [
            case
            for case in cases
            if str(case.feature_id or "").strip() == str(feature.id)
        ]
        if not related:
            blockers.append(f"must 功能 {feature.id} 没有任何测试用例")
            continue
        if all(case.status == C.TestCaseStatus.untested for case in related):
            blockers.append(
                f"must 功能 {feature.id} 的测试用例全部未执行（untested），结果不可用"
            )

    if report.simulated and "未实际执行" not in str(report.notes or ""):
        blockers.append("测试报告声明 simulated=True，但 notes 未注明「未实际执行」")

    if int(report.failed or 0) > 0 and not any(
        case.status == C.TestCaseStatus.failed for case in cases
    ):
        blockers.append("测试报告 failed 计数大于 0，但没有状态为 failed 的用例")

    return _cap(blockers)


# --------------------------------------------------------------------------- #
# 评分硬规则
# --------------------------------------------------------------------------- #


def clamp_score(
    card: C.LoopScoreCard,
    *,
    blockers: list[str],
    p0: int,
    p1: int,
) -> C.LoopScoreCard:
    """用硬规则覆盖模型自评分（contract §5.1）。

    规则：

    * ``blockers`` 非空或存在 P0（``p0 >= 1``）→ ``total = min(total, 70)``；
    * P1 数量 ``p1 >= 3`` → ``total = min(total, 85)``；
    * 触发任一规则时 ``hard_capped=True``；
    * ``total`` 一律重算为各维度分数之和，维度 key 必须属于
      :data:`domain.contracts.LOOP_SCORE_MAX`，``max`` 取表值；缺失维度按 0 分计入，
      并生成一条阻塞项「缺少维度」。
    """

    supplied = [str(item) for item in (blockers or [])]
    provided: dict[str, tuple[int, str]] = {}
    generated: list[str] = []

    for dimension in list(card.dimensions or []):
        key = _dimension_key(dimension)
        if key not in SCORE_MAX:
            generated.append(f"未知评分维度：{key}")
            continue
        if key in provided:
            generated.append(f"重复评分维度：{key}")
            continue
        score = max(0, min(int(dimension.score), SCORE_MAX[key]))
        provided[key] = (score, str(dimension.reason or ""))

    rebuilt: list[C.LoopScoreDimension] = []
    for key, max_score in SCORE_MAX.items():
        score, reason = provided.get(key, (0, ""))
        rebuilt.append(
            C.LoopScoreDimension(
                key=C.LoopScoreKey(key), score=score, max=max_score, reason=reason
            )
        )
        if key not in provided:
            generated.append(
                f"缺少维度：{C.LOOP_SCORE_LABELS.get(C.LoopScoreKey(key), key)}"
            )

    merged = _cap([*supplied, *generated])
    total = sum(dimension.score for dimension in rebuilt)

    hard_capped = False
    if merged or int(p0) >= 1:
        total = min(total, 70)
        hard_capped = True
    if int(p1) >= 3:
        total = min(total, 85)
        hard_capped = True

    return card.model_copy(
        update={
            "total": total,
            "dimensions": rebuilt,
            "blockers": merged,
            "hard_capped": hard_capped,
            "generated_by": "harness",
        }
    )


def derive_verdict(
    card: C.LoopScoreCard,
    *,
    target_score: int,
    round_ordinal: int,
    max_rounds: int,
) -> C.LoopVerdict:
    """由硬规则推导本轮结论（contract §5.1）。

    * 达到目标分且没有阻塞项 → ``pass``；
    * 轮次已用尽（``round_ordinal >= max_rounds``）→ ``iterate``（是否继续交用户决定）；
    * 阻塞项达到 3 条及以上 → ``blocked``；
    * 其余情况 → ``iterate``。
    """

    total = int(card.total)
    blockers = list(card.blockers or [])
    if total >= int(target_score) and not blockers:
        return C.LoopVerdict.pass_
    if int(round_ordinal) >= int(max_rounds):
        return C.LoopVerdict.iterate
    if len(blockers) >= 3:
        return C.LoopVerdict.blocked
    return C.LoopVerdict.iterate


# --------------------------------------------------------------------------- #
# 覆盖矩阵
# --------------------------------------------------------------------------- #


def feature_coverage(
    spec: C.LoopMvpSpec,
    *,
    validation: C.LoopValidation | None = None,
    test_report: C.LoopTestReport | None = None,
) -> dict[str, dict]:
    """返回每个功能在验证/测试两阶段的覆盖情况（确定性字典）。"""

    checks = list(validation.checks or []) if validation is not None else []
    cases = list(test_report.cases or []) if test_report is not None else []

    coverage: dict[str, dict] = {}
    for feature in list(spec.features or []):
        feature_id = str(feature.id)
        related_checks = [
            check
            for check in checks
            if str(check.feature_id or "").strip() == feature_id
        ]
        if not related_checks:
            validation_state = "missing"
        elif any(check.result == C.CheckResult.fail for check in related_checks):
            validation_state = "fail"
        elif all(check.result == C.CheckResult.unknown for check in related_checks):
            validation_state = "unknown"
        else:
            validation_state = "pass"

        related_cases = [
            case
            for case in cases
            if str(case.feature_id or "").strip() == feature_id
        ]
        if not related_cases:
            test_state = "missing"
        elif any(case.status == C.TestCaseStatus.failed for case in related_cases):
            test_state = "failed"
        elif any(case.status == C.TestCaseStatus.untested for case in related_cases):
            test_state = "untested"
        elif any(case.status == C.TestCaseStatus.passed for case in related_cases):
            test_state = "passed"
        else:
            test_state = "skipped"

        validated = validation_state == "pass"
        tested = test_state == "passed"
        coverage[feature_id] = {
            "title": str(feature.title),
            "priority": feature.priority.value,
            "validation": validation_state,
            "test": test_state,
            "validated": validated,
            "tested": tested,
            "covered": validated and tested,
        }

    return coverage
