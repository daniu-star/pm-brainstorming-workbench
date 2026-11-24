/**
 * M1 手写镜像 — 字段来源 `backend/domain/contracts.py`
 *
 * 说明：
 * - M1 不做自动 codegen（见 `docs/v4-m1-interfaces.md` §6），本文件是**手写**的
 *   前端类型镜像，任何后端契约变更都必须同步修改本文件。
 * - 枚举一律使用「字符串字面量联合 + 同名 const 数组」的形式：
 *   TS 里可用作类型，数组用于运行时遍历 / 校验。
 * - 后端 `StrictModel` 会拒绝额外字段（extra="forbid"），因此这里的字段集合
 *   必须与 backend 完全一致；可选字段用 `?` / `| null` 表达。
 * - 时间：后端返回 UTC ISO 字符串（如 `2026-11-18T03:00:00Z`）；
 *   租约 / 截止时间是 epoch 秒整数。
 * - ID：32 位小写 hex（`SESSION_ID_RE`）。
 */

/** backend/domain/contracts.py: SCHEMA_VERSION */
export const SCHEMA_VERSION = 1;

// --------------------------------------------------------------------------- //
// 枚举（PRD §5.4）
// --------------------------------------------------------------------------- //

export const SESSION_PHASES = ["clarifying", "brainstorming", "auditing", "audit_completed"] as const;
export type SessionPhase = (typeof SESSION_PHASES)[number];

export const RUN_STATUSES = [
  "queued",
  "running",
  "interrupted",
  "cancel_requested",
  "succeeded",
  "partial_succeeded",
  "failed",
  "cancelled",
] as const;
export type RunStatus = (typeof RUN_STATUSES)[number];
export const TERMINAL_RUN_STATUSES: readonly RunStatus[] = [
  "succeeded",
  "partial_succeeded",
  "failed",
  "cancelled",
];
export const ACTIVE_RUN_STATUSES: readonly RunStatus[] = [
  "queued",
  "running",
  "interrupted",
  "cancel_requested",
];

export const STEP_STATUSES = [
  "pending",
  "running",
  "interrupted",
  "succeeded",
  "failed",
  "cancelled",
  "skipped",
] as const;
export type StepStatus = (typeof STEP_STATUSES)[number];

export const MESSAGE_STATUSES = ["draft", "completed", "interrupted", "cancelled"] as const;
export type MessageStatus = (typeof MESSAGE_STATUSES)[number];

export const SENDER_KINDS = ["human", "agent", "system"] as const;
export type SenderKind = (typeof SENDER_KINDS)[number];

export const ROLE_CODES = ["coach", "cto", "designer", "ops", "target_user", "moderator", "interviewer"] as const;
export type RoleCode = (typeof ROLE_CODES)[number];

export const MESSAGE_STAGES = ["clarify", "brainstorm", "audit", "system"] as const;
export type MessageStage = (typeof MESSAGE_STAGES)[number];

export const CLAIM_KINDS = ["fact", "hypothesis", "feature", "risk", "question", "insight"] as const;
export type ClaimKind = (typeof CLAIM_KINDS)[number];

export const CLAIM_STATUSES = ["proposed", "accepted", "rejected", "superseded"] as const;
export type ClaimStatus = (typeof CLAIM_STATUSES)[number];

export const EVIDENCE_KINDS = [
  "methodology",
  "benchmark",
  "user_statement",
  "user_research",
  "market_source",
  "experiment_result",
] as const;
export type EvidenceKind = (typeof EVIDENCE_KINDS)[number];

export const VERIFICATION_STATUSES = ["unverified", "source_checked", "user_confirmed"] as const;
export type VerificationStatus = (typeof VERIFICATION_STATUSES)[number];

export const DECISION_STATUSES = ["proposed", "confirmed", "rejected", "superseded"] as const;
export type DecisionStatus = (typeof DECISION_STATUSES)[number];

export const EXPERIMENT_STATUSES = ["proposed", "planned", "completed"] as const;
export type ExperimentStatus = (typeof EXPERIMENT_STATUSES)[number];

export const ISSUE_STATUSES = ["open", "resolved"] as const;
export type IssueStatus = (typeof ISSUE_STATUSES)[number];

export const BRIEF_FIELD_STATUSES = ["unknown", "proposed", "confirmed"] as const;
export type BriefFieldStatus = (typeof BRIEF_FIELD_STATUSES)[number];

export const BRIEF_STATUSES = ["draft", "ready", "confirmed", "skipped"] as const;
export type BriefStatus = (typeof BRIEF_STATUSES)[number];

export const AUDIT_STATUSES = [
  "created",
  "opening",
  "active",
  "closing",
  "completed",
  "failed",
  "cancelled",
] as const;
export type AuditStatus = (typeof AUDIT_STATUSES)[number];

export const ASSESSMENTS = [
  "not_discussed",
  "awaiting_answer",
  "needs_clarification",
  "supported",
  "gap",
  "not_applicable",
] as const;
export type Assessment = (typeof ASSESSMENTS)[number];

export const END_REASONS = ["completed", "user_ended", "cancelled", "failed", "legacy_unstructured"] as const;
export type EndReason = (typeof END_REASONS)[number];

export const TARGET_ROLES = ["auto", "all", "cto", "designer", "ops", "target_user"] as const;
export type TargetRole = (typeof TARGET_ROLES)[number];

export const ACTIONS = [
  "clarify",
  "confirm_brief",
  "skip_clarification",
  "revise_brief",
  "add_evidence",
  "brainstorm",
  // 内部动作：brainstorm 成功后由工作流自动排入 synthesis 子 Run，
  // 用户不可提交，但 `RunView.action` 会返回该值，故必须纳入联合类型。
  "synthesis",
  "confirm_decision",
  "start_audit",
  "answer_audit",
  "correct_audit_assessment",
  "rebuild_legacy",
  "end_audit",
  "cancel_audit",
  "start_loop",
  "loop_decision",
  // 内部动作：loop 轮次由 loop_round 驱动，同样不可由用户提交。
  "loop_round",
] as const;
export type Action = (typeof ACTIONS)[number];
/** backend: NORMAL_ACTIONS（返回 RunReceipt 202） */
export const NORMAL_ACTIONS: readonly Action[] = [
  "clarify",
  "confirm_brief",
  "skip_clarification",
  "revise_brief",
  "add_evidence",
  "brainstorm",
  "confirm_decision",
  "start_audit",
  "answer_audit",
  "correct_audit_assessment",
  "rebuild_legacy",
  "start_loop",
  "loop_decision",
];
/** backend: CONTROL_ACTIONS（返回 ControlReceipt 202） */
export const CONTROL_ACTIONS: readonly Action[] = ["end_audit", "cancel_audit"];

export const CONTROL_STATUSES = ["accepted", "scheduled", "applied", "failed"] as const;
export type ControlStatus = (typeof CONTROL_STATUSES)[number];

export const EVENT_TYPES = [
  "run.queued",
  "run.started",
  "step.started",
  "role.output.updated",
  "role.completed",
  "product_state.updated",
  "audit.updated",
  "loop.updated",
  "run.recovered",
  "run.completed",
  "run.failed",
  "run.cancelled",
] as const;
export type EventType = (typeof EVENT_TYPES)[number];
export const TERMINAL_EVENT_TYPES: readonly EventType[] = ["run.completed", "run.failed", "run.cancelled"];

export const TASK_KINDS = [
  "clarify",
  "brainstorm",
  "synthesis",
  "peer_review",
  "audit_open",
  "audit_answer",
  "audit_report",
  "rebuild_legacy",
  "loop_round",
] as const;
export type TaskKind = (typeof TASK_KINDS)[number];

export const CONTEXT_SECTIONS = [
  "role_protocol",
  "product_definition",
  "product_state",
  "evidence",
  "audit_question",
  "conversation",
  "current_input",
] as const;
export type ContextSection = (typeof CONTEXT_SECTIONS)[number];

export const ORIGIN_KINDS = ["model", "human", "system", "legacy"] as const;
export type OriginKind = (typeof ORIGIN_KINDS)[number];

export const CONFIDENCE_LEVELS = ["low", "medium", "high"] as const;
export type ConfidenceLevel = (typeof CONFIDENCE_LEVELS)[number];

export const AUDIT_DIMENSIONS = [
  "problem_validity",
  "solution_effectiveness",
  "technical_risk",
  "business_loop",
  "user_adoption",
  "execution_risk",
] as const;
export type AuditDimension = (typeof AUDIT_DIMENSIONS)[number];
export const AUDIT_DIMENSION_LABELS: Record<AuditDimension, string> = {
  problem_validity: "问题真实性",
  solution_effectiveness: "方案有效性",
  technical_risk: "技术风险",
  business_loop: "商业闭环",
  user_adoption: "用户采用",
  execution_risk: "执行风险",
};

export const EXPERT_ROLES: readonly RoleCode[] = ["cto", "designer", "ops", "target_user"];
export const ALL_ROLE_CODES: readonly RoleCode[] = [
  "coach",
  "cto",
  "designer",
  "ops",
  "target_user",
  "moderator",
  "interviewer",
];

// --------------------------------------------------------------------------- //
// 产品定义（FR-01）
// --------------------------------------------------------------------------- //

export const BRIEF_FIELDS = [
  "target_users",
  "usage_scenario",
  "core_problem",
  "current_alternatives",
  "product_form",
  "constraints",
  "success_criteria",
] as const;
export type BriefFieldName = (typeof BRIEF_FIELDS)[number];

/** 缺少这些字段时不可展示「确认定义」卡片（FR-01） */
export const BRIEF_REQUIRED_FIELDS: readonly BriefFieldName[] = [
  "target_users",
  "usage_scenario",
  "core_problem",
  "success_criteria",
];

export const BRIEF_FIELD_LABELS: Record<BriefFieldName, string> = {
  target_users: "目标用户",
  usage_scenario: "使用场景",
  core_problem: "核心问题",
  current_alternatives: "现有替代方案",
  product_form: "产品形态",
  constraints: "约束条件",
  success_criteria: "成功标准",
};

export interface BriefField {
  value: string | null;
  status: BriefFieldStatus;
  source_message_ids: string[];
}

export interface ProductBrief {
  target_users: BriefField;
  usage_scenario: BriefField;
  core_problem: BriefField;
  current_alternatives: BriefField;
  product_form: BriefField;
  constraints: BriefField;
  success_criteria: BriefField;
  status: BriefStatus;
  skipped: boolean;
}

// --------------------------------------------------------------------------- //
// ProductState（§5.2）
// --------------------------------------------------------------------------- //

export interface Claim {
  id: string;
  kind: ClaimKind;
  text: string;
  source_message_ids: string[];
  evidence_refs: string[];
  origin: OriginKind;
  status: ClaimStatus;
  supersedes_id: string | null;
  created_product_version: number;
  confidence_level: ConfidenceLevel | null;
  role_code: RoleCode | null;
}

export interface OptionItem {
  id: string;
  title: string;
  summary: string | null;
  claim_ids: string[];
  tradeoffs: string[];
  risk_ids: string[];
  assumption_ids: string[];
  experiment_ids: string[];
  origin: OriginKind;
  status: ClaimStatus;
}

export interface DecisionItem {
  id: string;
  option_id: string | null;
  rationale: string;
  evidence_refs: string[];
  open_issue_ids: string[];
  status: DecisionStatus;
  needs_review: boolean;
  confirmed_by: string | null;
  confirmed_at: string | null;
  origin: OriginKind;
}

export interface ExperimentItem {
  id: string;
  hypothesis_claim_id: string | null;
  method: string;
  success_criterion: string;
  needed_data: string | null;
  status: ExperimentStatus;
  result_evidence_ids: string[];
  origin: OriginKind;
}

export const ISSUE_KINDS = ["risk", "assumption", "question", "conflict", "gap"] as const;
export type IssueKind = (typeof ISSUE_KINDS)[number];

export interface UnresolvedIssue {
  id: string;
  kind: IssueKind;
  text: string;
  related_ids: string[];
  status: IssueStatus;
  dimension: string | null;
}

export interface ProductState {
  session_id: string;
  version: number;
  schema_version: number;
  brief: ProductBrief;
  claims: Claim[];
  options: OptionItem[];
  decisions: DecisionItem[];
  experiments: ExperimentItem[];
  unresolved_issues: UnresolvedIssue[];
  source_run_id: string | null;
  created_at: string;
}

export interface Evidence {
  id: string;
  session_id: string | null;
  kind: EvidenceKind;
  source_uri: string | null;
  title: string;
  excerpt: string;
  content_hash: string;
  source_date: string | null;
  retrieved_at: string;
  verification_status: VerificationStatus;
  limitations: string | null;
  created_by: string | null;
}

// --------------------------------------------------------------------------- //
// 画布（FR-05）
// --------------------------------------------------------------------------- //

export const CANVAS_NODE_KINDS = [
  "problem",
  "user",
  "option",
  "risk",
  "assumption",
  "question",
  "experiment",
  "decision",
  "claim",
] as const;
export type CanvasNodeKind = (typeof CANVAS_NODE_KINDS)[number];

export interface CanvasNode {
  id: string;
  kind: CanvasNodeKind;
  title: string;
  detail: string;
  status: string;
  claim_ids: string[];
  option_ids: string[];
  decision_ids: string[];
  experiment_ids: string[];
  source_message_ids: string[];
  evidence_refs: string[];
  /** true = 无来源的模型推测，UI 必须显式标注 */
  model_speculation: boolean;
  /** AC-20：来源原文里找不到的数字（UI 标「数字未验证」并展示验证建议） */
  unverified_numbers?: string[];
  verification_suggestion?: string | null;
}

export interface CanvasBranch {
  key: string;
  title: string;
  nodes: CanvasNode[];
}

export interface CanvasProjection {
  session_id: string;
  product_version: number;
  schema_version: number;
  root: CanvasNode | null;
  branches: CanvasBranch[];
}

// --------------------------------------------------------------------------- //
// 审计（FR-07）
// --------------------------------------------------------------------------- //

export interface DimensionState {
  key: string;
  asked_question_ids: string[];
  answered_question_ids: string[];
  assessment: Assessment;
  rationale: string;
  evidence_refs: string[];
  open_issue_ids: string[];
}

export interface AuditQuestion {
  id: string;
  audit_id: string;
  ordinal: number;
  dimension: string;
  issue_refs: string[];
  question_text: string;
  question_message_id: string | null;
  answer_message_id: string | null;
  assessment: Assessment;
  rationale: string;
  evidence_refs: string[];
}

export interface AuditReport {
  audit_id: string;
  product_version: number;
  audit_version: number;
  end_reason: EndReason;
  question_count: number;
  dimension_states: DimensionState[];
  supported_points: string[];
  unresolved_gaps: string[];
  recommended_experiments: string[];
  limitations: string;
  /** 一段话总结（第 18 批新增；旧报告可能缺失 → 可选） */
  summary?: string | null;
  /** 报告正文里在来源中找不到的数字（第 28 批新增；旧报告缺失视为空） */
  unverified_numbers?: string[] | null;
  /** 补充数字来源的建议 */
  verification_suggestion?: string | null;
  human_revised: boolean;
  generated_by: "model" | "fallback";
}

export interface AuditSnapshot {
  id: string;
  session_id: string;
  product_version: number;
  audit_version: number;
  status: AuditStatus;
  question_count: number;
  end_reason: EndReason | null;
  active_question_id: string | null;
  pending_continuation: boolean;
  close_requested: boolean;
  closing_control_id: string | null;
  dimension_states: DimensionState[];
  /** 审计问题列表随快照一并返回，前端无需为「第 N 问/历史问答」再发一次请求（FR-07）。 */
  questions: AuditQuestion[];
  report: AuditReport | null;
  created_at: string;
  updated_at: string;
}

// --------------------------------------------------------------------------- //
// 闭环工作室 Loop Studio（契约 §2 / §4 / §6 / §7）
// --------------------------------------------------------------------------- //

export const LOOP_STATUSES = ["running", "awaiting_decision", "shipped", "stopped", "failed"] as const;
export type LoopStatus = (typeof LOOP_STATUSES)[number];

export const LOOP_SCOPES = ["mvp", "optimize"] as const;
export type LoopScope = (typeof LOOP_SCOPES)[number];

/** 五阶段顺序即后端 LOOP_STAGES 顺序，前端时间线不得重排。 */
export const LOOP_STAGES = ["ideate", "validate", "develop", "test", "evaluate"] as const;
export type LoopStage = (typeof LOOP_STAGES)[number];

export const LOOP_STAGE_STATUSES = ["pending", "running", "done", "failed", "skipped"] as const;
export type LoopStageStatus = (typeof LOOP_STAGE_STATUSES)[number];

export const LOOP_ROUND_STATUSES = ["running", "done", "failed"] as const;
export type LoopRoundStatus = (typeof LOOP_ROUND_STATUSES)[number];

/** 注意：后端 Python 侧写作 pass_，线上字面量仍是 "pass"。 */
export const LOOP_VERDICTS = ["pending", "pass", "iterate", "blocked"] as const;
export type LoopVerdict = (typeof LOOP_VERDICTS)[number];

export const LOOP_DECISION_KINDS = ["iterate", "ship", "stop"] as const;
export type LoopDecisionKind = (typeof LOOP_DECISION_KINDS)[number];

export const LOOP_ARTIFACT_KINDS = [
  "mvp_spec",
  "validation",
  "prototype",
  "test_report",
  "scorecard",
] as const;
export type LoopArtifactKind = (typeof LOOP_ARTIFACT_KINDS)[number];

export const CHECK_RESULTS = ["unknown", "pass", "fail"] as const;
export type CheckResult = (typeof CHECK_RESULTS)[number];

export const TEST_CASE_STATUSES = ["untested", "passed", "failed", "skipped"] as const;
export type TestCaseStatus = (typeof TEST_CASE_STATUSES)[number];

export const FEATURE_PRIORITIES = ["must", "should", "could"] as const;
export type FeaturePriority = (typeof FEATURE_PRIORITIES)[number];

export const EFFORT_SIZES = ["S", "M", "L"] as const;
export type EffortSize = (typeof EFFORT_SIZES)[number];

export const LOOP_SCORE_KEYS = [
  "functionality",
  "feasibility",
  "user_value",
  "risk",
  "delivery",
  "testability",
  "docs",
] as const;
export type LoopScoreKey = (typeof LOOP_SCORE_KEYS)[number];

/** 七维满分（后端 LOOP_SCORE_LABELS 的 max），合计 100。 */
export const LOOP_SCORE_MAX: Record<LoopScoreKey, number> = {
  functionality: 25,
  feasibility: 15,
  user_value: 20,
  risk: 15,
  delivery: 10,
  testability: 10,
  docs: 5,
};

export const MIN_LOOP_ROUNDS = 1;
export const MAX_LOOP_ROUNDS = 5;
export const LOOP_ROUNDS_DEFAULT = 3;
export const LOOP_TARGET_SCORE_MIN = 60;
export const LOOP_TARGET_SCORE_MAX = 100;
export const LOOP_TARGET_SCORE_DEFAULT = 85;
export const MAX_LOOP_FOCUS_CHARS = 2000;
export const MAX_LOOP_DECISION_NOTE_CHARS = 2000;

export interface LoopAssumption {
  id: string;
  statement: string;
  risk: string;
  verification: string;
}

export interface MvpFeature {
  id: string;
  title: string;
  description: string;
  acceptance: string;
  priority: FeaturePriority;
  effort: EffortSize;
}

export interface LoopMvpSpec {
  title: string;
  summary: string;
  features: MvpFeature[];
  assumptions: LoopAssumption[];
  not_in_scope: string[];
}

export interface ValidationCheck {
  id: string;
  feature_id: string | null;
  question: string;
  result: CheckResult;
  evidence_refs: string[];
  notes: string;
}

export interface LoopValidation {
  checks: ValidationCheck[];
  summary: string;
  unknowns: string[];
}

export interface PrototypeFile {
  path: string;
  language: string;
  content: string;
  purpose: string;
}

export interface PrototypeSpec {
  title: string;
  summary: string;
  entry_file: string;
  run_command: string;
  files: PrototypeFile[];
  limitations: string;
  requires_human_build: boolean;
}

export interface LoopTestCase {
  id: string;
  feature_id: string | null;
  title: string;
  steps: string[];
  expected: string;
  status: TestCaseStatus;
  evidence: string;
}

export interface LoopTestReport {
  /** 后端校验器会把 total 对齐 cases 长度，前端以 cases.length 为准展示。 */
  total: number;
  passed: number;
  failed: number;
  skipped: number;
  simulated: boolean;
  cases: LoopTestCase[];
  notes: string;
}

export interface LoopScoreDimension {
  key: LoopScoreKey;
  score: number;
  max: number;
  reason: string;
}

export interface LoopScoreCard {
  total: number;
  dimensions: LoopScoreDimension[];
  verdict: LoopVerdict;
  blockers: string[];
  highlights: string[];
  next_actions: string[];
  generated_by: "model" | "harness";
  hard_capped: boolean;
}

export interface LoopStageView {
  stage: LoopStage;
  status: LoopStageStatus;
  summary: string;
  error_code: string | null;
  updated_at: string;
}

export interface LoopArtifactView {
  id: string;
  kind: LoopArtifactKind;
  title: string;
  size_bytes: number;
  created_at: string;
}

export interface LoopRoundView {
  id: string;
  ordinal: number;
  status: LoopRoundStatus;
  score: number | null;
  verdict: LoopVerdict;
  stages: LoopStageView[];
  artifacts: LoopArtifactView[];
  started_at: string;
  finished_at: string | null;
}

export interface LoopSnapshot {
  loop_id: string;
  session_id: string;
  owner_email: string;
  status: LoopStatus;
  scope: LoopScope;
  current_round: number;
  max_rounds: number;
  target_score: number;
  loop_version: number;
  focus: string;
  rounds: LoopRoundView[];
  mvp_spec: LoopMvpSpec | null;
  validation: LoopValidation | null;
  prototype: PrototypeSpec | null;
  test_report: LoopTestReport | null;
  scorecard: LoopScoreCard | null;
  latest_run_id: string | null;
  created_at: string;
  updated_at: string;
}

/** `loop.updated` 事件 payload：局部更新，字段可能不完整，前端不得直接当快照用。 */
export interface LoopUpdatedPayload {
  loop_id: string;
  status: LoopStatus;
  current_round: number;
  loop_version: number;
  stage: LoopStage | null;
  stage_status: LoopStageStatus | null;
  round_status: LoopRoundStatus | null;
  score: number | null;
  verdict: LoopVerdict | null;
  artifact_ids: string[];
}

/** `start_loop` / `loop_decision` 的 202 回执（后端 LoopAck，命令层仍返回 RunReceipt）。 */
export interface LoopAck {
  loop_id: string;
  status: LoopStatus;
  loop_version: number;
  current_round: number;
  run_id: string | null;
  reused: boolean;
}

export interface StartLoopPayload {
  scope: LoopScope;
  max_rounds: number;
  target_score: number;
  focus: string;
  expected_product_version: number;
}

export interface LoopDecisionPayload {
  loop_id: string;
  expected_loop_version: number;
  decision: LoopDecisionKind;
  note: string;
}

/** `GET /api/v2/loops/{loop_id}/artifacts/{artifact_id}` */
export interface LoopArtifactResponse {
  artifact: LoopArtifactView;
  payload: Record<string, unknown>;
}

export interface LoopListPage {
  items: LoopSnapshot[];
  next_cursor: string | null;
}

// --------------------------------------------------------------------------- //
// 命令与回执（FR-03 / §7.2）
// --------------------------------------------------------------------------- //

export interface ClarifyPayload {
  content: string;
}

export interface ConfirmBriefPayload {
  expected_product_version: number;
  fields?: Record<string, string>;
}

export interface SkipClarificationPayload {
  reason?: string | null;
}

export interface BriefFieldUpdate {
  value: string | null;
  status: BriefFieldStatus;
}

export interface ReviseBriefPayload {
  expected_product_version: number;
  fields: Record<string, BriefFieldUpdate>;
  note?: string | null;
}

export interface AddEvidencePayload {
  kind: EvidenceKind;
  title?: string;
  excerpt: string;
  source_uri?: string | null;
  source_date?: string | null;
  limitations?: string | null;
}

export interface BrainstormPayload {
  content: string;
  target?: TargetRole;
}

export interface ConfirmDecisionPayload {
  expected_product_version: number;
  decision_id: string;
  option_id?: string | null;
  rationale?: string | null;
}

export interface StartAuditPayload {
  expected_product_version?: number | null;
}

export interface AnswerAuditPayload {
  audit_id: string;
  question_id: string;
  answer: string;
}

export interface CorrectAssessmentPayload {
  audit_id: string;
  expected_audit_version: number;
  dimension?: string | null;
  question_id?: string | null;
  assessment: Assessment;
  rationale: string;
  evidence_refs?: string[];
}

export interface EndAuditPayload {
  audit_id: string;
}

export interface CancelAuditPayload {
  audit_id: string;
}

/** POST /api/v2/sessions/{sid}/commands 请求体 */
export interface CommandRequest {
  request_id: string;
  action: Action;
  expected_session_version?: number | null;
  payload?: Record<string, unknown>;
}

/** 所有 action → payload 的映射（前端构造命令时使用） */
export type CommandPayloadMap = {
  clarify: ClarifyPayload;
  confirm_brief: ConfirmBriefPayload;
  skip_clarification: SkipClarificationPayload;
  revise_brief: ReviseBriefPayload;
  add_evidence: AddEvidencePayload;
  brainstorm: BrainstormPayload;
  confirm_decision: ConfirmDecisionPayload;
  start_audit: StartAuditPayload;
  answer_audit: AnswerAuditPayload;
  correct_audit_assessment: CorrectAssessmentPayload;
  rebuild_legacy: { expected_product_version?: number | null };
  end_audit: EndAuditPayload;
  cancel_audit: CancelAuditPayload;
  start_loop: StartLoopPayload;
  loop_decision: LoopDecisionPayload;
  // 内部动作：由工作流自动排入（brainstorm → synthesis、loop 轮次 → loop_round），
  // 前端**不可提交**（backend `parse_command` 会以 UNKNOWN_ACTION 拒绝），
  // 这里声明为空 payload 只是为了让 `CommandPayloadMap` 覆盖完整的 `Action` 联合。
  synthesis: Record<string, never>;
  loop_round: Record<string, never>;
};

export interface RunReceipt {
  run_id: string;
  session_id: string;
  action: Action;
  status: RunStatus;
  session_version: number;
  base_product_version: number;
  events_url: string;
  reused: boolean;
  audit_id: string | null;
  /** 闭环轮次 Run 才有值；普通命令为 null。 */
  loop_id: string | null;
}

export interface ControlReceipt {
  control_id: string;
  audit_id: string;
  status: ControlStatus;
  session_version: number;
  related_run_id: string | null;
  reused: boolean;
  action: Action | null;
}

/** 已有 opening/active/closing 审计时的 200 恢复回执 */
export interface StartAuditRecovery {
  audit_id: string;
  reused: true;
  status: AuditStatus;
  current_run_id: string | null;
  latest_run_id: string | null;
  session_version: number;
}

/** 命令 202 的联合返回类型 */
export type CommandReceipt = RunReceipt | ControlReceipt | StartAuditRecovery;

export interface RunSnapshot {
  run_id: string;
  session_id: string;
  action: Action;
  status: RunStatus;
  base_session_version: number;
  base_product_version: number;
  result_product_version: number | null;
  steps: Array<Record<string, unknown>>;
  messages: Array<Record<string, unknown>>;
  session_version: number;
  product_version: number;
  canvas: CanvasProjection | null;
  audit: AuditSnapshot | null;
  /** run.loop_id 存在时后端同事务填快照，前端据此刷新 Loop 工作室。 */
  loop: LoopSnapshot | null;
  loop_id: string | null;
  snapshot_event_seq: number;
  latest_event_seq: number;
}

// --------------------------------------------------------------------------- //
// 事件（FR-06）
// --------------------------------------------------------------------------- //

export interface RunEvent {
  schema_version: number;
  session_id: string;
  run_id: string;
  /** 每个 run 内单调递增，从 1 开始；去重键 = run_id + event_seq */
  event_seq: number;
  type: EventType;
  occurred_at: string;
  payload: Record<string, unknown>;
}

// --------------------------------------------------------------------------- //
// 会话 / 消息 / Run 视图
// --------------------------------------------------------------------------- //

export interface SessionView {
  id: string;
  owner_email: string;
  phase: SessionPhase;
  session_version: number;
  current_product_version: number;
  current_audit_id: string | null;
  active_run_id: string | null;
  problem_statement: string;
  brief_status: BriefStatus;
  current_loop_id: string | null;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
  message_count: number;
}

export interface MessageView {
  id: string;
  session_id: string;
  /** 会话内单调递增 */
  seq: number;
  sender_kind: SenderKind;
  role_code: RoleCode | null;
  stage: MessageStage;
  content: string;
  status: MessageStatus;
  run_id: string | null;
  audit_id: string | null;
  created_at: string;
}

export interface RunView {
  id: string;
  session_id: string;
  action: Action;
  status: RunStatus;
  request_id: string;
  base_session_version: number;
  base_product_version: number;
  result_product_version: number | null;
  lease_owner: string | null;
  lease_epoch: number;
  lease_expires_at: number | null;
  parent_run_id: string | null;
  audit_id: string | null;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  deadline_at: number | null;
}

// --------------------------------------------------------------------------- //
// 列表分页包装（§5.2）
// --------------------------------------------------------------------------- //

export interface SessionListPage {
  items: SessionView[];
  next_cursor: string | null;
}

export interface MessageListPage {
  items: MessageView[];
  next_before_seq: number | null;
}

export interface DeletedSession {
  session_id: string;
  deleted_at: string;
}

/** 统一错误体：AppError → {"error":{code,message,retryable,request_id,details}} */
export interface ApiErrorBody {
  error: {
    code: string;
    message: string;
    retryable: boolean;
    request_id: string | null;
    details: unknown;
  };
}

// --------------------------------------------------------------------------- //
// 便捷判断
// --------------------------------------------------------------------------- //

export function isTerminalRunStatus(status: RunStatus | string): boolean {
  return (TERMINAL_RUN_STATUSES as readonly string[]).includes(status);
}

export function isTerminalEventType(type: EventType | string): boolean {
  return (TERMINAL_EVENT_TYPES as readonly string[]).includes(type);
}

export function isTerminalRunEvent(event: RunEvent): boolean {
  return isTerminalEventType(event.type);
}