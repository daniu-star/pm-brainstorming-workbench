/**
 * M3 审计页的纯展示逻辑（维度/评估/状态中文标签、快照归一化、错误码文案）。
 *
 * 为什么单独抽一个模块：
 * 1) 审计快照来自 `GET /api/v2/audits/{id}`，后端在 AuditSnapshot 之外**额外**返回
 *    `questions`（问题列表，`backend/api/v2/audits.py`），而 contracts.py 的
 *    AuditSnapshot 并不含该字段 —— 需要一层防御性归一化，页面不能直接 `as` 强转。
 * 2) 这层归一化不依赖 React / zustand / fetch，可以被 `node --test` 直接覆盖
 *    （本机 npm registry 不可达，不能装 vitest / RTL）。
 * 3) 报告字段可能缺失：缺失必须显示「未生成」，绝不能编造内容（PRD FR-07）。
 */
import {
  AUDIT_DIMENSION_LABELS,
  AUDIT_DIMENSIONS,
  AUDIT_STATUSES,
  ASSESSMENTS,
  CONTROL_STATUSES,
  END_REASONS,
} from "./contracts.generated";
import type {
  Assessment,
  AuditDimension,
  AuditStatus,
  ControlStatus,
  EndReason,
} from "./contracts.generated";

/** 后端常量 AUDIT_QUESTION_HARD_LIMIT（contracts.py）：问题数硬上限 */
export const AUDIT_QUESTION_HARD_LIMIT = 18;
/** 后端常量 MAX_USER_INPUT_CHARS：单条回答上限 */
export const MAX_ANSWER_CHARS = 8000;
/** 报告缺字段时的统一占位文案 */
export const NOT_GENERATED = "未生成";

// --------------------------------------------------------------------------- //
// 中文标签
// --------------------------------------------------------------------------- //

export const ASSESSMENT_LABELS: Record<Assessment, string> = {
  not_discussed: "未讨论",
  awaiting_answer: "待回答",
  needs_clarification: "需澄清",
  supported: "已支撑",
  gap: "存在缺口",
  not_applicable: "不适用",
};

export const AUDIT_STATUS_LABELS: Record<AuditStatus, string> = {
  created: "已创建",
  opening: "准备中",
  active: "进行中",
  closing: "收尾中",
  completed: "已完成",
  failed: "已失败",
  cancelled: "已取消",
};

export const END_REASON_LABELS: Record<EndReason, string> = {
  completed: "正常结束",
  user_ended: "用户主动结束",
  cancelled: "用户取消",
  failed: "执行失败",
  legacy_unstructured: "旧流程迁移",
};

export const CONTROL_STATUS_LABELS: Record<ControlStatus, string> = {
  accepted: "已受理",
  scheduled: "排队中",
  applied: "已生效",
  failed: "处理失败",
};

export const AUDIT_STATUS_CLASSES: Record<AuditStatus, string> = {
  created: "text-zinc-300 border-zinc-600/50 bg-zinc-700/20",
  opening: "text-sky-300 border-sky-500/40 bg-sky-500/10",
  active: "text-brand-300 border-brand-500/40 bg-brand-500/10",
  closing: "text-amber-300 border-amber-500/40 bg-amber-500/10",
  completed: "text-emerald-300 border-emerald-500/40 bg-emerald-500/10",
  failed: "text-red-300 border-red-500/40 bg-red-500/10",
  cancelled: "text-zinc-400 border-zinc-600/50 bg-zinc-700/20",
};

export const ASSESSMENT_CLASSES: Record<Assessment, string> = {
  not_discussed: "text-zinc-500 border-zinc-700/50",
  awaiting_answer: "text-sky-300 border-sky-500/40 bg-sky-500/10",
  needs_clarification: "text-amber-300 border-amber-500/40 bg-amber-500/10",
  supported: "text-emerald-300 border-emerald-500/40 bg-emerald-500/10",
  gap: "text-red-300 border-red-500/40 bg-red-500/10",
  not_applicable: "text-zinc-400 border-zinc-600/50",
};

/** 六维 key → 中文（未知 key 回退原样，绝不显示 "undefined"） */
export function dimensionLabel(key: string | null | undefined): string {
  if (!key) return "未知维度";
  const labels = AUDIT_DIMENSION_LABELS as Record<string, string>;
  return labels[key] ?? key;
}

export function isAuditDimension(value: string): value is AuditDimension {
  return (AUDIT_DIMENSIONS as readonly string[]).includes(value);
}

export function assessmentLabel(value: string | null | undefined): string {
  if (!value) return ASSESSMENT_LABELS.not_discussed;
  const labels = ASSESSMENT_LABELS as Record<string, string>;
  return labels[value] ?? value;
}

export function assessmentClass(value: string | null | undefined): string {
  if (!value) return ASSESSMENT_CLASSES.not_discussed;
  const classes = ASSESSMENT_CLASSES as Record<string, string>;
  return classes[value] ?? ASSESSMENT_CLASSES.not_discussed;
}

export function auditStatusLabel(value: string | null | undefined): string {
  if (!value) return AUDIT_STATUS_LABELS.created;
  const labels = AUDIT_STATUS_LABELS as Record<string, string>;
  return labels[value] ?? value;
}

export function auditStatusClass(value: string | null | undefined): string {
  if (!value) return AUDIT_STATUS_CLASSES.created;
  const classes = AUDIT_STATUS_CLASSES as Record<string, string>;
  return classes[value] ?? AUDIT_STATUS_CLASSES.created;
}

export function endReasonLabel(value: string | null | undefined): string {
  if (!value) return NOT_GENERATED;
  const labels = END_REASON_LABELS as Record<string, string>;
  return labels[value] ?? value;
}

export function controlStatusLabel(value: string | null | undefined): string {
  if (!value) return CONTROL_STATUS_LABELS.accepted;
  const labels = CONTROL_STATUS_LABELS as Record<string, string>;
  return labels[value] ?? value;
}

/** `第 N / 18 问`；ordinal 非法（<1）时不假装有进度 */
export function questionOrdinalText(ordinal: number, total?: number): string {
  const hardLimit = total && total > 0 ? total : AUDIT_QUESTION_HARD_LIMIT;
  if (!Number.isFinite(ordinal) || ordinal < 1) return `第 — / ${hardLimit} 问`;
  return `第 ${ordinal} / ${hardLimit} 问`;
}

// --------------------------------------------------------------------------- //
// 防御性归一化
// --------------------------------------------------------------------------- //

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function asNullableString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function asBoolean(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

function asKnown<T extends string>(values: readonly T[], value: unknown, fallback: T): T {
  return typeof value === "string" && (values as readonly string[]).includes(value)
    ? (value as T)
    : fallback;
}

export interface NormalizedDimensionState {
  key: string;
  label: string;
  assessment: Assessment;
  assessmentLabel: string;
  rationale: string;
  askedCount: number;
  answeredCount: number;
  openIssueIds: string[];
}

export interface NormalizedQuestion {
  id: string;
  ordinal: number;
  ordinalText: string;
  dimension: string;
  dimensionLabel: string;
  questionText: string;
  /** 后端可能还没把问题文本落库（question_text 为空），此时用占位文案 */
  hasText: boolean;
  assessment: Assessment;
  assessmentLabel: string;
  rationale: string;
  questionMessageId: string | null;
  answerMessageId: string | null;
}

export interface NormalizedReport {
  questionCount: number;
  endReason: EndReason;
  endReasonLabel: string;
  dimensionStates: NormalizedDimensionState[];
  supportedPoints: string[];
  unresolvedGaps: string[];
  recommendedExperiments: string[];
  limitations: string;
  humanRevised: boolean;
  generatedBy: string | null;
  /** 缺失的字段名（中文），用于「未生成提示」；空数组 = 报告完整 */
  missingFields: string[];
  hasReports: boolean;
}

export interface NormalizedAudit {
  id: string;
  sessionId: string;
  status: AuditStatus;
  statusLabel: string;
  productVersion: number;
  auditVersion: number;
  questionCount: number;
  activeQuestionId: string | null;
  endReason: EndReason | null;
  closeRequested: boolean;
  dimensionStates: NormalizedDimensionState[];
  questions: NormalizedQuestion[];
  report: NormalizedReport | null;
  createdAt: string;
  updatedAt: string;
}

export function normalizeDimensionState(raw: unknown): NormalizedDimensionState {
  const record = isRecord(raw) ? raw : {};
  const key = asString(record.key);
  return {
    key,
    label: dimensionLabel(key),
    assessment: asKnown(ASSESSMENTS, record.assessment, "not_discussed"),
    assessmentLabel: assessmentLabel(asString(record.assessment) || null),
    rationale: asString(record.rationale),
    askedCount: Array.isArray(record.asked_question_ids) ? record.asked_question_ids.length : 0,
    answeredCount: Array.isArray(record.answered_question_ids)
      ? record.answered_question_ids.length
      : 0,
    openIssueIds: asStringArray(record.open_issue_ids),
  };
}

export function normalizeQuestion(raw: unknown): NormalizedQuestion | null {
  if (!isRecord(raw)) return null;
  const id = asString(raw.id);
  if (!id) return null;
  const ordinal = asNumber(raw.ordinal, 0);
  const questionText = asString(raw.question_text);
  return {
    id,
    ordinal,
    ordinalText: questionOrdinalText(ordinal),
    dimension: asString(raw.dimension),
    dimensionLabel: dimensionLabel(asString(raw.dimension)),
    questionText,
    hasText: questionText.trim().length > 0,
    assessment: asKnown(ASSESSMENTS, raw.assessment, "awaiting_answer"),
    assessmentLabel: assessmentLabel(asString(raw.assessment) || null),
    rationale: asString(raw.rationale),
    questionMessageId: asNullableString(raw.question_message_id),
    answerMessageId: asNullableString(raw.answer_message_id),
  };
}

/**
 * 报告归一化：**只做格式整理，不补内容**。
 * 缺失字段记入 missingFields，页面显示「未生成」。
 */
export function normalizeReport(raw: unknown): NormalizedReport | null {
  if (!isRecord(raw)) return null;
  const supportedPoints = asStringArray(raw.supported_points);
  const unresolvedGaps = asStringArray(raw.unresolved_gaps);
  const recommendedExperiments = asStringArray(raw.recommended_experiments);
  const limitations = asString(raw.limitations).trim();
  const dimensionStates = Array.isArray(raw.dimension_states)
    ? raw.dimension_states.map(normalizeDimensionState)
    : [];

  const missingFields: string[] = [];
  if (supportedPoints.length === 0) missingFields.push("支持论据");
  if (unresolvedGaps.length === 0) missingFields.push("未解决缺口");
  if (recommendedExperiments.length === 0) missingFields.push("建议实验");
  if (limitations.trim().length === 0) missingFields.push("局限性说明");
  if (dimensionStates.length === 0) missingFields.push("维度评估");

  const endReason = asKnown(END_REASONS, raw.end_reason, "completed");

  return {
    questionCount: asNumber(raw.question_count, 0),
    endReason,
    endReasonLabel: endReasonLabel(endReason),
    dimensionStates,
    supportedPoints,
    unresolvedGaps,
    recommendedExperiments,
    limitations,
    humanRevised: asBoolean(raw.human_revised),
    generatedBy: asNullableString(raw.generated_by),
    missingFields,
    hasReports:
      supportedPoints.length > 0 ||
      unresolvedGaps.length > 0 ||
      recommendedExperiments.length > 0 ||
      limitations.trim().length > 0 ||
      dimensionStates.length > 0,
  };
}

/** 归一化审计快照；不是对象 / 没有 id → null（页面据此显示「无法读取审计」） */
export function normalizeAudit(raw: unknown): NormalizedAudit | null {
  if (!isRecord(raw)) return null;
  const id = asString(raw.id);
  if (!id) return null;

  const status = asKnown(AUDIT_STATUSES, raw.status, "created");
  const questions = Array.isArray(raw.questions)
    ? raw.questions
        .map(normalizeQuestion)
        .filter((item): item is NormalizedQuestion => item !== null)
        .sort((a, b) => a.ordinal - b.ordinal)
    : [];
  const dimensionStates = Array.isArray(raw.dimension_states)
    ? raw.dimension_states.map(normalizeDimensionState)
    : [];

  return {
    id,
    sessionId: asString(raw.session_id),
    status,
    statusLabel: auditStatusLabel(status),
    productVersion: asNumber(raw.product_version, 0),
    auditVersion: asNumber(raw.audit_version, 1),
    questionCount: asNumber(raw.question_count, 0),
    activeQuestionId: asNullableString(raw.active_question_id),
    endReason: typeof raw.end_reason === "string" ? asKnown(END_REASONS, raw.end_reason, "completed") : null,
    closeRequested: asBoolean(raw.close_requested),
    dimensionStates,
    questions,
    report: normalizeReport(raw.report),
    createdAt: asString(raw.created_at),
    updatedAt: asString(raw.updated_at),
  };
}

// --------------------------------------------------------------------------- //
// 进度与交互规则
// --------------------------------------------------------------------------- //

/**
 * 当前应当作答的问题：
 * 1) 后端给的 active_question_id；
 * 2) 否则按序取第一个还没有回答的问题；
 * 3) 都已回答 → null（页面显示「本轮问题已回答完」）。
 */
export function pickCurrentQuestion(audit: NormalizedAudit | null): NormalizedQuestion | null {
  if (!audit) return null;
  if (audit.activeQuestionId) {
    const active = audit.questions.find((item) => item.id === audit.activeQuestionId);
    if (active) return active;
  }
  return audit.questions.find((item) => item.answerMessageId === null) ?? null;
}

/** 已回答数量（用于 `已回答 3 / 18`） */
export function answeredCount(audit: NormalizedAudit | null): number {
  if (!audit) return 0;
  return audit.questions.filter((item) => item.answerMessageId !== null).length;
}

/** 只有 active 且有当前问题时才允许提交回答 */
export function canSubmitAnswer(audit: NormalizedAudit | null): boolean {
  if (!audit) return false;
  if (audit.status !== "active") return false;
  return pickCurrentQuestion(audit) !== null;
}

/** 审计是否已终结（可在页面上展示报告而不是回答框） */
export function isAuditFinished(audit: NormalizedAudit | null): boolean {
  if (!audit) return false;
  return audit.status === "completed" || audit.status === "cancelled" || audit.status === "failed";
}

export interface AnswerValidation {
  ok: boolean;
  /** 实际要提交的内容（超长时已截断） */
  value: string;
  error: string | null;
  /** 截断提示（非空时应展示给用户） */
  warning: string | null;
}

/** 回答校验：1..8000 字；超长按前端截断并明确提示（不静默改写） */
export function validateAnswerInput(text: string): AnswerValidation {
  const raw = text ?? "";
  if (raw.length > MAX_ANSWER_CHARS) {
    const value = raw.slice(0, MAX_ANSWER_CHARS).trim();
    return {
      ok: value.length > 0,
      value,
      error: value.length > 0 ? null : "请输入回答",
      warning: `回答超出 ${MAX_ANSWER_CHARS} 字上限（原 ${raw.length} 字），已截断`,
    };
  }
  const value = raw.trim();
  return {
    ok: value.length > 0,
    value,
    error: value.length > 0 ? null : "请输入回答",
    warning: null,
  };
}

/**
 * 从已加载的消息里找回答正文。
 * 后端只给 question 的 `answer_message_id`，不重复存回答文本；
 * 找不到时返回 null（页面显示「已回答」，不编造）。
 */
export function resolveAnswerText(
  messages: readonly { id: string; content: string }[],
  answerMessageId: string | null,
): string | null {
  if (!answerMessageId) return null;
  const hit = messages.find((message) => message.id === answerMessageId);
  if (!hit) return null;
  const text = hit.content.trim();
  return text.length > 0 ? text : null;
}

// --------------------------------------------------------------------------- //
// 错误码 / 控制回执文案
// --------------------------------------------------------------------------- //

const AUDIT_ERROR_TEXT: Record<string, string> = {
  UNAUTHENTICATED: "请先登录后再操作",
  FORBIDDEN: "没有权限访问该审计",
  NOT_FOUND: "审计不存在或已被删除",
  INVALID_REQUEST: "请求参数不合法，请检查后重试",
  UNKNOWN_ACTION: "后端不支持该操作",
  INPUT_TOO_LONG: `回答过长（最多 ${MAX_ANSWER_CHARS} 字）`,
  IDEMPOTENCY_KEY_MISMATCH: "重复提交的请求标识不一致，请刷新后重试",
  IDEMPOTENCY_KEY_REUSED: "该请求已提交过，请勿重复提交",
  VERSION_CONFLICT: "内容已在别处更新，请刷新后重试",
  SESSION_BUSY: "已有任务正在执行，请稍候再试",
  RATE_LIMITED: "操作过于频繁，请稍后再试",
  AUDIT_CLOSED: "审计已结束，无法继续操作",
  AUDIT_ACTIVE: "已有进行中的审计，请继续当前审计",
  QUESTION_ALREADY_ANSWERED: "该问题已经回答过了",
  QUESTION_NOT_ACTIVE: "该问题不是当前待回答的问题",
  CONTROL_SUPERSEDED: "该请求已被更新的请求取代",
  FEATURE_NOT_READY: "该功能尚未开放",
  INTERNAL: "服务暂时不可用，请稍后重试",
  NETWORK_ERROR: "网络连接失败，请检查网络后重试",
};

/**
 * 错误码 → 中文。未知错误码时回退到后端 message，再回退到通用文案，
 * 保证页面上永远不会出现空白或英文裸码（PRD 要求「不得静默」）。
 */
export function auditErrorText(
  code: string | null | undefined,
  fallback?: string | null,
): string {
  if (code && AUDIT_ERROR_TEXT[code]) return AUDIT_ERROR_TEXT[code];
  const trimmed = (fallback ?? "").trim();
  if (trimmed) return trimmed;
  if (code) return `操作失败（${code}）`;
  return "操作失败，请稍后重试";
}

/**
 * ControlReceipt.status 的提示：
 * - scheduled：审计正在收尾 / 有生成任务在跑，意图要等当前生成结束后才执行；
 * - applied：已立即生效；
 * - accepted/failed：直译。
 */
export function controlStatusHint(status: string | null | undefined): string {
  switch (status) {
    case "scheduled":
      return "当前生成结束后处理";
    case "applied":
      return "已立即生效";
    case "accepted":
      return "已受理，稍后处理";
    case "failed":
      return "处理失败";
    default:
      return "请求已提交";
  }
}

/** 控制动作的终态文案：scheduled 时要明确告诉用户「还没结束」 */
export function describeControlOutcome(
  action: "end_audit" | "cancel_audit",
  status: string | null | undefined,
): string {
  const base = action === "end_audit" ? "结束审计请求" : "取消审计请求";
  const suffix = controlStatusHint(status);
  return `${base}${suffix === "请求已提交" ? "已提交" : suffix}`;
}

/** 审计状态是否仍可被控制（终结后按钮禁用） */
export function canControlAudit(audit: NormalizedAudit | null): boolean {
  if (!audit) return false;
  return audit.status === "active" || audit.status === "opening" || audit.status === "created";
}
