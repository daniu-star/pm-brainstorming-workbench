"""FR-04B 角色选择 + FR-04C 确定性归并（M2 / task-1）。

两个入口都是**纯函数**：

* :func:`select_roles` —— 智能分配规则：只读 ``content`` 与 ``product_state``，
  不读时间/随机数/网络，同样的输入永远得到同样的角色列表（PRD FR-04B：
  "规则与选择原因可测试"）。
* :func:`merge_role_results` —— 把各角色的 :class:`RoleResult` 归并进产品状态。
  归并顺序只取决于固定 ``ROLE_ORDER``、步骤 ``logical_key`` 与候选 ``local_id``，
  **与结果到达顺序无关**；服务器 ID 由调用方注入的 ``id_factory`` 决定（默认
  ``domain.contracts.new_id``）。函数不推进 ``ProductState.version`` —— 版本号
  只能由调用方（workflow）在提交前递增。

设计约束（PRD FR-04C）：

* 候选的 ``local_id`` 只在本角色内唯一；跨角色的服务器 ID 不能冲突 ⇒ 一律由
  ``id_factory`` 分配，归并层不自己造 ID。
* 完全相同的标准化文本视为同一条主张/方案/实验/未决事项：保留排序在前的那条，
  来源引用取并集（每项上限 ``MAX_REFS_PER_ITEM``）；近义但不同的文本不合并。
* 越权/无效的 ``existing_*_ids`` 不会进入产品状态（由 workflow 在归并前校验；
  这里再做一层防御性过滤，保证纯函数永不产出悬空引用）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Sequence

from domain.contracts import (
    MAX_REFS_PER_ITEM,
    Claim,
    ClaimStatus,
    ExperimentItem,
    ExperimentStatus,
    IssueStatus,
    OptionItem,
    OriginKind,
    ProductState,
    RoleCode,
    RoleResult,
    TargetRole,
    UnresolvedIssue,
    new_id,
)
from domain.errors import AppError, ErrorCode

# --------------------------------------------------------------------------- #
# 固定顺序与常量
# --------------------------------------------------------------------------- #

#: 四角色的固定顺序（PRD FR-04B/04C）。排序、展示、消息顺序都以它为锚点。
ROLE_ORDER: tuple[RoleCode, ...] = (
    RoleCode.cto,
    RoleCode.designer,
    RoleCode.ops,
    RoleCode.target_user,
)

#: 角色 → 步骤 logical_key（与 ``workflows.brainstorm`` 保持一致）。
def role_logical_key(role_code: RoleCode | str) -> str:
    return f"brainstorm.role.{_role_value(role_code)}"


#: 智能分配关键词表：命中即计 1 个"类别"（同一关键词重复出现只计一次）。
ROLE_KEYWORD_RULES: dict[RoleCode, tuple[str, ...]] = {
    RoleCode.cto: (
        "技术",
        "架构",
        "数据",
        "性能",
        "安全",
        "工程",
        "接口",
        "集成",
        "算法",
        "扩展",
        "并发",
        "研发",
        "依赖",
        "部署",
        "architecture",
        "technical",
        "performance",
        "security",
        "scalability",
        "infrastructure",
        "api",
    ),
    RoleCode.designer: (
        "流程",
        "交互",
        "体验",
        "易用",
        "无障碍",
        "界面",
        "信息架构",
        "认知",
        "设计",
        "原型",
        "视觉",
        "引导",
        "空态",
        "设计稿",
        "design",
        "interaction",
        "usability",
        "accessibility",
        "ux",
        "ui",
        "prototype",
    ),
    RoleCode.ops: (
        "商业",
        "定价",
        "收入",
        "增长",
        "获客",
        "渠道",
        "市场",
        "变现",
        "成本",
        "指标",
        "运营",
        "竞争",
        "预算",
        "推广",
        "business",
        "pricing",
        "revenue",
        "growth",
        "acquisition",
        "market",
        "monetization",
        "gtm",
    ),
    RoleCode.target_user: (
        "需求",
        "用户",
        "痛点",
        "采用",
        "替代",
        "习惯",
        "切换",
        "动机",
        "场景",
        "访谈",
        "留存",
        "抵触",
        "困惑",
        "adoption",
        "user",
        "pain",
        "alternative",
        "habit",
        "retention",
        "switch",
    ),
}

#: 智能分配最多选择三个角色（PRD FR-04B："选 1–3 个角色"）。
MAX_AUTO_ROLES = 3

#: 无任何命中时的兜底角色：先看用户价值与可用性，再谈技术与商业。
DEFAULT_ROLES: tuple[RoleCode, ...] = (RoleCode.designer, RoleCode.target_user)

#: 特定角色的 target 取值（其余取值交给 :class:`TargetRole`）。
_SPECIFIC_ROLES: dict[str, RoleCode] = {role.value: role for role in ROLE_ORDER}

#: 兼容旧前端 ``user`` 写法。
_TARGET_ALIASES: dict[str, str] = {
    "user": TargetRole.target_user.value,
    "target-user": TargetRole.target_user.value,
    "targetuser": TargetRole.target_user.value,
    "smart": TargetRole.auto.value,
    "auto_pick": TargetRole.auto.value,
}


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #


def _role_value(role_code: RoleCode | str) -> str:
    return role_code.value if isinstance(role_code, RoleCode) else str(role_code)


def _normalize_target(target: Any) -> TargetRole:
    """把 ``None`` / 字符串 / ``TargetRole`` 归一成 :class:`TargetRole`。"""

    if target is None:
        return TargetRole.auto
    if isinstance(target, TargetRole):
        return target
    text = str(target).strip().lower()
    if not text:
        return TargetRole.auto
    text = _TARGET_ALIASES.get(text, text)
    try:
        return TargetRole(text)
    except ValueError as exc:
        raise AppError(
            ErrorCode.INVALID_REQUEST,
            f"未知的角色分配目标：{target}",
            details={"target": str(target)},
        ) from exc


def _norm_text(text: str | None) -> str:
    """标准化文本：去首尾空白、折叠内部空白、忽略大小写（用于判重）。"""

    return " ".join(str(text or "").split()).casefold()


def _keyword_hits(text: str, keywords: Sequence[str]) -> int:
    """命中的**不同**关键词个数（同一关键词重复出现只算一次）。"""

    haystack = str(text or "").casefold()
    return sum(1 for word in keywords if word.casefold() in haystack)


def _merge_refs(
    base: Iterable[str] | None, extra: Iterable[str] | None, *, limit: int = MAX_REFS_PER_ITEM
) -> list[str]:
    """按稳定顺序合并引用并去重（保留 ``base`` 在前），超出上限截断。"""

    seen: dict[str, None] = {}
    for ref in list(base or []) + list(extra or []):
        ref = str(ref).strip()
        if ref and ref not in seen:
            seen[ref] = None
        if len(seen) >= limit:
            break
    return list(seen)


def _sorted_by_local_id(items: Sequence[Any]) -> list[Any]:
    return sorted(items, key=lambda item: str(getattr(item, "local_id", "")))


# --------------------------------------------------------------------------- #
# FR-04B：角色选择
# --------------------------------------------------------------------------- #


def _issue_role(issue: Any) -> RoleCode | None:
    """把一条未决事项归到最相关的角色；无命中返回 ``None``。"""

    text = f"{getattr(issue, 'dimension', '') or ''} {getattr(issue, 'text', '') or ''} {getattr(issue, 'kind', '') or ''}"
    best_role: RoleCode | None = None
    best_score = 0
    for role in ROLE_ORDER:
        score = _keyword_hits(text, ROLE_KEYWORD_RULES[role])
        if score > best_score:
            best_role, best_score = role, score
    return best_role


def select_roles(
    target: Any,
    content: str,
    product_state: ProductState | None = None,
) -> list[RoleCode]:
    """FR-04B 确定性角色选择：返回按 :data:`ROLE_ORDER` 排序的角色列表。

    * ``target`` 是具体角色 → 只跑该角色；
    * ``target == all`` → 四角色全跑；
    * ``target == auto`` / ``None`` → 按议题关键词与未决事项加权选择 1–3 个角色：
      命中类别更多的角色优先，平分按 ``ROLE_ORDER``；全无命中时走
      :data:`DEFAULT_ROLES`（设计 + 目标用户）。
    """

    resolved = _normalize_target(target)

    if resolved is TargetRole.all:
        return list(ROLE_ORDER)
    if resolved.value in _SPECIFIC_ROLES:
        return [_SPECIFIC_ROLES[resolved.value]]

    scores: dict[RoleCode, int] = {role: 0 for role in ROLE_ORDER}
    for role in ROLE_ORDER:
        scores[role] += _keyword_hits(content or "", ROLE_KEYWORD_RULES[role])

    if product_state is not None:
        for issue in getattr(product_state, "unresolved_issues", []) or []:
            if getattr(issue, "status", IssueStatus.open) != IssueStatus.open:
                continue
            role = _issue_role(issue)
            if role is not None:
                scores[role] += 1

    ranked = sorted(
        (role for role in ROLE_ORDER if scores[role] > 0),
        key=lambda role: (-scores[role], ROLE_ORDER.index(role)),
    )
    if not ranked:
        return list(DEFAULT_ROLES)
    return ranked[:MAX_AUTO_ROLES]


# --------------------------------------------------------------------------- #
# FR-04C：确定性归并
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RoleOutput:
    """一条待归并的角色产物（role + 步骤 logical_key + 结构化结果）。"""

    role_code: RoleCode
    logical_key: str
    result: RoleResult


#: ``id_factory`` 契约：``(kind, local_id, role_code) -> 服务器 ID``。
IdFactory = Callable[[str, str, str], str]


def _default_id_factory(kind: str, local_id: str, role_code: str) -> str:  # noqa: ARG001
    return new_id()


def _normalize_role_output(item: Any) -> RoleOutput:
    """接受多种写法，统一成 :class:`RoleOutput`，便于测试与 workflow 共用。"""

    if isinstance(item, RoleOutput):
        return item
    if isinstance(item, RoleResult):
        return RoleOutput(
            role_code=RoleCode(item.role_code),
            logical_key=role_logical_key(item.role_code),
            result=item,
        )
    if isinstance(item, tuple):
        if len(item) == 2:
            role, result = item
            return RoleOutput(RoleCode(_role_value(role)), role_logical_key(role), RoleResult.model_validate(result))
        if len(item) == 3:
            role, logical_key, result = item
            return RoleOutput(
                RoleCode(_role_value(role)),
                str(logical_key),
                RoleResult.model_validate(result),
            )
    # 鸭子类型：workflow 内部的完成记录 / 测试桩
    result = getattr(item, "result", None)
    if result is not None:
        role = getattr(item, "role_code", getattr(result, "role_code", None))
        logical_key = getattr(item, "logical_key", None) or role_logical_key(role)
        return RoleOutput(
            RoleCode(_role_value(role)),
            str(logical_key),
            result if isinstance(result, RoleResult) else RoleResult.model_validate(result),
        )
    raise AppError(ErrorCode.INVALID_REQUEST, f"无法识别的角色产物：{type(item)!r}")


def _ordered_outputs(results: Iterable[Any]) -> list[RoleOutput]:
    """按 (固定 role_order, logical_key, 角色内 local_id) 排序，与到达顺序无关。"""

    normalized = [_normalize_role_output(item) for item in results or []]
    normalized.sort(
        key=lambda output: (
            ROLE_ORDER.index(output.role_code),
            output.logical_key,
            _sorted_by_local_id(output.result.claims)[0].local_id if output.result.claims else "",
        )
    )
    return normalized


def merge_role_results(
    state: ProductState,
    results: Iterable[Any],
    *,
    run_id: str,
    step_ids: dict[str, str] | None = None,
    id_factory: IdFactory | None = None,
) -> ProductState:
    """把角色产物确定性地归并进 ``state``（**不推进 version**）。

    ``step_ids`` 是 ``logical_key → step_id`` 的只读映射，仅用于校验/审计；
    缺省时按 :func:`role_logical_key` 推导。``id_factory`` 决定服务器 ID，
    默认 ``new_id()``，测试与 workflow 会注入确定性实现（workflow 注入的是
    ``run_repo.reserve_candidate_id`` 已登记的 staging ID）。
    """

    if state is None or not isinstance(state, ProductState):
        raise AppError(ErrorCode.INVALID_REQUEST, "缺少产品状态，无法归并角色产物")
    factory = id_factory or _default_id_factory
    logical_keys = dict(step_ids or {})
    outputs = _ordered_outputs(results)

    claims = [claim.model_copy(deep=True) for claim in state.claims]
    options = [option.model_copy(deep=True) for option in state.options]
    experiments = [experiment.model_copy(deep=True) for experiment in state.experiments]
    issues = [issue.model_copy(deep=True) for issue in state.unresolved_issues]

    claim_by_key: dict[tuple[str, str], Claim] = {}
    for claim in claims:
        claim_by_key.setdefault(_claim_key(claim.kind, claim.text), claim)
    option_by_key: dict[str, OptionItem] = {_norm_text(option.title): option for option in options}
    experiment_by_key: dict[tuple[str, str], ExperimentItem] = {
        (_norm_text(experiment.method), _norm_text(experiment.success_criterion)): experiment
        for experiment in experiments
    }
    issue_by_key: dict[tuple[str, str], UnresolvedIssue] = {
        (str(issue.kind), _norm_text(issue.text)): issue for issue in issues
    }

    for output in outputs:
        role_code = output.role_code
        result = output.result
        logical_key = logical_keys.get(output.logical_key, output.logical_key)

        # 1) 主张：local_id → 服务器 ID（同角色内唯一）
        claim_ids: dict[str, str] = {}
        for candidate in _sorted_by_local_id(result.claims):
            key = _claim_key(candidate.kind, candidate.text)
            existing = claim_by_key.get(key)
            if existing is not None:
                existing.source_message_ids = _merge_refs(
                    existing.source_message_ids, candidate.source_message_ids
                )
                existing.evidence_refs = _merge_refs(
                    existing.evidence_refs, candidate.evidence_refs
                )
                claim_ids[candidate.local_id] = existing.id
                continue
            claim = Claim(
                id=factory("claim", candidate.local_id, role_code.value),
                kind=candidate.kind,
                text=candidate.text,
                source_message_ids=_merge_refs([], candidate.source_message_ids),
                evidence_refs=_merge_refs([], candidate.evidence_refs),
                origin=OriginKind.model,
                status=ClaimStatus.proposed,
                created_product_version=state.version + 1,
                confidence_level=str(candidate.confidence_level),
                role_code=role_code,
            )
            claims.append(claim)
            claim_by_key[key] = claim
            claim_ids[candidate.local_id] = claim.id

        # 2) 实验：先建，供方案引用
        experiment_ids: dict[str, str] = {}
        for candidate in _sorted_by_local_id(result.suggested_experiments):
            key = (_norm_text(candidate.method), _norm_text(candidate.success_criterion))
            existing_experiment = experiment_by_key.get(key)
            if existing_experiment is not None:
                experiment_ids[candidate.local_id] = existing_experiment.id
                continue
            experiment = ExperimentItem(
                id=factory("experiment", candidate.local_id, role_code.value),
                hypothesis_claim_id=claim_ids.get(candidate.hypothesis_local_id or ""),
                method=candidate.method,
                success_criterion=candidate.success_criterion,
                needed_data=candidate.needed_data,
                status=ExperimentStatus.proposed,
                result_evidence_ids=[],
                origin=OriginKind.model,
            )
            experiments.append(experiment)
            experiment_by_key[key] = experiment
            experiment_ids[candidate.local_id] = experiment.id

        # 3) 方案提案
        existing_claim_ids = set(state.claim_ids()) | set(claim_ids.values())
        existing_issue_ids = {issue.id for issue in state.unresolved_issues}
        for candidate in _sorted_by_local_id(result.option_proposals):
            option_claim_ids = _merge_refs(
                [], [claim_ids[ref] for ref in candidate.claim_local_ids if ref in claim_ids]
            )
            risk_ids = _merge_refs(
                [], [claim_ids[ref] for ref in candidate.risk_local_ids if ref in claim_ids]
            )
            assumption_ids = _merge_refs(
                [],
                [claim_ids[ref] for ref in candidate.assumption_local_ids if ref in claim_ids],
            )
            experiments_for_option = _merge_refs(
                [],
                [experiment_ids[ref] for ref in candidate.experiment_local_ids if ref in experiment_ids],
            )
            existing_claims = _merge_refs(
                [], [ref for ref in candidate.existing_claim_ids if ref in existing_claim_ids]
            )
            existing_risks = _merge_refs(
                [], [ref for ref in candidate.existing_risk_ids if ref in existing_issue_ids]
            )

            key = _norm_text(candidate.title)
            existing_option = option_by_key.get(key)
            if existing_option is not None:
                existing_option.claim_ids = _merge_refs(existing_option.claim_ids, option_claim_ids)
                existing_option.risk_ids = _merge_refs(existing_option.risk_ids, risk_ids)
                existing_option.assumption_ids = _merge_refs(
                    existing_option.assumption_ids, assumption_ids
                )
                existing_option.experiment_ids = _merge_refs(
                    existing_option.experiment_ids, experiments_for_option
                )
                continue
            option = OptionItem(
                id=factory("option", candidate.local_id, role_code.value),
                title=candidate.title,
                summary=candidate.summary,
                claim_ids=option_claim_ids,
                tradeoffs=_merge_refs([], [str(item) for item in candidate.tradeoffs]),
                risk_ids=risk_ids,
                assumption_ids=assumption_ids,
                experiment_ids=experiments_for_option,
                origin=OriginKind.model,
                status=ClaimStatus.proposed,
            )
            # existing_*_ids 属于"引用已有条目"，直接并入引用集合（已校验存在）。
            option.claim_ids = _merge_refs(option.claim_ids, existing_claims)
            option.risk_ids = _merge_refs(option.risk_ids, existing_risks)
            options.append(option)
            option_by_key[key] = option

        # 4) 未决事项：critical_issues（risk）+ uncertainties
        _merge_issues(
            issues,
            issue_by_key,
            role_code=role_code,
            result=result,
            factory=factory,
            claim_ids=claim_ids,
        )

    return state.model_copy(
        update={
            "claims": claims,
            "options": options,
            "experiments": experiments,
            "unresolved_issues": issues,
            "source_run_id": run_id,
        }
    )


# --------------------------------------------------------------------------- #
# 归并辅助
# --------------------------------------------------------------------------- #


def _claim_key(kind: Any, text: str) -> tuple[str, str]:
    return (str(kind), _norm_text(text))


def _merge_issues(
    issues: list[UnresolvedIssue],
    issue_by_key: dict[tuple[str, str], UnresolvedIssue],
    *,
    role_code: RoleCode,
    result: RoleResult,
    factory: IdFactory,
    claim_ids: dict[str, str],
) -> None:
    """critical_issues → risk；uncertainties → assumption/question/gap。"""

    kind_map = {"assumption": "assumption", "question": "question", "data_gap": "gap"}
    pending: list[tuple[str, str, str | None, list[str]]] = []
    for index, issue in enumerate(result.critical_issues):
        pending.append(("risk", issue.text, issue.dimension, []))
        del index
    for index, uncertainty in enumerate(result.uncertainties):
        pending.append((kind_map.get(str(uncertainty.kind), "question"), uncertainty.text, None, []))
        del index

    # 稳定顺序：按 (kind, 文本) 排序，避免模型条目顺序影响结果
    pending.sort(key=lambda entry: (entry[0], _norm_text(entry[1])))
    for ordinal, (kind, text, dimension, related) in enumerate(pending):
        key = (kind, _norm_text(text))
        existing = issue_by_key.get(key)
        if existing is not None:
            existing.related_ids = _merge_refs(existing.related_ids, related)
            continue
        issue = UnresolvedIssue(
            id=factory("issue", f"{kind}-{ordinal}", role_code.value),
            kind=kind,  # type: ignore[arg-type]
            text=text,
            related_ids=_merge_refs([], related),
            status=IssueStatus.open,
            dimension=dimension,
        )
        issues.append(issue)
        issue_by_key[key] = issue


__all__ = [
    "DEFAULT_ROLES",
    "MAX_AUTO_ROLES",
    "ROLE_KEYWORD_RULES",
    "ROLE_ORDER",
    "RoleOutput",
    "merge_role_results",
    "role_logical_key",
    "select_roles",
]
