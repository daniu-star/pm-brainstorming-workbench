"""确定性画布投影测试（FR-05 / ADR-06）。

覆盖验收点：
- AC-19 画布是产品状态的确定性投影：同一状态两次投影逐字节相等；
- AC-20 既无来源消息又无证据的主张必须标记为模型推测（``model_speculation=True``）；
- 版本透传（``product_version`` 恒等于状态版本），版本不符拒绝投影；
- 分支分组与节点 id 规则稳定，空状态不崩。
"""
import unittest

from domain.contracts import (
    BriefField,
    BriefFieldStatus,
    Claim,
    ClaimKind,
    ClaimStatus,
    DecisionItem,
    ExperimentItem,
    OptionItem,
    ProductState,
    RoleCode,
    UnresolvedIssue,
    canonical_json,
    new_id,
)
from domain.errors import AppError, ErrorCode
from services.canvas_projector import BRANCH_TITLES, ROOT_NODE_ID, project_canvas


# --------------------------------------------------------------------------- #
# 构造辅助
# --------------------------------------------------------------------------- #


def make_state(*, version: int = 1, **kwargs) -> ProductState:
    payload = {"session_id": new_id(), "version": version}
    payload.update(kwargs)
    return ProductState.model_validate(payload)


def make_claim(
    claim_id: str,
    *,
    kind: ClaimKind = ClaimKind.feature,
    text: str = "支持离线使用",
    source: tuple[str, ...] = (),
    evidence: tuple[str, ...] = (),
    role: RoleCode | None = None,
    status: ClaimStatus = ClaimStatus.proposed,
) -> Claim:
    return Claim(
        id=claim_id,
        kind=kind,
        text=text,
        source_message_ids=list(source),
        evidence_refs=list(evidence),
        role_code=role,
        status=status,
    )


def branch_keys(projection) -> list[str]:
    return [branch.key for branch in projection.branches]


def nodes_of(projection, key: str) -> list:
    for branch in projection.branches:
        if branch.key == key:
            return list(branch.nodes)
    return []


class ProjectorTestCase(unittest.TestCase):
    """纯函数测试：不建库、不碰时间与随机数。"""


# --------------------------------------------------------------------------- #
# 确定性
# --------------------------------------------------------------------------- #


class DeterminismTests(ProjectorTestCase):
    def _rich_state(self) -> ProductState:
        claim_a = make_claim("c1", source=("m1",), role=RoleCode.cto)
        claim_b = make_claim("c2", kind=ClaimKind.risk, text="合规风险")
        option = OptionItem(
            id="o1",
            title="本地优先方案",
            summary="全部数据留在本机",
            claim_ids=["c1"],
        )
        decision = DecisionItem(id="d1", option_id="o1", rationale="先验证留存")
        experiment = ExperimentItem(
            id="e1",
            hypothesis_claim_id="c1",
            method="两周灰度",
            success_criterion="次日留存 >= 30%",
        )
        issue = UnresolvedIssue(id="u1", kind="assumption", text="用户愿意订阅")
        return make_state(
            version=3,
            brief={
                "core_problem": {"value": "写作时间被切碎", "status": "proposed"},
            },
            claims=[claim_a, claim_b],
            options=[option],
            decisions=[decision],
            experiments=[experiment],
            unresolved_issues=[issue],
        )

    def test_two_calls_are_byte_identical(self):
        state = self._rich_state()
        first = canonical_json(project_canvas(state).model_dump(mode="json"))
        second = canonical_json(project_canvas(state).model_dump(mode="json"))
        self.assertEqual(first, second)

    def test_two_calls_are_byte_identical_via_model_dump_json(self):
        state = self._rich_state()
        first = project_canvas(state).model_dump_json()
        second = project_canvas(state).model_dump_json()
        self.assertEqual(first, second)

    def test_projection_does_not_mutate_state(self):
        state = self._rich_state()
        before = canonical_json(state.model_dump(mode="json"))
        project_canvas(state)
        self.assertEqual(before, canonical_json(state.model_dump(mode="json")))

    def test_node_ids_are_deterministic_not_random(self):
        state = self._rich_state()
        projection = project_canvas(state)
        ids = {node.id for branch in projection.branches for node in branch.nodes}
        self.assertIn("claim:c1", ids)
        self.assertIn("option:o1", ids)
        self.assertIn("decision:d1", ids)
        self.assertIn("experiment:e1", ids)
        self.assertIn("assumption:u1", ids)
        self.assertNotIn(ROOT_NODE_ID, ids)  # 根节点独立于分支


# --------------------------------------------------------------------------- #
# 版本
# --------------------------------------------------------------------------- #


class VersionTests(ProjectorTestCase):
    def test_product_version_is_passed_through(self):
        state = make_state(version=7, claims=[make_claim("c1")])
        projection = project_canvas(state)
        self.assertEqual(projection.product_version, 7)
        self.assertEqual(projection.session_id, state.session_id)

    def test_matching_version_argument_is_accepted(self):
        state = make_state(version=4)
        projection = project_canvas(state, version=4)
        self.assertEqual(projection.product_version, 4)

    def test_mismatched_version_raises_conflict(self):
        state = make_state(version=4)
        with self.assertRaises(AppError) as caught:
            project_canvas(state, version=3)
        self.assertEqual(caught.exception.code, ErrorCode.VERSION_CONFLICT)
        self.assertEqual(caught.exception.details["requested_version"], 3)
        self.assertEqual(caught.exception.details["state_version"], 4)

    def test_none_state_is_rejected(self):
        with self.assertRaises(AppError) as caught:
            project_canvas(None)  # type: ignore[arg-type]
        self.assertEqual(caught.exception.code, ErrorCode.INVALID_REQUEST)

    def test_suggestions_reserved_for_m2(self):
        state = make_state()
        with self.assertRaises(AppError) as caught:
            project_canvas(state, suggestions=[])
        self.assertEqual(caught.exception.code, ErrorCode.FEATURE_NOT_READY)


# --------------------------------------------------------------------------- #
# 分支分组
# --------------------------------------------------------------------------- #


class BranchGroupingTests(ProjectorTestCase):
    def test_empty_state_has_no_branches_and_no_root(self):
        projection = project_canvas(make_state())
        self.assertEqual(projection.branches, [])
        self.assertIsNone(projection.root)

    def test_claims_are_grouped_by_role(self):
        state = make_state(
            claims=[
                make_claim("t1", role=RoleCode.cto),
                make_claim("t2", role=RoleCode.designer),
                make_claim("t3", role=RoleCode.ops),
                make_claim("t4", role=RoleCode.target_user),
            ]
        )
        projection = project_canvas(state)
        self.assertEqual(branch_keys(projection), ["technical", "design", "business", "user"])
        self.assertEqual([n.id for n in nodes_of(projection, "technical")], ["claim:t1"])
        self.assertEqual([n.id for n in nodes_of(projection, "design")], ["claim:t2"])
        self.assertEqual([n.id for n in nodes_of(projection, "business")], ["claim:t3"])
        self.assertEqual([n.id for n in nodes_of(projection, "user")], ["claim:t4"])

    def test_unattributed_risk_goes_to_unresolved(self):
        state = make_state(claims=[make_claim("r1", kind=ClaimKind.risk, text="可能被平台封禁")])
        projection = project_canvas(state)
        self.assertEqual(branch_keys(projection), ["unresolved"])
        node = nodes_of(projection, "unresolved")[0]
        self.assertEqual(node.id, "risk:r1")
        self.assertEqual(node.kind, "risk")

    def test_unattributed_feature_goes_to_fallback_branch(self):
        state = make_state(claims=[make_claim("f1", kind=ClaimKind.feature)])
        projection = project_canvas(state)
        self.assertEqual(branch_keys(projection), ["claim"])
        self.assertEqual(nodes_of(projection, "claim")[0].kind, "claim")

    def test_branch_order_follows_declared_order(self):
        state = make_state(
            claims=[
                make_claim("q1", kind=ClaimKind.question, text="要不要做移动端？"),
                make_claim("t1", role=RoleCode.cto),
            ]
        )
        projection = project_canvas(state)
        self.assertEqual(branch_keys(projection), ["technical", "unresolved"])
        declared = [key for key, _title in BRANCH_TITLES]
        self.assertEqual(branch_keys(projection), [k for k in declared if k in branch_keys(projection)])

    def test_options_decisions_experiments_issues_are_projected(self):
        state = make_state(
            claims=[make_claim("c1", source=("m1",))],
            options=[
                OptionItem(
                    id="o1",
                    title="本地优先",
                    summary="数据不出本机",
                    claim_ids=["c1"],
                )
            ],
            decisions=[DecisionItem(id="d1", option_id="o1", rationale="先做单人版")],
            experiments=[
                ExperimentItem(
                    id="e1",
                    hypothesis_claim_id="c1",
                    method="两周灰度",
                    success_criterion="留存 >= 30%",
                )
            ],
            unresolved_issues=[UnresolvedIssue(id="u1", kind="gap", text="缺少付费意愿证据")],
        )
        projection = project_canvas(state)
        self.assertEqual(
            branch_keys(projection),
            ["option", "decision", "unresolved", "experiment", "claim"],
        )
        option_node = nodes_of(projection, "option")[0]
        self.assertEqual(option_node.id, "option:o1")
        self.assertEqual(option_node.kind, "option")
        self.assertEqual(option_node.claim_ids, ["c1"])
        self.assertEqual(option_node.option_ids, ["o1"])

        decision_node = nodes_of(projection, "decision")[0]
        self.assertEqual(decision_node.id, "decision:d1")
        self.assertEqual(decision_node.decision_ids, ["d1"])
        self.assertEqual(decision_node.option_ids, ["o1"])
        # 决策标题复用方案标题，而不是裸 id
        self.assertEqual(decision_node.title, "本地优先")

        experiment_node = nodes_of(projection, "experiment")[0]
        self.assertEqual(experiment_node.id, "experiment:e1")
        self.assertEqual(experiment_node.experiment_ids, ["e1"])
        self.assertEqual(experiment_node.claim_ids, ["c1"])

        issue_node = nodes_of(projection, "unresolved")[0]
        self.assertEqual(issue_node.id, "question:u1")
        self.assertEqual(issue_node.kind, "question")
        # related_ids 混合多种 id，不能冒充 claim_ids
        self.assertEqual(issue_node.claim_ids, [])

    def test_issue_kind_mapping(self):
        state = make_state(
            unresolved_issues=[
                UnresolvedIssue(id="a1", kind="assumption", text="假设"),
                UnresolvedIssue(id="a2", kind="risk", text="风险"),
                UnresolvedIssue(id="a3", kind="conflict", text="冲突"),
            ]
        )
        projection = project_canvas(state)
        ids = [node.id for node in nodes_of(projection, "unresolved")]
        self.assertEqual(ids, ["assumption:a1", "risk:a2", "question:a3"])

    def test_every_node_carries_refs_and_status(self):
        state = make_state(claims=[make_claim("c1", source=("m1", "m1", "m2"), status=ClaimStatus.accepted)])
        node = project_canvas(state).branches[0].nodes[0]
        self.assertEqual(node.status, "accepted")
        # 去重且保序
        self.assertEqual(node.source_message_ids, ["m1", "m2"])
        self.assertEqual(node.evidence_refs, [])


# --------------------------------------------------------------------------- #
# 来源与模型推测标记
# --------------------------------------------------------------------------- #


class ProvenanceTests(ProjectorTestCase):
    def test_claim_without_any_source_is_model_speculation(self):
        state = make_state(claims=[make_claim("c1")])
        node = project_canvas(state).branches[0].nodes[0]
        self.assertTrue(node.model_speculation)
        self.assertEqual(node.source_message_ids, [])
        self.assertEqual(node.evidence_refs, [])

    def test_claim_with_message_source_is_not_speculation(self):
        state = make_state(claims=[make_claim("c1", source=("msg-1",))])
        node = project_canvas(state).branches[0].nodes[0]
        self.assertFalse(node.model_speculation)

    def test_claim_with_evidence_only_is_not_speculation(self):
        state = make_state(claims=[make_claim("c1", evidence=("ev-1",))])
        node = project_canvas(state).branches[0].nodes[0]
        self.assertFalse(node.model_speculation)
        self.assertEqual(node.evidence_refs, ["ev-1"])

    def test_root_node_marks_speculation_without_sources(self):
        state = make_state(
            brief={
                "core_problem": {"value": "写作时间被切碎", "status": "proposed"},
            }
        )
        root = project_canvas(state).root
        self.assertIsNotNone(root)
        self.assertEqual(root.id, ROOT_NODE_ID)
        self.assertEqual(root.kind, "problem")
        self.assertEqual(root.title, "写作时间被切碎")
        self.assertTrue(root.model_speculation)

    def test_root_node_collects_brief_sources(self):
        state = make_state(
            brief={
                "target_users": {
                    "value": "独立开发者",
                    "status": "proposed",
                    "source_message_ids": ["m1"],
                },
                "core_problem": {
                    "value": "写作时间被切碎",
                    "status": "proposed",
                    "source_message_ids": ["m2"],
                },
            }
        )
        root = project_canvas(state).root
        self.assertEqual(root.source_message_ids, ["m1", "m2"])
        self.assertFalse(root.model_speculation)

    def test_confirmed_brief_fields_show_in_root_detail(self):
        state = make_state(
            brief={
                "target_users": {
                    "value": "独立开发者",
                    "status": "confirmed",
                    "source_message_ids": ["m1"],
                },
                "core_problem": {"value": "写作时间被切碎", "status": "confirmed"},
                "success_criteria": {"value": "周更 3 篇", "status": "proposed"},
            },
            version=2,
        )
        root = project_canvas(state).root
        self.assertIn("目标用户：独立开发者", root.detail)
        self.assertIn("成功标准：周更 3 篇", root.detail)


class UnverifiedNumberTests(ProjectorTestCase):
    """AC-20：未验证数字必须投影到节点字段 + 详情 + 验证建议。"""

    def test_unverified_numbers_reach_node_and_detail(self):
        claim = make_claim(
            "c1",
            text="12 家客户每周节省 40% 对账时间",
            source=("msg-1",),
        ).model_copy(update={"unverified_numbers": ["40"]})
        state = make_state(claims=[claim])
        node = project_canvas(state).branches[0].nodes[0]
        self.assertEqual(node.unverified_numbers, ["40"])
        assert node.verification_suggestion is not None
        self.assertIn("未验证数字：40", node.verification_suggestion)
        self.assertIn("统计日期", node.verification_suggestion)
        self.assertIn("未验证数字：40", node.detail)

    def test_claim_without_unverified_numbers_has_no_badge_text(self):
        state = make_state(
            claims=[make_claim("c1", text="12 家客户每周节省时间", source=("m1",))]
        )
        node = project_canvas(state).branches[0].nodes[0]
        self.assertEqual(node.unverified_numbers, [])
        self.assertIsNone(node.verification_suggestion)
        self.assertNotIn("未验证数字", node.detail)


if __name__ == "__main__":
    unittest.main()
