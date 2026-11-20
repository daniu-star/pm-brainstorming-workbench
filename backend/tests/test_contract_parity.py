"""契约 parity 测试：`frontend/lib/contracts.generated.ts` ↔ `backend/domain/contracts.py`。

背景
----
前端类型层是**手写镜像**（`frontend/lib/contracts.generated.ts` 文件头注明 M1 不做自动
codegen）。手写镜像最容易出的问题不是「少写一个类型」，而是**枚举漂移**：后端加了
`loop.updated` 事件、前端忘补；后端把 `EventType` 从 11 条扩到 12 条、前端还是 11 条。
这类漂移在运行时表现为「事件被前端丢弃」「按钮永远不可用」，很难靠人工 review 兜住。

本测试把 TS 里的 `export const XXX = [...] as const` 与后端枚举 / `Literal` 提示
逐条对比，作为**唯一的自动守卫**。任何一侧改动而另一侧未同步，测试即失败并给出
具体差异（缺少的值、多余的值、顺序、重复）。

解析策略
--------
- 先去掉 `//` 行注释与 `/* */` 块注释，避免注释里的引号干扰；
- 用 `export const NAME = [ ... ] as const;` 非贪婪匹配取出数组体；
- 数组体里抓单/双引号字符串字面量；
- 与后端比对时**按顺序**比较（前端数组顺序即 UI 展示顺序，值得锁住），
  同时额外检查重复值。

用法
----
    cd backend && ../.venv/Scripts/python.exe -m unittest tests.test_contract_parity -v
"""

from __future__ import annotations

import enum
import re
import typing
import unittest
from pathlib import Path

from domain import contracts as C

REPO_ROOT = Path(__file__).resolve().parents[2]
TS_CONTRACTS = REPO_ROOT / "frontend" / "lib" / "contracts.generated.ts"

_COMMENT_BLOCK_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_COMMENT_LINE_RE = re.compile(r"^\s*//.*$", re.MULTILINE)
_STRING_RE = re.compile(r'"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\'')


def _strip_comments(text: str) -> str:
    return _COMMENT_LINE_RE.sub("", _COMMENT_BLOCK_RE.sub("", text))


def _parse_ts_arrays(text: str) -> dict[str, list[str]]:
    """解析 `export const NAME = [ ... ] as const;` 为 {NAME: [values]}。"""

    body = _strip_comments(text)
    arrays: dict[str, list[str]] = {}
    pattern = re.compile(
        r"export\s+const\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\[(.*?)\]\s*as\s+const\s*;",
        re.DOTALL,
    )
    for match in pattern.finditer(body):
        name = match.group(1)
        raw = match.group(2)
        values: list[str] = []
        for single, double in _STRING_RE.findall(raw):
            values.append(single if single else double)
        arrays[name] = values
    return arrays


def _enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    return [member.value for member in enum_cls]


def _literal_values(annotation: object) -> list[str]:
    """从 `typing.Literal[...]` 注解里取出字符串取值。"""

    if typing.get_origin(annotation) is typing.Literal:
        return [value for value in typing.get_args(annotation) if isinstance(value, str)]
    raise AssertionError(f"不是 Literal 注解：{annotation!r}")


def _model_literal(model: type, field_name: str) -> list[str]:
    annotation = model.model_fields[field_name].annotation
    return _literal_values(annotation)


class TsArrayParsingTests(unittest.TestCase):
    """先证明解析器本身可靠，否则 parity 测试会「假绿」。"""

    def setUp(self) -> None:
        self.assertTrue(TS_CONTRACTS.is_file(), f"找不到前端契约文件：{TS_CONTRACTS}")
        self.text = TS_CONTRACTS.read_text(encoding="utf-8")
        self.arrays = _parse_ts_arrays(self.text)

    def test_parses_expected_number_of_arrays(self) -> None:
        # 30+ 个枚举数组；数量级断言可防止正则整体失效后「全部跳过」的假绿。
        self.assertGreaterEqual(
            len(self.arrays),
            30,
            f"只解析到 {len(self.arrays)} 个数组，正则可能失效：{sorted(self.arrays)[:5]}",
        )

    def test_single_line_and_multi_line_arrays_are_both_parsed(self) -> None:
        # 单行样例
        self.assertEqual(
            self.arrays["SESSION_PHASES"],
            ["clarifying", "brainstorming", "auditing", "audit_completed"],
        )
        # 多行样例（RUN_STATUSES 是换行书写的）
        self.assertEqual(len(self.arrays["RUN_STATUSES"]), 8)

    def test_no_array_contains_duplicates(self) -> None:
        duplicated = {
            name: sorted({value for value in values if values.count(value) > 1})
            for name, values in self.arrays.items()
            if len(set(values)) != len(values)
        }
        self.assertEqual(duplicated, {}, f"前端枚举数组存在重复值：{duplicated}")


class EnumParityTests(unittest.TestCase):
    """前端字符串联合 ↔ 后端枚举。"""

    #: 前端数组名 -> 后端枚举类
    ENUM_MAP: dict[str, type[enum.Enum]] = {
        "SESSION_PHASES": C.SessionPhase,
        "RUN_STATUSES": C.RunStatus,
        "STEP_STATUSES": C.StepStatus,
        "MESSAGE_STATUSES": C.MessageStatus,
        "SENDER_KINDS": C.SenderKind,
        "ROLE_CODES": C.RoleCode,
        "MESSAGE_STAGES": C.MessageStage,
        "CLAIM_KINDS": C.ClaimKind,
        "CLAIM_STATUSES": C.ClaimStatus,
        "EVIDENCE_KINDS": C.EvidenceKind,
        "VERIFICATION_STATUSES": C.VerificationStatus,
        "DECISION_STATUSES": C.DecisionStatus,
        "EXPERIMENT_STATUSES": C.ExperimentStatus,
        "ISSUE_STATUSES": C.IssueStatus,
        "BRIEF_FIELD_STATUSES": C.BriefFieldStatus,
        "BRIEF_STATUSES": C.BriefStatus,
        "AUDIT_STATUSES": C.AuditStatus,
        "ASSESSMENTS": C.Assessment,
        "END_REASONS": C.EndReason,
        "TARGET_ROLES": C.TargetRole,
        "ACTIONS": C.Action,
        "CONTROL_STATUSES": C.ControlStatus,
        "EVENT_TYPES": C.EventType,
        "TASK_KINDS": C.TaskKind,
        "CONTEXT_SECTIONS": C.ContextSection,
        "ORIGIN_KINDS": C.OriginKind,
        "CONFIDENCE_LEVELS": C.ConfidenceLevel,
        "LOOP_STATUSES": C.LoopStatus,
        "LOOP_SCOPES": C.LoopScope,
        "LOOP_STAGES": C.LoopStage,
        "LOOP_STAGE_STATUSES": C.LoopStageStatus,
        "LOOP_ROUND_STATUSES": C.LoopRoundStatus,
        "LOOP_VERDICTS": C.LoopVerdict,
        "LOOP_DECISION_KINDS": C.LoopDecisionKind,
        "LOOP_ARTIFACT_KINDS": C.LoopArtifactKind,
        "CHECK_RESULTS": C.CheckResult,
        "TEST_CASE_STATUSES": C.TestCaseStatus,
        "FEATURE_PRIORITIES": C.FeaturePriority,
        "EFFORT_SIZES": C.EffortSize,
        "LOOP_SCORE_KEYS": C.LoopScoreKey,
    }

    @classmethod
    def setUpClass(cls) -> None:
        cls.arrays = _parse_ts_arrays(TS_CONTRACTS.read_text(encoding="utf-8"))

    def test_every_mapped_array_exists(self) -> None:
        missing = [name for name in self.ENUM_MAP if name not in self.arrays]
        self.assertEqual(missing, [], f"前端缺少这些枚举数组：{missing}")

    def test_enum_values_match_in_order(self) -> None:
        mismatched: dict[str, dict[str, list[str]]] = {}
        for ts_name, enum_cls in self.ENUM_MAP.items():
            expected = _enum_values(enum_cls)
            actual = self.arrays.get(ts_name, [])
            if actual != expected:
                mismatched[ts_name] = {
                    "backend": expected,
                    "frontend": actual,
                    "missing_in_frontend": [v for v in expected if v not in actual],
                    "extra_in_frontend": [v for v in actual if v not in expected],
                }
        self.assertEqual(
            mismatched,
            {},
            "前端契约与后端枚举漂移（请同步 frontend/lib/contracts.generated.ts）：\n"
            + "\n".join(f"  - {name}: {diff}" for name, diff in mismatched.items()),
        )

    def test_normal_and_control_actions_are_covered_by_actions(self) -> None:
        actions = self.arrays["ACTIONS"]
        action_set = set(actions)

        # 用户可提交的动作（NORMAL + CONTROL）必须都在 ACTIONS 里
        for action in sorted(a.value for a in C.NORMAL_ACTIONS) + sorted(
            a.value for a in C.CONTROL_ACTIONS
        ):
            self.assertIn(action, action_set, f"可提交动作 {action} 未写入前端 ACTIONS")

        # 内部动作（synthesis / loop_round）必须出现在 TS 的 ACTIONS 里，
        # 但**不属于**用户可提交动作（否则前端会给出「提交 synthesis」这样的非法入口）。
        normal = {a.value for a in C.NORMAL_ACTIONS}
        control = {a.value for a in C.CONTROL_ACTIONS}
        for internal in ("synthesis", "loop_round"):
            self.assertIn(internal, action_set, f"内部动作 {internal} 未写入前端 ACTIONS")
            self.assertNotIn(internal, normal, f"内部动作 {internal} 不应出现在 NORMAL_ACTIONS")
            self.assertNotIn(internal, control, f"内部动作 {internal} 不应出现在 CONTROL_ACTIONS")


class ConstantParityTests(unittest.TestCase):
    """常量元组 / Literal 字段的 parity。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.arrays = _parse_ts_arrays(TS_CONTRACTS.read_text(encoding="utf-8"))

    def test_schema_version_matches(self) -> None:
        match = re.search(r"export\s+const\s+SCHEMA_VERSION\s*=\s*(\d+)\s*;", TS_CONTRACTS.read_text(encoding="utf-8"))
        self.assertIsNotNone(match, "前端未声明 SCHEMA_VERSION")
        self.assertEqual(int(match.group(1)), C.SCHEMA_VERSION)

    def test_audit_dimensions_match(self) -> None:
        self.assertEqual(self.arrays["AUDIT_DIMENSIONS"], list(C.AUDIT_DIMENSIONS))

    def test_brief_fields_match(self) -> None:
        self.assertEqual(self.arrays["BRIEF_FIELDS"], list(C.BRIEF_FIELDS))

    def test_loop_stages_match(self) -> None:
        self.assertEqual(self.arrays["LOOP_STAGES"], [stage.value for stage in C.LOOP_STAGES])

    def test_loop_score_keys_match_labels(self) -> None:
        self.assertEqual(
            self.arrays["LOOP_SCORE_KEYS"],
            [key.value for key in C.LOOP_SCORE_LABELS],
        )

    def test_issue_kinds_match_model_literal(self) -> None:
        self.assertEqual(
            self.arrays["ISSUE_KINDS"],
            _model_literal(C.UnresolvedIssue, "kind"),
        )

    def test_canvas_node_kinds_match_model_literal(self) -> None:
        self.assertEqual(
            self.arrays["CANVAS_NODE_KINDS"],
            _model_literal(C.CanvasNode, "kind"),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
