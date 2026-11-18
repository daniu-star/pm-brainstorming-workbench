"""FR-04B/FR-04C 单元测试：确定性角色选择 + 确定性归并（task-1）。

这些用例是**纯函数**测试：不连数据库、不调模型、不读时间/随机数。
"""
from __future__ import annotations

import unittest

from domain.contracts import (
    CandidateClaim,
    CandidateExperiment,
    CandidateOption,
    ClaimKind,
    ClaimStatus,
    CriticalIssue,
    ExperimentStatus,
    IssueStatus,
    OriginKind,
    ProductState,
    RoleCode,
    RoleResult,
    TargetRole,
    Uncertainty,
    canonical_json,
    initial_product_state,
    new_id,
)
from domain.errors import AppError, ErrorCode
from services.reducer import (
    DEFAULT_ROLES,
    MAX_AUTO_ROLES,
    ROLE_ORDER,
    RoleOutput,
    merge_role_results,
    role_logical_key,
    select_roles,
)


# --------------------------------------------------------------------------- #
# 构造辅助
# --------------------------------------------------------------------------- #


def make_state(**updates) -> ProductState:
    payload = initial_product_state(new_id()).model_dump(mode="json")
    payload.update(updates)
    return ProductState.model_validate(payload)


def make_claim(local_id: str, text: str, *, kind: ClaimKind = ClaimKind.fact, **updates) -> CandidateClaim:
    payload = {"local_id": local_id, "kind": kind, "text": text, "evidence_refs": [], "source_message_ids": []}
    payload.update(updates)
    return CandidateClaim.model_validate(payload)


def make_option(local_id: str, title: str, *, claim_ids=(), **updates) -> CandidateOption:
    payload = {"local_id": local_id, "title": title, "claim_local_ids": list(claim_ids)}
    payload.update(updates)
    return CandidateOption.model_validate(payload)


def make_experiment(local_id: str, method: str, *, hypothesis=None, **updates) -> CandidateExperiment:
    payload = {
        "local_id": local_id,
        "method": method,
        "success_criterion": "可验证",
        "hypothesis_local_id": hypothesis,
    }
    payload.update(updates)
    return CandidateExperiment.model_validate(payload)


def make_result(
    role: RoleCode,
    *,
    claims=(),
    options=(),
    experiments=(),
    issues=(),
    uncertainties=(),
    display: str = "角色输出",
) -> RoleResult:
    return RoleResult(
        role_code=role,
        display_text=display,
        claims=list(claims),
        option_proposals=list(options),
        suggested_experiments=list(experiments),
        critical_issues=list(issues),
        uncertainties=list(uncertainties),
    )


def det_factory(kind: str, local_id: str, role: str) -> str:
    """确定性 ID 工厂：相同 (kind, local_id, role) 永远给同一个 ID。"""

    return f"{kind}:{role}:{local_id}"


def canonical(state: ProductState) -> str:
    return canonical_json(state.model_dump(mode="json"))


# --------------------------------------------------------------------------- #
# FR-04B：角色选择
# --------------------------------------------------------------------------- #


class SelectRolesTests(unittest.TestCase):
    def test_all_returns_four_roles_in_fixed_order(self) -> None:
        self.assertEqual(select_roles(TargetRole.all, "随便什么内容"), list(ROLE_ORDER))

    def test_specific_role_only_runs_itself(self) -> None:
        self.assertEqual(select_roles(TargetRole.cto, "设计与商业都不相关"), [RoleCode.cto])
        self.assertEqual(select_roles("designer", ""), [RoleCode.designer])
        self.assertEqual(select_roles("ops", "技术架构"), [RoleCode.ops])

    def test_legacy_user_alias_maps_to_target_user(self) -> None:
        self.assertEqual(select_roles("user", "任意"), [RoleCode.target_user])

    def test_auto_prefers_technical_role_for_engineering_topics(self) -> None:
        roles = select_roles(TargetRole.auto, "这个系统的技术架构、数据一致性和性能、安全都要考虑")
        self.assertEqual(roles, [RoleCode.cto])

    def test_auto_picks_designer_for_experience_topics(self) -> None:
        roles = select_roles(TargetRole.auto, "首轮流程交互体验和易用性、无障碍也要评估")
        self.assertEqual(roles, [RoleCode.designer])

    def test_auto_picks_ops_for_commercial_topics(self) -> None:
        roles = select_roles(TargetRole.auto, "定价、收入模型、增长获客和渠道预算怎么定")
        self.assertEqual(roles, [RoleCode.ops])

    def test_auto_picks_target_user_for_user_research_topics(self) -> None:
        roles = select_roles(TargetRole.auto, "用户的真实痛点、替代方案、采用习惯和留存")
        self.assertEqual(roles, [RoleCode.target_user])

    def test_auto_ties_fall_back_to_role_order(self) -> None:
        # 技术、设计各命中一次 ⇒ 平分，按固定顺序 cto 在前。
        roles = select_roles(TargetRole.auto, "技术架构 与 交互体验")
        self.assertEqual(roles, [RoleCode.cto, RoleCode.designer])

    def test_auto_returns_default_roles_when_nothing_matches(self) -> None:
        self.assertEqual(select_roles(TargetRole.auto, "嗯，我们再想想"), list(DEFAULT_ROLES))

    def test_auto_caps_at_three_roles(self) -> None:
        content = "技术架构 数据 性能 安全 交互体验 易用 无障碍 定价 收入 增长 获客 用户痛点 采用"
        roles = select_roles(TargetRole.auto, content)
        self.assertLessEqual(len(roles), MAX_AUTO_ROLES)
        self.assertEqual(len(roles), MAX_AUTO_ROLES)
        # 顺序始终是固定的 role_order 子序列
        indexes = [ROLE_ORDER.index(role) for role in roles]
        self.assertEqual(indexes, sorted(indexes))

    def test_open_issues_add_weight_to_the_matching_role(self) -> None:
        state = make_state(
            unresolved_issues=[
                {"id": new_id(), "kind": "risk", "text": "定价模型还没验证", "related_ids": [], "status": "open", "dimension": "business"},
            ]
        )
        roles = select_roles(TargetRole.auto, "整体方案怎么落地", state)
        self.assertIn(RoleCode.ops, roles)

    def test_resolved_issues_do_not_affect_selection(self) -> None:
        state = make_state(
            unresolved_issues=[
                {"id": new_id(), "kind": "risk", "text": "定价模型还没验证", "related_ids": [], "status": "resolved", "dimension": "business"},
            ]
        )
        self.assertEqual(select_roles(TargetRole.auto, "嗯，我们再想想", state), list(DEFAULT_ROLES))

    def test_selection_is_deterministic(self) -> None:
        state = make_state()
        first = select_roles(TargetRole.auto, "技术与交互与定价", state)
        second = select_roles(TargetRole.auto, "技术与交互与定价", state)
        self.assertEqual(first, second)

    def test_unknown_target_is_rejected(self) -> None:
        with self.assertRaises(AppError) as ctx:
            select_roles("cfo", "内容")
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_REQUEST)

    def test_none_target_means_auto(self) -> None:
        self.assertEqual(select_roles(None, "技术架构"), [RoleCode.cto])


# --------------------------------------------------------------------------- #
# FR-04C：确定性归并
# --------------------------------------------------------------------------- #


class MergeRoleResultsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = make_state()
        self.run_id = new_id()

    def test_new_claims_are_proposed_and_carry_role_and_version(self) -> None:
        result = make_result(
            RoleCode.cto,
            claims=[make_claim("c1", "需要先确认数据一致性策略", source_message_ids=["m1"])],
        )
        merged = merge_role_results(self.state, [result], run_id=self.run_id, id_factory=det_factory)
        self.assertEqual(len(merged.claims), 1)
        claim = merged.claims[0]
        self.assertEqual(claim.id, "claim:cto:c1")
        self.assertIs(claim.status, ClaimStatus.proposed)
        self.assertIs(claim.origin, OriginKind.model)
        self.assertIs(claim.role_code, RoleCode.cto)
        self.assertEqual(claim.created_product_version, self.state.version + 1)
        self.assertEqual(claim.source_message_ids, ["m1"])

    def test_version_is_not_advanced_by_merge(self) -> None:
        result = make_result(RoleCode.cto, claims=[make_claim("c1", "主张一")])
        merged = merge_role_results(self.state, [result], run_id=self.run_id, id_factory=det_factory)
        self.assertEqual(merged.version, self.state.version)
        self.assertEqual(merged.session_id, self.state.session_id)
        self.assertEqual(merged.source_run_id, self.run_id)

    def test_merge_is_independent_of_arrival_order(self) -> None:
        cto = make_result(RoleCode.cto, claims=[make_claim("c1", "技术侧风险 A")], display="cto")
        designer = make_result(RoleCode.designer, claims=[make_claim("d1", "流程需要简化")], display="designer")
        ops = make_result(RoleCode.ops, claims=[make_claim("o1", "定价需验证")], display="ops")

        forward = merge_role_results(self.state, [cto, designer, ops], run_id=self.run_id, id_factory=det_factory)
        backward = merge_role_results(self.state, [ops, designer, cto], run_id=self.run_id, id_factory=det_factory)
        shuffled = merge_role_results(self.state, [designer, ops, cto], run_id=self.run_id, id_factory=det_factory)

        self.assertEqual(canonical(forward), canonical(backward))
        self.assertEqual(canonical(forward), canonical(shuffled))
        self.assertEqual([claim.id for claim in forward.claims], ["claim:cto:c1", "claim:designer:d1", "claim:ops:o1"])

    def test_same_local_id_in_different_roles_gets_distinct_server_ids(self) -> None:
        cto = make_result(RoleCode.cto, claims=[make_claim("c1", "技术主张")])
        designer = make_result(RoleCode.designer, claims=[make_claim("c1", "设计主张")])
        merged = merge_role_results(self.state, [cto, designer], run_id=self.run_id, id_factory=det_factory)
        self.assertEqual(len(merged.claims), 2)
        self.assertEqual({claim.id for claim in merged.claims}, {"claim:cto:c1", "claim:designer:c1"})

    def test_identical_claim_text_is_deduplicated_and_sources_are_unioned(self) -> None:
        cto = make_result(
            RoleCode.cto,
            claims=[make_claim("c1", "登录必须先可用", source_message_ids=["m1"], evidence_refs=["e1"])],
        )
        designer = make_result(
            RoleCode.designer,
            claims=[make_claim("d1", "登录必须先可用", source_message_ids=["m2"], evidence_refs=["e2"])],
        )
        merged = merge_role_results(self.state, [cto, designer], run_id=self.run_id, id_factory=det_factory)
        self.assertEqual(len(merged.claims), 1)
        claim = merged.claims[0]
        self.assertEqual(claim.source_message_ids, ["m1", "m2"])
        self.assertEqual(claim.evidence_refs, ["e1", "e2"])
        # 保留排序在前（cto）的那条
        self.assertIs(claim.role_code, RoleCode.cto)

    def test_duplicate_option_title_merges_references(self) -> None:
        cto = make_result(
            RoleCode.cto,
            claims=[make_claim("c1", "方案 A 主张")],
            options=[make_option("o1", "方案 A", claim_ids=["c1"])],
        )
        ops = make_result(
            RoleCode.ops,
            claims=[make_claim("p1", "方案 A 的商业价值")],
            options=[make_option("q1", "方案 A", claim_ids=["p1"])],
        )
        merged = merge_role_results(self.state, [cto, ops], run_id=self.run_id, id_factory=det_factory)
        self.assertEqual(len(merged.options), 1)
        option = merged.options[0]
        self.assertEqual(option.id, "option:cto:o1")
        self.assertEqual(option.claim_ids, ["claim:cto:c1", "claim:ops:p1"])

    def test_distinct_option_titles_are_kept_separately(self) -> None:
        result = make_result(
            RoleCode.designer,
            claims=[make_claim("c1", "主张一")],
            options=[make_option("o1", "引导式上手", claim_ids=["c1"]), make_option("o2", "专业模式")],
        )
        merged = merge_role_results(self.state, [result], run_id=self.run_id, id_factory=det_factory)
        self.assertEqual([option.title for option in merged.options], ["引导式上手", "专业模式"])
        self.assertEqual(merged.options[0].claim_ids, ["claim:designer:c1"])
        self.assertEqual(merged.options[1].claim_ids, [])

    def test_experiments_are_merged_and_option_refs_resolve(self) -> None:
        result = make_result(
            RoleCode.ops,
            claims=[make_claim("c1", "定价需要实验")],
            experiments=[make_experiment("x1", "A/B 定价测试", hypothesis="c1")],
            options=[make_option("o1", "渐进定价", claim_ids=["c1"], experiment_local_ids=["x1"])],
        )
        merged = merge_role_results(self.state, [result], run_id=self.run_id, id_factory=det_factory)
        self.assertEqual(len(merged.experiments), 1)
        experiment = merged.experiments[0]
        self.assertEqual(experiment.id, "experiment:ops:x1")
        self.assertEqual(experiment.hypothesis_claim_id, "claim:ops:c1")
        self.assertIs(experiment.status, ExperimentStatus.proposed)
        self.assertEqual(merged.options[0].experiment_ids, ["experiment:ops:x1"])

    def test_duplicate_experiment_is_not_duplicated(self) -> None:
        first = make_result(RoleCode.ops, experiments=[make_experiment("x1", "A/B 定价测试")])
        second = make_result(RoleCode.designer, experiments=[make_experiment("x2", "A/B  定价测试")])
        merged = merge_role_results(self.state, [first, second], run_id=self.run_id, id_factory=det_factory)
        self.assertEqual(len(merged.experiments), 1)

    def test_critical_issues_and_uncertainties_become_open_issues(self) -> None:
        result = make_result(
            RoleCode.cto,
            issues=[CriticalIssue(text="数据迁移风险", severity="high", dimension="data")],
            uncertainties=[Uncertainty(text="并发量级未知", kind="data_gap")],
        )
        merged = merge_role_results(self.state, [result], run_id=self.run_id, id_factory=det_factory)
        self.assertEqual(len(merged.unresolved_issues), 2)
        kinds = [issue.kind for issue in merged.unresolved_issues]
        self.assertEqual(sorted(kinds), ["gap", "risk"])
        self.assertTrue(all(issue.status is IssueStatus.open for issue in merged.unresolved_issues))

    def test_duplicate_issue_text_is_deduplicated(self) -> None:
        cto = make_result(RoleCode.cto, issues=[CriticalIssue(text="上线风险", severity="high")])
        ops = make_result(RoleCode.ops, issues=[CriticalIssue(text="上线风险 ", severity="medium")])
        merged = merge_role_results(self.state, [cto, ops], run_id=self.run_id, id_factory=det_factory)
        self.assertEqual(len(merged.unresolved_issues), 1)

    def test_option_local_refs_resolve_to_server_ids(self) -> None:
        result = make_result(
            RoleCode.designer,
            claims=[make_claim("c1", "主张一")],
            options=[make_option("o1", "方案一", claim_ids=["c1"])],
        )
        merged = merge_role_results(self.state, [result], run_id=self.run_id, id_factory=det_factory)
        self.assertEqual(merged.options[0].claim_ids, ["claim:designer:c1"])

    def test_existing_refs_are_filtered_to_known_ids(self) -> None:
        state = make_state(
            claims=[
                {
                    "id": "old-claim",
                    "kind": "fact",
                    "text": "既有主张",
                    "source_message_ids": [],
                    "evidence_refs": [],
                }
            ]
        )
        result = make_result(
            RoleCode.cto,
            options=[make_option("o1", "复用既有主张", existing_claim_ids=["old-claim", "ghost-claim"])],
        )
        merged = merge_role_results(state, [result], run_id=self.run_id, id_factory=det_factory)
        self.assertEqual(merged.options[0].claim_ids, ["old-claim"])

    def test_role_outputs_can_be_passed_as_pairs_and_duck_typed(self) -> None:
        result = make_result(RoleCode.target_user, claims=[make_claim("u1", "用户会忽略引导")])
        merged = merge_role_results(
            self.state,
            [(RoleCode.target_user, result)],
            run_id=self.run_id,
            id_factory=det_factory,
        )
        self.assertEqual(merged.claims[0].id, "claim:target_user:u1")

        wrapped = RoleOutput(RoleCode.target_user, role_logical_key(RoleCode.target_user), result)
        merged_again = merge_role_results(self.state, [wrapped], run_id=self.run_id, id_factory=det_factory)
        self.assertEqual(canonical(merged), canonical(merged_again))

    def test_empty_results_leave_state_content_unchanged(self) -> None:
        merged = merge_role_results(self.state, [], run_id=self.run_id, id_factory=det_factory)
        self.assertEqual(merged.claims, self.state.claims)
        self.assertEqual(merged.options, self.state.options)
        self.assertEqual(merged.unresolved_issues, self.state.unresolved_issues)
        self.assertEqual(merged.source_run_id, self.run_id)

    def test_invalid_input_is_rejected(self) -> None:
        with self.assertRaises(AppError) as ctx:
            merge_role_results(self.state, ["not-a-result"], run_id=self.run_id)
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_REQUEST)

    def test_non_state_is_rejected(self) -> None:
        with self.assertRaises(AppError):
            merge_role_results(None, [], run_id=self.run_id)

    def test_step_ids_mapping_does_not_change_output(self) -> None:
        result = make_result(RoleCode.cto, claims=[make_claim("c1", "主张一")])
        without = merge_role_results(self.state, [result], run_id=self.run_id, id_factory=det_factory)
        with_ids = merge_role_results(
            self.state,
            [result],
            run_id=self.run_id,
            step_ids={role_logical_key(RoleCode.cto): "step-1"},
            id_factory=det_factory,
        )
        self.assertEqual(canonical(without), canonical(with_ids))

    def test_role_logical_key_is_stable(self) -> None:
        self.assertEqual(role_logical_key(RoleCode.cto), "brainstorm.role.cto")
        self.assertEqual(role_logical_key("target_user"), "brainstorm.role.target_user")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
