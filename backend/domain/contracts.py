"""V4 canonical 数据契约（PRD §5 / §6）。

本模块是唯一的类型真相来源：
- API 请求/响应、持久化 JSON 列、Worker 输入输出都复用这里的模型；
- 只做类型、枚举与长度校验，不做数据库或网络访问；
- 后端 Pydantic 模型一律 ``extra="forbid"``（FR-04C），未知字段直接拒绝。

时间统一为 UTC ISO-8601 文本（``2026-11-18T03:00:00Z``）；租约与截止时间
另用 epoch 秒整数，便于在 SQL 中比较。
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# --------------------------------------------------------------------------- #
# 常量与限额（FR-08 默认值；可被 Settings 覆盖，但不得留无限值）
# --------------------------------------------------------------------------- #

SCHEMA_VERSION = 1

MAX_USER_INPUT_CHARS = 8000
MAX_CONTEXT_INPUT_TOKENS = 12_000
MAX_OUTPUT_TOKENS = 1_500
MAX_SYNTHESIS_OUTPUT_TOKENS = 2_200
MAX_RUN_INPUT_TOKENS = 60_000
MAX_RUN_OUTPUT_TOKENS = 14_000
MAX_UPSTREAM_CALLS_PER_RUN = 10
STEP_TIMEOUT_SECONDS = 90
RUN_TIMEOUT_SECONDS = 300
ROLE_PARALLELISM = 4
MAX_RUNNING_RUNS = 2
MAX_QUEUED_RUNS_PER_USER = 3
COMMANDS_PER_MINUTE = 20
MAX_REVISION_ROUNDS = 1
LEASE_SECONDS = 30
LEASE_RENEW_SECONDS = 10
QUEUE_WAIT_LIMIT_SECONDS = 600
# FR-06 事件保留：未压缩事件保留 7 天；活动 Run 不压缩；压缩后保留每个 Run 的终结事件。
EVENT_RETENTION_DAYS = 7
EVENT_COMPACT_INTERVAL_SECONDS = 3600

#: 幂等记录保留天数（PRD §5.3 原写 30 天；这里按「可重放窗口」收紧到 7 天，
#: 因为客户端只会在秒级窗口内重试同一 request_id，7 天足以覆盖跨天故障排查）。
IDEMPOTENCY_RETENTION_DAYS = 7
#: 维护性清理（幂等记录 / 过期验证码）的触发间隔，默认 6 小时。
MAINTENANCE_INTERVAL_SECONDS = 6 * 3600
#: 中间载荷（步骤上下文快照 / 角色结果 JSON / 候选表）保留天数，默认 90 天；``0`` 表示关闭。
#: 这些数据只服务于「重试复用」与故障排查，不属于用户可见记录；长期保留会让库随任务量
#: 线性增长（一次四角色头脑风暴就有 4-8 条上下文快照 + 候选 JSON）。
LLM_PAYLOAD_RETENTION_DAYS = 90
#: 账户级配额窗口：同一 owner 在滚动窗口内的模型消耗上限（token / 上游调用次数）。
#: 与单 Run 预算互补——单 Run 预算防「一次任务烧太多」，账户配额防「一个账号反复烧」。
#: 具体数值来自 ``ACCOUNT_DAILY_TOKEN_LIMIT`` / ``ACCOUNT_DAILY_CALL_LIMIT``，0 表示不限。
ACCOUNT_QUOTA_WINDOW_SECONDS = 24 * 3600

# BYOK（账户自带模型）：平台前期免费，模型调用由用户自己买单。这两个默认值只用于
# 前端表单预填与提示，不参与服务端调用——真正生效的是用户在「模型 API 设置」里保存的值。
LLM_CONFIG_DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
LLM_CONFIG_DEFAULT_MODEL = "deepseek-flash"

# FR-08 预算档位：只允许在默认限额基础上**收紧**，用于低成本环境（如个人额度）。
# 通过环境变量 LLM_BUDGET_PROFILE=standard|lean|frugal 选择，写入 Run.budget_json 后可复现。
BUDGET_PROFILE_STANDARD = "standard"
BUDGET_PROFILES: dict[str, dict[str, int]] = {
    BUDGET_PROFILE_STANDARD: {},
    "lean": {
        "max_run_input_tokens": 24_000,
        "max_run_output_tokens": 6_000,
        "max_upstream_calls": 6,
        "max_context_input_tokens": 6_000,
        "max_output_tokens": 900,
        "max_step_output_tokens": 900,
        "max_synthesis_output_tokens": 1_200,
    },
    "frugal": {
        "max_run_input_tokens": 12_000,
        "max_run_output_tokens": 3_000,
        "max_upstream_calls": 4,
        "max_context_input_tokens": 4_000,
        "max_output_tokens": 700,
        "max_step_output_tokens": 700,
        "max_synthesis_output_tokens": 900,
    },
}
BUDGET_PROFILE_NAMES = tuple(BUDGET_PROFILES)


def budget_profile_limits(profile: str | None) -> dict[str, int]:
    """返回档位覆盖值；未知/空档位按 standard（不覆盖）。"""

    name = (profile or "").strip().lower() or BUDGET_PROFILE_STANDARD
    return dict(BUDGET_PROFILES.get(name) or {})

# FR-04C 结构上限
MAX_DISPLAY_TEXT_CHARS = 6_000
MAX_CLAIMS_PER_ROLE_RESULT = 12
MAX_CLAIM_TEXT_CHARS = 500
MAX_OPTIONS_PER_ROLE_RESULT = 3
MAX_CRITICAL_ISSUES = 4
MAX_EXPERIMENTS_PER_ROLE_RESULT = 3
MAX_REFS_PER_ITEM = 8
MAX_LOCAL_ID_CHARS = 64
MAX_EVIDENCE_EXCERPT_CHARS = 8000

# 分页（FR-02）
MESSAGES_PAGE_DEFAULT = 50
MESSAGES_PAGE_MAX = 100
SESSIONS_PAGE_DEFAULT = 20
SESSIONS_PAGE_MAX = 100

# 审计（FR-07）
AUDIT_QUESTION_TARGET = 12
AUDIT_QUESTION_HARD_LIMIT = 18
AUDIT_DIMENSIONS: tuple[str, ...] = (
    "problem_validity",
    "solution_effectiveness",
    "technical_risk",
    "business_loop",
    "user_adoption",
    "execution_risk",
)
AUDIT_DIMENSION_LABELS: dict[str, str] = {
    "problem_validity": "问题真实性",
    "solution_effectiveness": "方案有效性",
    "technical_risk": "技术风险",
    "business_loop": "商业闭环",
    "user_adoption": "用户采用",
    "execution_risk": "执行风险",
}

# 专家角色（v1 的 user 映射为 target_user）
EXPERT_ROLES: tuple[str, ...] = ("cto", "designer", "ops", "target_user")
ROLE_ORDER: dict[str, int] = {r: i for i, r in enumerate(EXPERT_ROLES)}
ALL_ROLE_CODES: tuple[str, ...] = (
    "coach",
    "cto",
    "designer",
    "ops",
    "target_user",
    "moderator",
    "interviewer",
)

SESSION_ID_RE = re.compile(r"^[0-9a-f]{32}$")
PROMPT_VERSION_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}$")


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #


def new_id() -> str:
    """32 位小写十六进制 ID（与既有 session_id 兼容）。"""
    return uuid.uuid4().hex


def new_request_id() -> str:
    """客户端 request_id 的默认形态：标准 UUID 字符串。"""
    return str(uuid.uuid4())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str) -> datetime:
    text = value.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def is_session_id(value: str) -> bool:
    return bool(value) and bool(SESSION_ID_RE.match(value))


def canonical_json(payload: Any) -> str:
    """稳定序列化：用于输入哈希与幂等键比较。"""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def input_hash(action: str, resource_scope: str, payload: Any, expected_version: int | None) -> str:
    """幂等输入哈希：资源身份 + action + payload + expected_version。

    不含连接时间等传输噪声（PRD §5.3）。
    """
    return sha256_text(
        canonical_json(
            {
                "action": action,
                "resource_scope": resource_scope,
                "payload": payload,
                "expected_session_version": expected_version,
            }
        )
    )


class StrictModel(BaseModel):
    """所有契约的基础类：拒绝额外字段。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# --------------------------------------------------------------------------- #
# Canonical 枚举（PRD §5.4）
# --------------------------------------------------------------------------- #


class SessionPhase(StrEnum):
    clarifying = "clarifying"
    brainstorming = "brainstorming"
    auditing = "auditing"
    audit_completed = "audit_completed"


class RunStatus(StrEnum):
    queued = "queued"
    running = "running"
    interrupted = "interrupted"
    cancel_requested = "cancel_requested"
    succeeded = "succeeded"
    partial_succeeded = "partial_succeeded"
    failed = "failed"
    cancelled = "cancelled"


TERMINAL_RUN_STATUSES = frozenset(
    {RunStatus.succeeded, RunStatus.partial_succeeded, RunStatus.failed, RunStatus.cancelled}
)
ACTIVE_RUN_STATUSES = frozenset({RunStatus.queued, RunStatus.running, RunStatus.interrupted, RunStatus.cancel_requested})


class StepStatus(StrEnum):
    pending = "pending"
    running = "running"
    interrupted = "interrupted"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    skipped = "skipped"


class MessageStatus(StrEnum):
    draft = "draft"
    completed = "completed"
    interrupted = "interrupted"
    cancelled = "cancelled"


class SenderKind(StrEnum):
    human = "human"
    agent = "agent"
    system = "system"


class RoleCode(StrEnum):
    coach = "coach"
    cto = "cto"
    designer = "designer"
    ops = "ops"
    target_user = "target_user"
    moderator = "moderator"
    interviewer = "interviewer"


class MessageStage(StrEnum):
    clarify = "clarify"
    brainstorm = "brainstorm"
    audit = "audit"
    system = "system"


class ClaimKind(StrEnum):
    fact = "fact"
    hypothesis = "hypothesis"
    feature = "feature"
    risk = "risk"
    question = "question"
    insight = "insight"


class ClaimStatus(StrEnum):
    proposed = "proposed"
    accepted = "accepted"
    rejected = "rejected"
    superseded = "superseded"


class EvidenceKind(StrEnum):
    methodology = "methodology"
    benchmark = "benchmark"
    user_statement = "user_statement"
    user_research = "user_research"
    market_source = "market_source"
    experiment_result = "experiment_result"


class VerificationStatus(StrEnum):
    unverified = "unverified"
    source_checked = "source_checked"
    user_confirmed = "user_confirmed"


class DecisionStatus(StrEnum):
    proposed = "proposed"
    confirmed = "confirmed"
    rejected = "rejected"
    superseded = "superseded"


class ExperimentStatus(StrEnum):
    proposed = "proposed"
    planned = "planned"
    completed = "completed"


class IssueStatus(StrEnum):
    open = "open"
    resolved = "resolved"


class BriefFieldStatus(StrEnum):
    unknown = "unknown"
    proposed = "proposed"
    confirmed = "confirmed"


class BriefStatus(StrEnum):
    draft = "draft"
    ready = "ready"
    confirmed = "confirmed"
    skipped = "skipped"


class AuditStatus(StrEnum):
    created = "created"
    opening = "opening"
    active = "active"
    closing = "closing"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class Assessment(StrEnum):
    not_discussed = "not_discussed"
    awaiting_answer = "awaiting_answer"
    needs_clarification = "needs_clarification"
    supported = "supported"
    gap = "gap"
    not_applicable = "not_applicable"


class EndReason(StrEnum):
    completed = "completed"
    user_ended = "user_ended"
    cancelled = "cancelled"
    failed = "failed"
    legacy_unstructured = "legacy_unstructured"


class TargetRole(StrEnum):
    auto = "auto"
    all = "all"
    cto = "cto"
    designer = "designer"
    ops = "ops"
    target_user = "target_user"


# --------------------------------------------------------------------------- #
# Loop Studio（产品内闭环：设想 → 检验 → 开发 → 测试 → 评测）
# --------------------------------------------------------------------------- #


class LoopStatus(StrEnum):
    running = "running"
    awaiting_decision = "awaiting_decision"
    shipped = "shipped"
    stopped = "stopped"
    failed = "failed"


class LoopScope(StrEnum):
    mvp = "mvp"
    optimize = "optimize"


class LoopStage(StrEnum):
    ideate = "ideate"
    validate = "validate"
    develop = "develop"
    test = "test"
    evaluate = "evaluate"


LOOP_STAGES: tuple[LoopStage, ...] = (
    LoopStage.ideate,
    LoopStage.validate,
    LoopStage.develop,
    LoopStage.test,
    LoopStage.evaluate,
)


class LoopStageStatus(StrEnum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"
    skipped = "skipped"


class LoopRoundStatus(StrEnum):
    running = "running"
    done = "done"
    failed = "failed"


class LoopVerdict(StrEnum):
    pending = "pending"
    pass_ = "pass"
    iterate = "iterate"
    blocked = "blocked"


class LoopDecisionKind(StrEnum):
    iterate = "iterate"
    ship = "ship"
    stop = "stop"


class LoopArtifactKind(StrEnum):
    mvp_spec = "mvp_spec"
    validation = "validation"
    prototype = "prototype"
    test_report = "test_report"
    scorecard = "scorecard"
    #: 真实执行沙箱的一次运行记录（见 ``services/loop_sandbox.py``）——
    #: 它把「开发/测试」阶段从文本产出推进到真实进程执行，结果作为证据留档。
    sandbox_run = "sandbox_run"


class CheckResult(StrEnum):
    unknown = "unknown"
    pass_ = "pass"
    fail = "fail"


class TestCaseStatus(StrEnum):
    untested = "untested"
    passed = "passed"
    failed = "failed"
    skipped = "skipped"


class FeaturePriority(StrEnum):
    must = "must"
    should = "should"
    could = "could"


class EffortSize(StrEnum):
    s = "S"
    m = "M"
    l = "L"


class LoopScoreKey(StrEnum):
    functionality = "functionality"
    feasibility = "feasibility"
    user_value = "user_value"
    risk = "risk"
    delivery = "delivery"
    testability = "testability"
    docs = "docs"


LOOP_SCORE_LABELS: dict[LoopScoreKey, str] = {
    LoopScoreKey.functionality: "功能可用性",
    LoopScoreKey.feasibility: "技术可行性",
    LoopScoreKey.user_value: "用户价值",
    LoopScoreKey.risk: "风险与验证",
    LoopScoreKey.delivery: "交付完整度",
    LoopScoreKey.testability: "可测试性",
    LoopScoreKey.docs: "文档与交接",
}

LOOP_SCORE_MAX: dict[LoopScoreKey, int] = {
    LoopScoreKey.functionality: 25,
    LoopScoreKey.feasibility: 15,
    LoopScoreKey.user_value: 20,
    LoopScoreKey.risk: 15,
    LoopScoreKey.delivery: 10,
    LoopScoreKey.testability: 10,
    LoopScoreKey.docs: 5,
}

MAX_LOOP_ROUNDS = 5
MIN_LOOP_ROUNDS = 1
LOOP_ROUNDS_DEFAULT = 3
LOOP_TARGET_SCORE_DEFAULT = 85
LOOP_TARGET_SCORE_MIN = 60
LOOP_TARGET_SCORE_MAX = 100
MAX_LOOP_FEATURES = 12
MAX_LOOP_ASSUMPTIONS = 8
MAX_LOOP_FILES = 12
MAX_LOOP_FILE_CHARS = 6000
MAX_LOOP_CHECKS = 12
MAX_LOOP_CASES = 20
MAX_LOOP_DIMENSIONS = 7
MAX_LOOP_BLOCKERS = 8
LOOP_STAGE_OUTPUT_TOKENS = 1500
LOOP_FINAL_OUTPUT_TOKENS = 2200


class Action(StrEnum):
    clarify = "clarify"
    confirm_brief = "confirm_brief"
    skip_clarification = "skip_clarification"
    revise_brief = "revise_brief"
    add_evidence = "add_evidence"
    brainstorm = "brainstorm"
    #: 内部动作：由 brainstorm 成功后自动排入（主持人归并），不接受用户提交。
    synthesis = "synthesis"
    confirm_decision = "confirm_decision"
    start_audit = "start_audit"
    answer_audit = "answer_audit"
    correct_audit_assessment = "correct_audit_assessment"
    rebuild_legacy = "rebuild_legacy"
    end_audit = "end_audit"
    cancel_audit = "cancel_audit"
    start_loop = "start_loop"
    loop_decision = "loop_decision"
    #: 内部动作：一轮闭环执行（start_loop / loop_decision(iterate) 自动排入）。
    loop_round = "loop_round"


NORMAL_ACTIONS: frozenset[Action] = frozenset(
    {
        Action.clarify,
        Action.confirm_brief,
        Action.skip_clarification,
        Action.revise_brief,
        Action.add_evidence,
        Action.brainstorm,
        Action.confirm_decision,
        Action.start_audit,
        Action.answer_audit,
        Action.correct_audit_assessment,
        Action.rebuild_legacy,
        Action.start_loop,
        Action.loop_decision,
    }
)
CONTROL_ACTIONS: frozenset[Action] = frozenset({Action.end_audit, Action.cancel_audit})


class ControlStatus(StrEnum):
    accepted = "accepted"
    scheduled = "scheduled"
    applied = "applied"
    failed = "failed"


class EventType(StrEnum):
    run_queued = "run.queued"
    run_started = "run.started"
    step_started = "step.started"
    role_output_updated = "role.output.updated"
    role_completed = "role.completed"
    product_state_updated = "product_state.updated"
    audit_updated = "audit.updated"
    loop_updated = "loop.updated"
    run_recovered = "run.recovered"
    run_completed = "run.completed"
    run_failed = "run.failed"
    run_cancelled = "run.cancelled"


TERMINAL_EVENT_TYPES = frozenset({EventType.run_completed, EventType.run_failed, EventType.run_cancelled})


class TaskKind(StrEnum):
    clarify = "clarify"
    brainstorm = "brainstorm"
    synthesis = "synthesis"
    peer_review = "peer_review"
    audit_open = "audit_open"
    audit_answer = "audit_answer"
    audit_report = "audit_report"
    rebuild_legacy = "rebuild_legacy"
    loop_round = "loop_round"


class ContextSection(StrEnum):
    role_protocol = "role_protocol"
    product_definition = "product_definition"
    product_state = "product_state"
    evidence = "evidence"
    audit_question = "audit_question"
    conversation = "conversation"
    current_input = "current_input"


class OriginKind(StrEnum):
    model = "model"
    human = "human"
    system = "system"
    legacy = "legacy"


# --------------------------------------------------------------------------- #
# 产品定义（FR-01）
# --------------------------------------------------------------------------- #


BRIEF_FIELDS: tuple[str, ...] = (
    "target_users",
    "usage_scenario",
    "core_problem",
    "current_alternatives",
    "product_form",
    "constraints",
    "success_criteria",
)

# 缺少这些字段时不可展示"确认定义"卡片（FR-01）
BRIEF_REQUIRED_FIELDS: tuple[str, ...] = (
    "target_users",
    "usage_scenario",
    "core_problem",
    "success_criteria",
)


class BriefField(StrictModel):
    value: str | None = Field(default=None, max_length=2000)
    status: BriefFieldStatus = BriefFieldStatus.unknown
    source_message_ids: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)

    @model_validator(mode="after")
    def _unknown_has_no_value(self) -> "BriefField":
        if self.status == BriefFieldStatus.unknown and self.value:
            # 允许保留历史值，但 unknown 必须显式清空，避免"未确认却被当成已知"
            self.value = None
        return self


class ProductBrief(StrictModel):
    target_users: BriefField = Field(default_factory=BriefField)
    usage_scenario: BriefField = Field(default_factory=BriefField)
    core_problem: BriefField = Field(default_factory=BriefField)
    current_alternatives: BriefField = Field(default_factory=BriefField)
    product_form: BriefField = Field(default_factory=BriefField)
    constraints: BriefField = Field(default_factory=BriefField)
    success_criteria: BriefField = Field(default_factory=BriefField)
    status: BriefStatus = BriefStatus.draft
    skipped: bool = False

    def known(self, field: str) -> bool:
        item: BriefField = getattr(self, field)
        return bool(item.value) and item.status != BriefFieldStatus.unknown

    def confirmed(self, field: str) -> bool:
        item: BriefField = getattr(self, field)
        return item.status == BriefFieldStatus.confirmed and bool(item.value)

    def missing_fields(self) -> list[str]:
        return [f for f in BRIEF_FIELDS if not self.known(f)]

    def required_missing(self) -> list[str]:
        return [f for f in BRIEF_REQUIRED_FIELDS if not self.known(f)]

    def ready(self) -> bool:
        """可供用户确认（不代表事实已验证）。"""
        return not self.required_missing()

    def confirmed_fields(self) -> dict[str, str]:
        return {f: str(getattr(self, f).value) for f in BRIEF_FIELDS if self.confirmed(f)}


def empty_brief() -> ProductBrief:
    return ProductBrief()


# --------------------------------------------------------------------------- #
# ProductState 组成（§5.2）
# --------------------------------------------------------------------------- #


class Claim(StrictModel):
    id: str
    kind: ClaimKind
    text: str = Field(min_length=1, max_length=MAX_CLAIM_TEXT_CHARS)
    source_message_ids: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)
    evidence_refs: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)
    origin: OriginKind = OriginKind.model
    status: ClaimStatus = ClaimStatus.proposed
    supersedes_id: str | None = None
    created_product_version: int = 1
    confidence_level: Literal["low", "medium", "high"] | None = None
    role_code: RoleCode | None = None
    # AC-20：论断里在来源原文中找不到的数字（未验证数字），由 services/number_guard.py 标注。
    unverified_numbers: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)


class OptionItem(StrictModel):
    id: str
    title: str = Field(min_length=1, max_length=300)
    summary: str | None = Field(default=None, max_length=2000)
    claim_ids: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)
    tradeoffs: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)
    risk_ids: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)
    assumption_ids: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)
    experiment_ids: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)
    origin: OriginKind = OriginKind.model
    status: ClaimStatus = ClaimStatus.proposed


class DecisionItem(StrictModel):
    id: str
    option_id: str | None = None
    rationale: str = Field(default="", max_length=2000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)
    open_issue_ids: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)
    status: DecisionStatus = DecisionStatus.proposed
    needs_review: bool = False
    confirmed_by: str | None = None
    confirmed_at: str | None = None
    origin: OriginKind = OriginKind.model
    #: 生成该决策建议的 Run（Loop 轮次）；交付时据此把本闭环的 proposed 决策收口。
    source_run_id: str | None = None


class ExperimentItem(StrictModel):
    id: str
    hypothesis_claim_id: str | None = None
    method: str = Field(default="", max_length=2000)
    success_criterion: str = Field(default="", max_length=1000)
    needed_data: str | None = Field(default=None, max_length=1000)
    status: ExperimentStatus = ExperimentStatus.proposed
    result_evidence_ids: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)
    origin: OriginKind = OriginKind.model


class UnresolvedIssue(StrictModel):
    id: str
    kind: Literal["risk", "assumption", "question", "conflict", "gap"] = "question"
    text: str = Field(min_length=1, max_length=1000)
    related_ids: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)
    status: IssueStatus = IssueStatus.open
    dimension: str | None = None


class ProductState(StrictModel):
    """产品内容快照；版本只增长，旧版本保留（§5.3 第 5 条）。"""

    session_id: str
    version: int = Field(default=1, ge=1)
    schema_version: int = SCHEMA_VERSION
    brief: ProductBrief = Field(default_factory=empty_brief)
    claims: list[Claim] = Field(default_factory=list)
    options: list[OptionItem] = Field(default_factory=list)
    decisions: list[DecisionItem] = Field(default_factory=list)
    experiments: list[ExperimentItem] = Field(default_factory=list)
    unresolved_issues: list[UnresolvedIssue] = Field(default_factory=list)
    source_run_id: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)

    @field_validator("session_id")
    @classmethod
    def _check_session_id(cls, value: str) -> str:
        if not is_session_id(value):
            raise ValueError("session_id must be 32-char lowercase hex")
        return value

    # ---- 便捷访问 ---- #

    def claim(self, claim_id: str) -> Claim | None:
        return next((c for c in self.claims if c.id == claim_id), None)

    def open_issues(self) -> list[UnresolvedIssue]:
        return [i for i in self.unresolved_issues if i.status == IssueStatus.open]

    def claim_ids(self) -> set[str]:
        return {c.id for c in self.claims}

    def option_ids(self) -> set[str]:
        return {o.id for o in self.options}

    def experiment_ids(self) -> set[str]:
        return {e.id for e in self.experiments}

    def decision_ids(self) -> set[str]:
        return {d.id for d in self.decisions}


def initial_product_state(session_id: str, *, brief: ProductBrief | None = None, source_run_id: str | None = None) -> ProductState:
    return ProductState(
        session_id=session_id,
        version=1,
        brief=brief or empty_brief(),
        source_run_id=source_run_id,
    )


# --------------------------------------------------------------------------- #
# 证据（FR-04A）
# --------------------------------------------------------------------------- #


class Evidence(StrictModel):
    id: str
    session_id: str | None = None  # 为空表示公开知识域
    kind: EvidenceKind
    source_uri: str | None = Field(default=None, max_length=1000)
    title: str = Field(default="", max_length=300)
    excerpt: str = Field(default="", max_length=MAX_EVIDENCE_EXCERPT_CHARS)
    content_hash: str = ""
    source_date: str | None = None
    retrieved_at: str = Field(default_factory=utc_now_iso)
    verification_status: VerificationStatus = VerificationStatus.unverified
    limitations: str | None = Field(default=None, max_length=1000)
    created_by: str | None = None


class RetrievalHit(StrictModel):
    evidence_id: str
    source_uri: str | None = None
    title: str = ""
    heading: str | None = None
    excerpt: str = ""
    score: float = 0.0
    category: str | None = None
    source_date: str | None = None
    content_hash: str = ""
    retrieved_at: str = Field(default_factory=utc_now_iso)


# --------------------------------------------------------------------------- #
# 角色结构化产物（FR-04C）与主持人结果（FR-04D）
# --------------------------------------------------------------------------- #


class ConfidenceLevel(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"


class CandidateClaim(StrictModel):
    local_id: str = Field(min_length=1, max_length=MAX_LOCAL_ID_CHARS)
    kind: ClaimKind
    text: str = Field(min_length=1, max_length=MAX_CLAIM_TEXT_CHARS)
    evidence_refs: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)
    source_message_ids: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)
    confidence_level: ConfidenceLevel = ConfidenceLevel.medium
    confidence_reason: str = Field(default="", max_length=300)
    # AC-20：服务端在归并前标注的「来源原文里找不到的数字」（模型不填，由 number_guard 写）。
    unverified_numbers: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)


class CandidateOption(StrictModel):
    local_id: str = Field(min_length=1, max_length=MAX_LOCAL_ID_CHARS)
    title: str = Field(min_length=1, max_length=300)
    summary: str | None = Field(default=None, max_length=2000)
    claim_local_ids: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)
    tradeoffs: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)
    risk_local_ids: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)
    assumption_local_ids: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)
    experiment_local_ids: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)
    existing_claim_ids: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)
    existing_risk_ids: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)


class CandidateExperiment(StrictModel):
    local_id: str = Field(min_length=1, max_length=MAX_LOCAL_ID_CHARS)
    hypothesis_local_id: str | None = Field(default=None, max_length=MAX_LOCAL_ID_CHARS)
    method: str = Field(default="", max_length=2000)
    success_criterion: str = Field(default="", max_length=1000)
    needed_data: str | None = Field(default=None, max_length=1000)


class CriticalIssue(StrictModel):
    text: str = Field(min_length=1, max_length=MAX_CLAIM_TEXT_CHARS)
    severity: Literal["low", "medium", "high"] = "medium"
    dimension: str | None = None


class Uncertainty(StrictModel):
    text: str = Field(min_length=1, max_length=MAX_CLAIM_TEXT_CHARS)
    kind: Literal["assumption", "question", "data_gap"] = "assumption"


class RoleResult(StrictModel):
    """单个专家的一次结构化输出。"""

    schema_version: int = SCHEMA_VERSION
    role_code: RoleCode
    display_text: str = Field(default="", max_length=MAX_DISPLAY_TEXT_CHARS)
    claims: list[CandidateClaim] = Field(default_factory=list, max_length=MAX_CLAIMS_PER_ROLE_RESULT)
    option_proposals: list[CandidateOption] = Field(default_factory=list, max_length=MAX_OPTIONS_PER_ROLE_RESULT)
    critical_issues: list[CriticalIssue] = Field(default_factory=list, max_length=MAX_CRITICAL_ISSUES)
    uncertainties: list[Uncertainty] = Field(default_factory=list, max_length=MAX_CRITICAL_ISSUES)
    suggested_experiments: list[CandidateExperiment] = Field(
        default_factory=list, max_length=MAX_EXPERIMENTS_PER_ROLE_RESULT
    )
    # 模拟观点标识：target_user 必须为 True（FR-04C）
    simulated_perspective: bool = False

    @model_validator(mode="after")
    def _unique_local_ids(self) -> "RoleResult":
        seen: set[str] = set()
        for group in (self.claims, self.option_proposals, self.suggested_experiments):
            for item in group:
                if item.local_id in seen:
                    raise ValueError(f"duplicate local_id: {item.local_id}")
                seen.add(item.local_id)
        return self

    @model_validator(mode="after")
    def _check_local_refs(self) -> "RoleResult":
        local = {c.local_id for c in self.claims} | {e.local_id for e in self.suggested_experiments}
        for opt in self.option_proposals:
            for ref in opt.claim_local_ids + opt.risk_local_ids + opt.assumption_local_ids + opt.experiment_local_ids:
                if ref not in local:
                    raise ValueError(f"unknown local ref: {ref}")
        for exp in self.suggested_experiments:
            if exp.hypothesis_local_id and exp.hypothesis_local_id not in {c.local_id for c in self.claims}:
                raise ValueError(f"unknown hypothesis ref: {exp.hypothesis_local_id}")
        return self


class SynthesisConflict(StrictModel):
    topic: str = Field(min_length=1, max_length=300)
    claim_ids: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)
    impact: str = Field(default="", max_length=1000)
    needed_evidence: str = Field(default="", max_length=1000)
    role_codes: list[RoleCode] = Field(default_factory=list, max_length=4)


class DecisionProposal(StrictModel):
    option_id: str | None = None
    rationale: str = Field(default="", max_length=2000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)


class SynthesisResult(StrictModel):
    schema_version: int = SCHEMA_VERSION
    consensus_claim_ids: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM * 2)
    conflicts: list[SynthesisConflict] = Field(default_factory=list, max_length=2)
    recommended_option_id: str | None = None
    alternative_option_ids: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)
    decision_proposals: list[DecisionProposal] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)
    unresolved_issue_ids: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM * 2)
    next_experiment_ids: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)
    limitations: str = Field(default="", max_length=2000)
    degraded: bool = False  # 主持人失败时的确定性整理结果


# --------------------------------------------------------------------------- #
# 上下文（FR-02）
# --------------------------------------------------------------------------- #


class LLMMessage(StrictModel):
    role: Literal["system", "user", "assistant"]
    content: str
    source_label: str | None = None


class ContextBundle(StrictModel):
    messages: list[LLMMessage] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    source_message_ids: list[str] = Field(default_factory=list)
    estimated_input_tokens: int = 0
    included_sections: list[ContextSection] = Field(default_factory=list)
    omitted_sections: list[ContextSection] = Field(default_factory=list)
    prompt_version: str = "v1"
    role_code: RoleCode | None = None
    product_version: int = 1
    history_cutoff_seq: int = 0
    notes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# 画布（FR-05）
# --------------------------------------------------------------------------- #


class CanvasNode(StrictModel):
    id: str
    kind: Literal["problem", "user", "option", "risk", "assumption", "question", "experiment", "decision", "claim"]
    title: str = Field(default="", max_length=300)
    detail: str = Field(default="", max_length=2000)
    status: str = "proposed"
    claim_ids: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)
    option_ids: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)
    decision_ids: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)
    experiment_ids: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)
    source_message_ids: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)
    evidence_refs: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)
    model_speculation: bool = False
    # AC-20：来源原文里找不到的数字 + 给用户的验证建议（补来源与统计日期）。
    unverified_numbers: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)
    verification_suggestion: str | None = Field(default=None, max_length=600)


class CanvasBranch(StrictModel):
    key: str
    title: str
    nodes: list[CanvasNode] = Field(default_factory=list)


class CanvasProjection(StrictModel):
    session_id: str
    product_version: int
    schema_version: int = SCHEMA_VERSION
    root: CanvasNode | None = None
    branches: list[CanvasBranch] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# 审计（FR-07）
# --------------------------------------------------------------------------- #


class DimensionState(StrictModel):
    key: str
    asked_question_ids: list[str] = Field(default_factory=list)
    answered_question_ids: list[str] = Field(default_factory=list)
    assessment: Assessment = Assessment.not_discussed
    rationale: str = Field(default="", max_length=2000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)
    open_issue_ids: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)

    @field_validator("key")
    @classmethod
    def _check_dimension(cls, value: str) -> str:
        if value not in AUDIT_DIMENSIONS:
            raise ValueError(f"unknown dimension: {value}")
        return value


class AuditQuestion(StrictModel):
    id: str
    audit_id: str
    ordinal: int = Field(ge=1, le=AUDIT_QUESTION_HARD_LIMIT)
    dimension: str
    issue_refs: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)
    question_text: str = Field(default="", max_length=1000)
    question_message_id: str | None = None
    answer_message_id: str | None = None
    assessment: Assessment = Assessment.awaiting_answer
    rationale: str = Field(default="", max_length=2000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)


class AuditReport(StrictModel):
    audit_id: str
    product_version: int
    audit_version: int = 1
    end_reason: EndReason = EndReason.completed
    question_count: int = Field(default=0, ge=0, le=AUDIT_QUESTION_HARD_LIMIT)
    dimension_states: list[DimensionState] = Field(default_factory=list)
    supported_points: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM * 2)
    unresolved_gaps: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM * 2)
    recommended_experiments: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)
    limitations: str = Field(default="", max_length=2000)
    #: 一段话总结（AC-36/AC-40）。模型路径用模型给的摘要，兜底路径由结构化记录拼出，
    #: 前端在「报告摘要」区展示；缺失时为空串，页面显示「未生成」而不是编内容。
    summary: str = Field(default="", max_length=4000)
    #: AC-20/AC-33 延伸：报告正文里在来源（消息/证据）找不到的数字，由服务端标注，
    #: 前端在报告顶部给出「数字未验证」徽标与补充建议；为空表示报告里的数字都可追溯。
    unverified_numbers: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)
    verification_suggestion: str | None = Field(default=None, max_length=600)
    human_revised: bool = False
    generated_by: Literal["model", "fallback"] = "model"


class AuditSnapshot(StrictModel):
    id: str
    session_id: str
    product_version: int
    audit_version: int = 1
    status: AuditStatus = AuditStatus.created
    question_count: int = 0
    end_reason: EndReason | None = None
    active_question_id: str | None = None
    pending_continuation: bool = False
    close_requested: bool = False
    closing_control_id: str | None = None
    dimension_states: list[DimensionState] = Field(default_factory=list)
    # 审计问题列表随快照一并返回，前端无需为「第 N 问/历史问答」再发一次请求（FR-07）。
    questions: list[AuditQuestion] = Field(default_factory=list)
    report: AuditReport | None = None
    #: 最近一次失败 Run 的错误码/文案：审计失败时必须让用户看见原因（PRD-03）。
    last_error_code: str | None = None
    last_error_message: str | None = None
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)


# --------------------------------------------------------------------------- #
# 命令与回执（FR-03 / §7.2）
# --------------------------------------------------------------------------- #


class ClarifyPayload(StrictModel):
    content: str = Field(min_length=1, max_length=MAX_USER_INPUT_CHARS)


class ConfirmBriefPayload(StrictModel):
    expected_product_version: int = Field(ge=1)
    fields: dict[str, str] = Field(default_factory=dict)


class SkipClarificationPayload(StrictModel):
    reason: str | None = Field(default=None, max_length=500)


class BriefFieldUpdate(StrictModel):
    value: str | None = None
    status: BriefFieldStatus = BriefFieldStatus.proposed


class ReviseBriefPayload(StrictModel):
    expected_product_version: int = Field(ge=1)
    fields: dict[str, BriefFieldUpdate] = Field(min_length=1)
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("fields")
    @classmethod
    def _known_fields(cls, value: dict[str, BriefFieldUpdate]) -> dict[str, BriefFieldUpdate]:
        unknown = [k for k in value if k not in BRIEF_FIELDS]
        if unknown:
            raise ValueError(f"unknown brief fields: {unknown}")
        return value


class SessionSharePayload(StrictModel):
    """共享会话：把「查看」或「可提交命令」角色授予另一个账号。"""

    email: str = Field(min_length=3, max_length=320)
    role: Literal["viewer", "editor"] = "viewer"


class GroupMemberPayload(StrictModel):
    """成员组里的一行：邮箱 + 角色（与单个会话共享同样的两种角色）。"""

    email: str = Field(min_length=3, max_length=320)
    role: Literal["viewer", "editor"] = "viewer"


class CreateGroupPayload(StrictModel):
    """新建成员组：名字在同一个所有者名下唯一。"""

    name: str = Field(min_length=1, max_length=80)


class MemberGroupMemberView(StrictModel):
    email: str
    role: Literal["viewer", "editor"] = "viewer"
    created_at: str = ""


class MemberGroupView(StrictModel):
    id: str
    name: str
    owner_email: str = ""
    members: list[MemberGroupMemberView] = Field(default_factory=list)
    created_at: str = ""


class MemberGroupListPage(StrictModel):
    items: list[MemberGroupView] = Field(default_factory=list)


class ApplyGroupPayload(StrictModel):
    """把某个成员组一次性应用到会话。"""

    group_id: str = Field(min_length=8, max_length=64)


class ApplyGroupReceipt(StrictModel):
    """把成员组一次性应用到某个会话：新增 / 已是成员（跳过）/ 组内总人数。"""

    group_id: str
    added: int = 0
    already_shared: int = 0
    members_total: int = 0


class LlmConfigPayload(StrictModel):
    """用户提交自己的模型 API 配置（BYOK）。

    只要 base_url 指向 OpenAI 兼容接口即可（DeepSeek、OpenAI、各类中转站均可）。
    api_key 落库前用 ``core.crypto`` 加密，接口永远只回显末 4 位。
    """

    base_url: str = Field(min_length=8, max_length=512)
    model: str = Field(min_length=1, max_length=128)
    api_key: str = Field(min_length=8, max_length=512)
    disable_reasoning: bool = True

    @field_validator("base_url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        text = value.strip().rstrip("/")
        if not (text.startswith("http://") or text.startswith("https://")):
            raise ValueError("base_url 必须以 http:// 或 https:// 开头")
        return text


class AddEvidencePayload(StrictModel):
    kind: EvidenceKind
    title: str = Field(default="", max_length=300)
    excerpt: str = Field(min_length=1, max_length=MAX_EVIDENCE_EXCERPT_CHARS)
    source_uri: str | None = Field(default=None, max_length=1000)
    source_date: str | None = Field(default=None, max_length=64)
    limitations: str | None = Field(default=None, max_length=1000)


class BrainstormPayload(StrictModel):
    content: str = Field(min_length=1, max_length=MAX_USER_INPUT_CHARS)
    target: TargetRole = TargetRole.auto


class ConfirmDecisionPayload(StrictModel):
    expected_product_version: int = Field(ge=1)
    decision_id: str = Field(min_length=1, max_length=64)
    option_id: str | None = Field(default=None, max_length=64)
    rationale: str | None = Field(default=None, max_length=2000)


class StartAuditPayload(StrictModel):
    expected_product_version: int | None = Field(default=None, ge=1)


class AnswerAuditPayload(StrictModel):
    audit_id: str = Field(min_length=1, max_length=64)
    question_id: str = Field(min_length=1, max_length=64)
    answer: str = Field(min_length=1, max_length=MAX_USER_INPUT_CHARS)


class CorrectAssessmentPayload(StrictModel):
    audit_id: str = Field(min_length=1, max_length=64)
    expected_audit_version: int = Field(ge=1)
    dimension: str | None = None
    question_id: str | None = Field(default=None, max_length=64)
    assessment: Assessment
    rationale: str = Field(min_length=1, max_length=2000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)

    @model_validator(mode="after")
    def _target_present(self) -> "CorrectAssessmentPayload":
        if not self.dimension and not self.question_id:
            raise ValueError("dimension 或 question_id 至少提供一个")
        if self.dimension and self.dimension not in AUDIT_DIMENSIONS:
            raise ValueError(f"unknown dimension: {self.dimension}")
        return self


class RebuildLegacyPayload(StrictModel):
    expected_product_version: int | None = Field(default=None, ge=1)


class EndAuditPayload(StrictModel):
    audit_id: str = Field(min_length=1, max_length=64)


class CancelAuditPayload(StrictModel):
    audit_id: str = Field(min_length=1, max_length=64)


# --------------------------------------------------------------------------- #
# Loop 模型
# --------------------------------------------------------------------------- #


class LoopAssumption(StrictModel):
    id: str = Field(min_length=1, max_length=MAX_LOCAL_ID_CHARS)
    statement: str = Field(min_length=1, max_length=600)
    risk: str = Field(default="", max_length=600)
    verification: str = Field(min_length=1, max_length=600)


class MvpFeature(StrictModel):
    id: str = Field(min_length=1, max_length=MAX_LOCAL_ID_CHARS)
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=1200)
    acceptance: str = Field(min_length=1, max_length=600)
    priority: FeaturePriority = FeaturePriority.should
    effort: EffortSize = EffortSize.m


class LoopMvpSpec(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=1200)
    features: list[MvpFeature] = Field(default_factory=list, max_length=MAX_LOOP_FEATURES)
    assumptions: list[LoopAssumption] = Field(default_factory=list, max_length=MAX_LOOP_ASSUMPTIONS)
    not_in_scope: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def _features_present(self) -> "LoopMvpSpec":
        if not self.features:
            raise ValueError("至少需要一个 MVP 功能")
        ids = [f.id for f in self.features]
        if len(set(ids)) != len(ids):
            raise ValueError("feature id 必须唯一")
        return self


class ValidationCheck(StrictModel):
    id: str = Field(min_length=1, max_length=MAX_LOCAL_ID_CHARS)
    feature_id: str | None = Field(default=None, max_length=MAX_LOCAL_ID_CHARS)
    question: str = Field(min_length=1, max_length=600)
    result: CheckResult = CheckResult.unknown
    evidence_refs: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)
    notes: str = Field(default="", max_length=600)


class LoopValidation(StrictModel):
    checks: list[ValidationCheck] = Field(default_factory=list, max_length=MAX_LOOP_CHECKS)
    summary: str = Field(default="", max_length=1200)
    unknowns: list[str] = Field(default_factory=list, max_length=8)


class PrototypeFile(StrictModel):
    path: str = Field(min_length=1, max_length=200)
    language: str = Field(default="text", max_length=32)
    content: str = Field(min_length=1, max_length=MAX_LOOP_FILE_CHARS)
    purpose: str = Field(default="", max_length=300)


class PrototypeSpec(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=1200)
    entry_file: str = Field(min_length=1, max_length=200)
    run_command: str = Field(default="", max_length=300)
    files: list[PrototypeFile] = Field(default_factory=list, max_length=MAX_LOOP_FILES)
    limitations: str = Field(default="", max_length=600)
    requires_human_build: bool = True


class LoopTestCase(StrictModel):
    id: str = Field(min_length=1, max_length=MAX_LOCAL_ID_CHARS)
    feature_id: str | None = Field(default=None, max_length=MAX_LOCAL_ID_CHARS)
    title: str = Field(min_length=1, max_length=200)
    steps: list[str] = Field(default_factory=list, max_length=6)
    expected: str = Field(min_length=1, max_length=600)
    status: TestCaseStatus = TestCaseStatus.untested
    evidence: str = Field(default="", max_length=600)


class LoopTestReport(StrictModel):
    total: int = Field(default=0, ge=0, le=MAX_LOOP_CASES)
    passed: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)
    simulated: bool = True
    cases: list[LoopTestCase] = Field(default_factory=list, max_length=MAX_LOOP_CASES)
    notes: str = Field(default="", max_length=1200)

    @model_validator(mode="after")
    def _counts_match(self) -> "LoopTestReport":
        counted = len(self.cases)
        if counted and self.total != counted:
            self.total = counted
        return self


class LoopScoreDimension(StrictModel):
    key: LoopScoreKey
    score: int = Field(ge=0, le=100)
    max: int = Field(ge=1, le=100)
    reason: str = Field(default="", max_length=600)


class LoopScoreCard(StrictModel):
    total: int = Field(ge=0, le=100)
    dimensions: list[LoopScoreDimension] = Field(default_factory=list, max_length=MAX_LOOP_DIMENSIONS)
    verdict: LoopVerdict = LoopVerdict.pending
    blockers: list[str] = Field(default_factory=list, max_length=MAX_LOOP_BLOCKERS)
    highlights: list[str] = Field(default_factory=list, max_length=8)
    next_actions: list[str] = Field(default_factory=list, max_length=6)
    generated_by: str = Field(default="model", pattern="^(model|harness)$")
    hard_capped: bool = False


class LoopStageView(StrictModel):
    stage: LoopStage
    status: LoopStageStatus = LoopStageStatus.pending
    summary: str = ""
    error_code: str | None = None
    updated_at: str = ""


class LoopArtifactView(StrictModel):
    id: str
    kind: LoopArtifactKind
    title: str
    size_bytes: int = 0
    created_at: str = ""


#: 沙箱运行结果的稳定枚举：前端按 status 分支，不解析文案。
class SandboxStatus(StrEnum):
    passed = "passed"
    failed = "failed"
    timeout = "timeout"


class SandboxRunPayload(StrictModel):
    """一次沙箱执行的请求：``command`` 是 argv 数组（不经 shell，避免注入）。"""

    command: list[str] = Field(min_length=1, max_length=12)
    timeout_seconds: int | None = Field(default=None, ge=1, le=60)


class SandboxRunReceipt(StrictModel):
    """沙箱执行结果（同时作为 ``sandbox_run`` 产物负载的公共字段集合）。"""

    id: str
    loop_id: str
    status: SandboxStatus
    command_display: str
    exit_code: int | None = None
    duration_ms: int = 0
    timeout_seconds: int = 0
    stdout_tail: str = ""
    stderr_tail: str = ""
    truncated: bool = False
    created_at: str = ""


class SandboxRunListPage(StrictModel):
    items: list[SandboxRunReceipt] = Field(default_factory=list)
    next_cursor: str | None = None


class SandboxStatusView(StrictModel):
    """沙箱能力开关视图：前端据此决定按钮可用性与提示文案。"""

    enabled: bool = False
    allowed_commands: list[str] = Field(default_factory=list)
    timeout_seconds: int = 0
    max_output_bytes: int = 0
    note: str = ""


class LoopRoundView(StrictModel):
    id: str
    ordinal: int = Field(ge=1)
    status: LoopRoundStatus = LoopRoundStatus.running
    score: int | None = None
    verdict: LoopVerdict = LoopVerdict.pending
    stages: list[LoopStageView] = Field(default_factory=list)
    artifacts: list[LoopArtifactView] = Field(default_factory=list)
    started_at: str = ""
    finished_at: str | None = None


class LoopSnapshot(StrictModel):
    loop_id: str
    session_id: str
    owner_email: str = ""
    status: LoopStatus = LoopStatus.running
    scope: LoopScope = LoopScope.mvp
    current_round: int = 1
    max_rounds: int = LOOP_ROUNDS_DEFAULT
    target_score: int = LOOP_TARGET_SCORE_DEFAULT
    loop_version: int = 1
    focus: str = ""
    rounds: list[LoopRoundView] = Field(default_factory=list)
    mvp_spec: LoopMvpSpec | None = None
    validation: LoopValidation | None = None
    prototype: PrototypeSpec | None = None
    test_report: LoopTestReport | None = None
    scorecard: LoopScoreCard | None = None
    latest_run_id: str | None = None
    decision: LoopDecisionKind | None = None
    decision_note: str = ""
    closed_at: str | None = None
    created_at: str = ""
    updated_at: str = ""


class LoopUpdatedPayload(StrictModel):
    """``loop.updated`` 事件负载：足够前端就地刷新，不替代 GET /loops/{id}。"""

    loop_id: str
    status: LoopStatus = LoopStatus.running
    current_round: int = 1
    loop_version: int = 1
    stage: LoopStage | None = None
    stage_status: LoopStageStatus | None = None
    round_status: LoopRoundStatus | None = None
    score: int | None = None
    verdict: LoopVerdict | None = None
    artifact_ids: list[str] = Field(default_factory=list, max_length=8)


class LoopAck(StrictModel):
    loop_id: str
    status: LoopStatus
    loop_version: int
    current_round: int
    run_id: str | None = None
    reused: bool = False


class StartLoopPayload(StrictModel):
    scope: LoopScope = LoopScope.mvp
    max_rounds: int = Field(default=LOOP_ROUNDS_DEFAULT, ge=MIN_LOOP_ROUNDS, le=MAX_LOOP_ROUNDS)
    target_score: int = Field(default=LOOP_TARGET_SCORE_DEFAULT, ge=LOOP_TARGET_SCORE_MIN, le=LOOP_TARGET_SCORE_MAX)
    focus: str = Field(default="", max_length=2000)
    expected_product_version: int | None = Field(default=None, ge=1)


class LoopDecisionPayload(StrictModel):
    loop_id: str = Field(min_length=1, max_length=64)
    expected_loop_version: int = Field(ge=1)
    decision: LoopDecisionKind
    note: str = Field(default="", max_length=2000)


PAYLOAD_MODELS: dict[Action, type[StrictModel]] = {
    Action.clarify: ClarifyPayload,
    Action.confirm_brief: ConfirmBriefPayload,
    Action.skip_clarification: SkipClarificationPayload,
    Action.revise_brief: ReviseBriefPayload,
    Action.add_evidence: AddEvidencePayload,
    Action.brainstorm: BrainstormPayload,
    Action.confirm_decision: ConfirmDecisionPayload,
    Action.start_audit: StartAuditPayload,
    Action.answer_audit: AnswerAuditPayload,
    Action.correct_audit_assessment: CorrectAssessmentPayload,
    Action.rebuild_legacy: RebuildLegacyPayload,
    Action.end_audit: EndAuditPayload,
    Action.cancel_audit: CancelAuditPayload,
    Action.start_loop: StartLoopPayload,
    Action.loop_decision: LoopDecisionPayload,
}


class CommandRequest(StrictModel):
    """POST /api/v2/sessions/{sid}/commands 的请求体。"""

    request_id: str = Field(min_length=8, max_length=64)
    action: Action
    expected_session_version: int | None = Field(default=None, ge=1)
    payload: dict[str, Any] = Field(default_factory=dict)


class ParsedCommand(StrictModel):
    request_id: str
    action: Action
    expected_session_version: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    body: StrictModel | None = None
    input_hash: str = ""
    resource_scope: str = ""


def parse_command(action: Action, request_id: str, payload: dict[str, Any], expected_session_version: int | None, resource_scope: str) -> ParsedCommand:
    model = PAYLOAD_MODELS.get(action)
    if model is None:
        raise ValueError(f"no payload model for action {action}")
    body = model.model_validate(payload)
    dumped = body.model_dump(mode="json")
    return ParsedCommand(
        request_id=request_id,
        action=action,
        expected_session_version=expected_session_version,
        payload=dumped,
        body=body,
        input_hash=input_hash(str(action), resource_scope, dumped, expected_session_version),
        resource_scope=resource_scope,
    )


class RunReceipt(StrictModel):
    run_id: str
    session_id: str
    action: Action
    status: RunStatus
    session_version: int
    base_product_version: int
    events_url: str
    reused: bool = False
    audit_id: str | None = None
    loop_id: str | None = None


class ControlReceipt(StrictModel):
    control_id: str
    audit_id: str
    status: ControlStatus
    session_version: int
    related_run_id: str | None = None
    reused: bool = False
    action: Action | None = None


class StartAuditRecovery(StrictModel):
    """已有 opening/active/closing 审计时的 200 恢复回执。"""

    audit_id: str
    reused: bool = True
    status: AuditStatus
    current_run_id: str | None = None
    latest_run_id: str | None = None
    session_version: int


class RunSnapshot(StrictModel):
    run_id: str
    session_id: str
    action: Action
    status: RunStatus
    base_session_version: int = 0
    base_product_version: int = 1
    result_product_version: int | None = None
    steps: list[dict[str, Any]] = Field(default_factory=list)
    messages: list[dict[str, Any]] = Field(default_factory=list)
    session_version: int = 1
    product_version: int = 1
    canvas: CanvasProjection | None = None
    audit: AuditSnapshot | None = None
    loop: LoopSnapshot | None = None
    loop_id: str | None = None
    snapshot_event_seq: int = 0
    latest_event_seq: int = 0


# --------------------------------------------------------------------------- #
# 事件（FR-06）
# --------------------------------------------------------------------------- #


class RunEvent(StrictModel):
    schema_version: int = SCHEMA_VERSION
    session_id: str
    run_id: str
    event_seq: int = Field(ge=1)
    type: EventType
    occurred_at: str = Field(default_factory=utc_now_iso)
    payload: dict[str, Any] = Field(default_factory=dict)

    def to_sse_data(self) -> str:
        return canonical_json(self.model_dump(mode="json"))

    @property
    def is_terminal(self) -> bool:
        return self.type in TERMINAL_EVENT_TYPES


# --------------------------------------------------------------------------- #
# 会话与消息视图
# --------------------------------------------------------------------------- #


class SessionView(StrictModel):
    id: str
    owner_email: str
    phase: SessionPhase
    session_version: int = 1
    current_product_version: int = 1
    current_audit_id: str | None = None
    active_run_id: str | None = None
    problem_statement: str = ""
    brief_status: BriefStatus = BriefStatus.draft
    current_loop_id: str | None = None
    created_at: str = ""
    updated_at: str = ""
    deleted_at: str | None = None
    message_count: int = 0


class MessageView(StrictModel):
    id: str
    session_id: str
    seq: int
    sender_kind: SenderKind
    role_code: RoleCode | None = None
    stage: MessageStage = MessageStage.clarify
    content: str = ""
    status: MessageStatus = MessageStatus.completed
    run_id: str | None = None
    audit_id: str | None = None
    created_at: str = ""


class RunView(StrictModel):
    id: str
    session_id: str
    action: Action
    status: RunStatus
    request_id: str
    base_session_version: int = 0
    base_product_version: int = 1
    result_product_version: int | None = None
    lease_owner: str | None = None
    lease_epoch: int = 0
    lease_expires_at: int | None = None
    parent_run_id: str | None = None
    audit_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    deadline_at: int | None = None


# --------------------------------------------------------------------------- #
# 内部步骤结果
# --------------------------------------------------------------------------- #


class StepOutcome(StrictModel):
    """runner 与 workflow 之间的统一返回值。"""

    status: StepStatus = StepStatus.succeeded
    output: dict[str, Any] = Field(default_factory=dict)
    messages: list[dict[str, Any]] = Field(default_factory=list)
    product_state: ProductState | None = None
    events: list[dict[str, Any]] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    upstream_calls: int = 0
    output_tokens: int = 0
    limitation: str | None = None


__all__ = [name for name in dir() if not name.startswith("_")]