"""确定性画布投影（FR-05 / ADR-06，M1 §4.3）。

画布是**产品状态的纯投影**，不是独立数据源（PRD ADR-06）：

* 无 I/O：不读数据库、不读时间、不读随机数、不读环境；
* 无副作用：不修改传入的 ``ProductState``；
* 同输入必同输出：节点 id 由 ``f"{node_kind}:{item.id}"`` 推导，分支顺序固定，
  列表顺序即状态内顺序，两次调用 ``model_dump_json()`` 逐字节相等；
* 每个节点都带 ``source_message_ids`` / ``evidence_refs`` / ``status``；既无来源消息
  又无证据引用的主张会被标记 ``model_speculation=True``（= 模型推测，不得当作事实）。

M1 不实现"待合并建议"暂存区（M2 由 reducer 提供）；``suggestions`` 参数预留给 M2，
M1 传入非 None 时直接抛 ``FEATURE_NOT_READY``，避免调用方误以为建议已被消费。
"""
from __future__ import annotations

from typing import Any, Sequence

from domain.contracts import (
    MAX_REFS_PER_ITEM,
    CanvasBranch,
    CanvasNode,
    CanvasProjection,
    Claim,
    ClaimKind,
    ProductState,
    UnresolvedIssue,
)
from domain.errors import AppError, ErrorCode
from services.number_guard import VERIFICATION_HINT

__all__ = [
    "BRANCH_TITLES",
    "MAX_UNVERIFIED_NUMBERS",
    "NODE_DETAIL_MAX",
    "NODE_TITLE_MAX",
    "branch_key_for_claim",
    "project_canvas",
]

#: 节点文本上限（与 ``CanvasNode`` 字段约束一致）。
NODE_TITLE_MAX = 300
NODE_DETAIL_MAX = 2000

#: 单个节点最多展示多少个「未验证数字」（与 ``CanvasNode`` 字段上限一致）。
MAX_UNVERIFIED_NUMBERS = MAX_REFS_PER_ITEM

#: 分支顺序（= 输出顺序，也是前端渲染顺序）；空分支不输出。
BRANCH_TITLES: tuple[tuple[str, str], ...] = (
    ("technical", "技术"),
    ("design", "设计"),
    ("business", "商业"),
    ("user", "用户"),
    ("option", "方案"),
    ("decision", "决策"),
    ("unresolved", "未决"),
    ("experiment", "实验"),
    ("claim", "其他主张"),
)

#: 角色 → 分支（技术/设计/商业/用户）。
ROLE_BRANCH: dict[str, str] = {
    "cto": "technical",
    "designer": "design",
    "ops": "business",
    "target_user": "user",
}

#: ``ClaimKind`` → ``CanvasNode.kind``。
CLAIM_NODE_KIND: dict[str, str] = {
    ClaimKind.fact.value: "claim",
    ClaimKind.hypothesis.value: "claim",
    ClaimKind.feature.value: "claim",
    ClaimKind.risk.value: "risk",
    ClaimKind.question.value: "question",
    ClaimKind.insight.value: "claim",
}

#: ``UnresolvedIssue.kind`` → ``CanvasNode.kind``。
ISSUE_NODE_KIND: dict[str, str] = {
    "risk": "risk",
    "assumption": "assumption",
    "question": "question",
    "conflict": "question",
    "gap": "question",
}

#: 没有角色归属、也不属于风险/问题时进入的兜底分支。
FALLBACK_BRANCH = "claim"

#: 根节点固定 id（确定性，不使用 uuid）。
ROOT_NODE_ID = "problem:brief"

#: ``brief`` 字段的中文标签（顺序 = 展示与摘要顺序）。
BRIEF_LABELS: tuple[tuple[str, str], ...] = (
    ("target_users", "目标用户"),
    ("usage_scenario", "使用场景"),
    ("core_problem", "核心问题"),
    ("current_alternatives", "现有替代方案"),
    ("product_form", "产品形态"),
    ("constraints", "硬约束"),
    ("success_criteria", "成功标准"),
)


# --------------------------------------------------------------------------- #
# 小工具（纯函数）
# --------------------------------------------------------------------------- #


def _clip(text: str | None, limit: int) -> str:
    """按字符截断并加省略号；``None`` 视为空串。"""
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _refs(values: Sequence[str] | None, limit: int = MAX_REFS_PER_ITEM) -> list[str]:
    """去重并保序，截到 ``limit``；不使用 set 以保持确定性顺序。"""
    if not values:
        return []
    return list(dict.fromkeys(values))[:limit]


def branch_key_for_claim(claim: Claim) -> str:
    """主张 → 分支：有角色归属按角色，风险/问题进未决，其余进兜底分支。"""
    if claim.role_code is not None:
        role = claim.role_code.value if hasattr(claim.role_code, "value") else str(claim.role_code)
        branch = ROLE_BRANCH.get(role)
        if branch is not None:
            return branch
    kind = claim.kind.value if hasattr(claim.kind, "value") else str(claim.kind)
    if kind in (ClaimKind.risk.value, ClaimKind.question.value):
        return "unresolved"
    return FALLBACK_BRANCH


def _issue_node_kind(issue: UnresolvedIssue) -> str:
    return ISSUE_NODE_KIND.get(str(issue.kind), "question")


def _status_value(item: Any, default: str = "proposed") -> str:
    status = getattr(item, "status", None)
    if status is None:
        return default
    return status.value if hasattr(status, "value") else str(status)


def _has_any_source(node: CanvasNode) -> bool:
    return bool(node.source_message_ids or node.evidence_refs)


# --------------------------------------------------------------------------- #
# 节点构造
# --------------------------------------------------------------------------- #


def _claim_node(claim: Claim) -> CanvasNode:
    kind = CLAIM_NODE_KIND.get(str(claim.kind), "claim")
    sources = _refs(claim.source_message_ids)
    evidence = _refs(claim.evidence_refs)
    unverified = [str(value) for value in claim.unverified_numbers][:MAX_UNVERIFIED_NUMBERS]
    node = CanvasNode(
        id=f"{kind}:{claim.id}",
        kind=kind,  # type: ignore[arg-type]
        title=_clip(claim.text, NODE_TITLE_MAX),
        detail=_claim_detail(claim),
        status=_status_value(claim),
        claim_ids=[claim.id],
        source_message_ids=sources,
        evidence_refs=evidence,
        unverified_numbers=unverified,
        verification_suggestion=_number_suggestion(unverified),
    )
    # 既无来源消息也无证据 → 模型推测（FR-05 AC：不得把无来源主张当事实展示）。
    node.model_speculation = not _has_any_source(node)
    return node


def _number_suggestion(unverified: list[str]) -> str | None:
    if not unverified:
        return None
    return "未验证数字：" + "、".join(unverified) + "。" + VERIFICATION_HINT


def _claim_detail(claim: Claim) -> str:
    parts = [claim.text]
    if claim.confidence_level:
        parts.append(f"置信度：{claim.confidence_level}")
    if claim.supersedes_id:
        parts.append(f"取代：{claim.supersedes_id}")
    if claim.role_code is not None:
        parts.append(f"提出角色：{_enum_value(claim.role_code)}")
    if claim.unverified_numbers:
        parts.append("未验证数字：" + "、".join(claim.unverified_numbers))
    if not (claim.source_message_ids or claim.evidence_refs):
        parts.append("来源：模型推测（无来源消息与证据）")
    return _clip("\n".join(parts), NODE_DETAIL_MAX)


def _option_node(option: Any) -> CanvasNode:
    detail: list[str] = []
    if option.summary:
        detail.append(option.summary)
    if option.tradeoffs:
        detail.append("权衡：" + "；".join(option.tradeoffs))
    if option.claim_ids:
        detail.append("支撑主张：" + "、".join(option.claim_ids))
    return CanvasNode(
        id=f"option:{option.id}",
        kind="option",
        title=_clip(option.title, NODE_TITLE_MAX),
        detail=_clip("\n".join(detail), NODE_DETAIL_MAX),
        status=_status_value(option),
        claim_ids=_refs(option.claim_ids),
        option_ids=[option.id],
        experiment_ids=_refs(option.experiment_ids),
    )


def _decision_node(decision: Any, option_titles: dict[str, str]) -> CanvasNode:
    detail: list[str] = []
    if decision.rationale:
        detail.append(decision.rationale)
    if decision.option_id:
        detail.append(f"对应方案：{option_titles.get(decision.option_id, decision.option_id)}")
    if decision.open_issue_ids:
        detail.append("仍有未决：" + "、".join(decision.open_issue_ids))
    if decision.needs_review:
        detail.append("需要复核")
    return CanvasNode(
        id=f"decision:{decision.id}",
        kind="decision",
        title=_clip(
            option_titles.get(decision.option_id or "", "") or f"决策 {decision.id}",
            NODE_TITLE_MAX,
        ),
        detail=_clip("\n".join(detail), NODE_DETAIL_MAX),
        status=_status_value(decision),
        option_ids=_refs([decision.option_id] if decision.option_id else []),
        decision_ids=[decision.id],
        evidence_refs=_refs(decision.evidence_refs),
    )


def _experiment_node(experiment: Any) -> CanvasNode:
    detail: list[str] = []
    if experiment.method:
        detail.append(experiment.method)
    if experiment.success_criterion:
        detail.append(f"成功标准：{experiment.success_criterion}")
    if experiment.needed_data:
        detail.append(f"所需数据：{experiment.needed_data}")
    return CanvasNode(
        id=f"experiment:{experiment.id}",
        kind="experiment",
        title=_clip(experiment.method or f"实验 {experiment.id}", NODE_TITLE_MAX),
        detail=_clip("\n".join(detail), NODE_DETAIL_MAX),
        status=_status_value(experiment),
        claim_ids=_refs([experiment.hypothesis_claim_id] if experiment.hypothesis_claim_id else []),
        experiment_ids=[experiment.id],
        evidence_refs=_refs(experiment.result_evidence_ids),
    )


def _issue_node(issue: UnresolvedIssue) -> CanvasNode:
    kind = _issue_node_kind(issue)
    detail: list[str] = [issue.text]
    if issue.dimension:
        detail.append(f"维度：{issue.dimension}")
    if issue.related_ids:
        detail.append("关联：" + "、".join(issue.related_ids))
    return CanvasNode(
        id=f"{kind}:{issue.id}",
        kind=kind,  # type: ignore[arg-type]
        title=_clip(issue.text, NODE_TITLE_MAX),
        detail=_clip("\n".join(detail), NODE_DETAIL_MAX),
        status=_status_value(issue, default="open"),
        # 未决事项的 related_ids 混合了 claim/option/issue 三类 id，无法在此安全分类，
        # 因此只保留在 detail 里展示，不冒充 claim_ids（避免画布出现悬空引用）。
        claim_ids=[],
    )


def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


# --------------------------------------------------------------------------- #
# 根节点（Brief 摘要）
# --------------------------------------------------------------------------- #


def _brief_summary_lines(state: ProductState) -> list[str]:
    lines: list[str] = []
    for field, label in BRIEF_LABELS:
        item = getattr(state.brief, field, None)
        if item is None or not item.value:
            continue
        lines.append(f"{label}：{_clip(item.value, 400)}")
    return lines


def _brief_source_ids(state: ProductState) -> list[str]:
    collected: list[str] = []
    for field, _label in BRIEF_LABELS:
        item = getattr(state.brief, field, None)
        if item is not None:
            collected.extend(item.source_message_ids or [])
    return _refs(collected)


def _root_node(state: ProductState) -> CanvasNode | None:
    """有任一定义内容才输出根节点；完全空状态返回 None。"""
    lines = _brief_summary_lines(state)
    if not lines:
        return None
    core_problem = state.brief.core_problem.value if state.brief.core_problem else None
    title = core_problem or "产品定义"
    detail_parts = [f"简报状态：{_enum_value(state.brief.status)}", *lines]
    if state.brief.skipped:
        detail_parts.append("用户已跳过澄清")
    if state.brief.required_missing():
        detail_parts.append("仍缺（必填）：" + "、".join(state.brief.required_missing()))
    node = CanvasNode(
        id=ROOT_NODE_ID,
        kind="problem",
        title=_clip(title, NODE_TITLE_MAX),
        detail=_clip("\n".join(detail_parts), NODE_DETAIL_MAX),
        status=_enum_value(state.brief.status),
        source_message_ids=_brief_source_ids(state),
        evidence_refs=[],
    )
    node.model_speculation = not _has_any_source(node)
    return node


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #


def project_canvas(
    product_state: ProductState,
    *,
    version: int | None = None,
    suggestions: Sequence[Any] | None = None,
) -> CanvasProjection:
    """把 ``ProductState`` 确定性投影为 ``CanvasProjection``（纯函数）。

    Args:
        product_state: 产品状态快照；函数不修改它。
        version: 可选的期望版本；传入时必须与 ``product_state.version`` 一致，
            否则抛 ``VERSION_CONFLICT``（避免用旧画布覆盖新状态）。
        suggestions: M2 的"待合并建议"预留给；M1 传入非 None 直接抛
            ``FEATURE_NOT_READY``。

    Returns:
        ``CanvasProjection``：``product_version`` 恒等于 ``product_state.version``。
    """
    if product_state is None:
        raise AppError(ErrorCode.INVALID_REQUEST, "缺少产品状态，无法投影画布")
    if suggestions is not None:
        raise AppError(
            ErrorCode.FEATURE_NOT_READY,
            "M1 不提供待合并建议的投影（M2 由 reducer 提供）",
        )
    expected = product_state.version
    if version is not None and int(version) != int(expected):
        raise AppError(
            ErrorCode.VERSION_CONFLICT,
            "画布投影版本与产品状态版本不一致",
            details={"requested_version": int(version), "state_version": int(expected)},
        )

    buckets: dict[str, list[CanvasNode]] = {key: [] for key, _title in BRANCH_TITLES}
    option_titles: dict[str, str] = {
        option.id: option.title for option in product_state.options
    }

    for claim in product_state.claims:
        buckets[branch_key_for_claim(claim)].append(_claim_node(claim))
    for option in product_state.options:
        buckets["option"].append(_option_node(option))
    for decision in product_state.decisions:
        buckets["decision"].append(_decision_node(decision, option_titles))
    for experiment in product_state.experiments:
        buckets["experiment"].append(_experiment_node(experiment))
    for issue in product_state.unresolved_issues:
        buckets["unresolved"].append(_issue_node(issue))

    branches = [
        CanvasBranch(key=key, title=title, nodes=buckets[key])
        for key, title in BRANCH_TITLES
        if buckets[key]
    ]

    return CanvasProjection(
        session_id=product_state.session_id,
        product_version=int(product_state.version),
        root=_root_node(product_state),
        branches=branches,
    )
