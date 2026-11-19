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
  SessionPhase,
} from "./contracts.generated";
import { canStartAuditPhase } from "./phaseRouter";

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

/**
 * 与 asKnown 相同，但未知值返回 null 而不是折叠成某个「正常」值。
 * 用于 end_reason：失败码（如 MODEL_OUTPUT_INVALID）绝不能被显示成 completed。
 */
function asKnownOrNull<T extends string>(values: readonly T[], value: unknown): T | null {
  return typeof value === "string" && (values as readonly string[]).includes(value)
    ? (value as T)
    : null;
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
  /** 原始 end_reason（可能是后端错误码）；失败码不能折叠成 completed */
  endReasonRaw: string | null;
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
  /** 已识别的结束原因；未知/失败码为 null（原始值见 endReasonRaw） */
  endReason: EndReason | null;
  /** 后端原始 end_reason：失败码（如 MODEL_OUTPUT_INVALID）必须保留可展示 */
  endReasonRaw: string | null;
  closeRequested: boolean;
  /** 上一问处理失败、后端要求继续（AuditSnapshot.pending_continuation） */
  pendingContinuation: boolean;
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

  const rawEndReason = asNullableString(raw.end_reason);
  const endReason = asKnown(END_REASONS, rawEndReason, "completed");

  return {
    questionCount: asNumber(raw.question_count, 0),
    endReason,
    endReasonRaw: rawEndReason,
    // 未知值（失败码）原样展示，不折叠成「正常结束」
    endReasonLabel: endReasonLabel(rawEndReason ?? endReason),
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

  const rawEndReason = asNullableString(raw.end_reason);

  return {
    id,
    sessionId: asString(raw.session_id),
    status,
    statusLabel: auditStatusLabel(status),
    productVersion: asNumber(raw.product_version, 0),
    auditVersion: asNumber(raw.audit_version, 1),
    questionCount: asNumber(raw.question_count, 0),
    activeQuestionId: asNullableString(raw.active_question_id),
    endReason: rawEndReason ? asKnownOrNull(END_REASONS, rawEndReason) : null,
    endReasonRaw: rawEndReason,
    closeRequested: asBoolean(raw.close_requested),
    pendingContinuation: asBoolean(raw.pending_continuation),
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

/**
 * 页头进度文案（PROD-07）。
 *
 * 18 只是硬上限：六维都有结论（或达到 12 问目标）就会提前收尾
 * （backend/domain/constants.py AUDIT_QUESTION_TARGET / AUDIT_QUESTION_HARD_LIMIT，
 * backend/workflows/audit.py should_close）。只写「N / 18」会让用户以为必须走满 18 问。
 */
export function auditProgressLabel(
  answered: number,
  dimensionStates: readonly NormalizedDimensionState[] | null | undefined
): string {
  const answeredSafe = Number.isFinite(answered) && answered > 0 ? Math.floor(answered) : 0;
  const covered = (dimensionStates ?? []).filter(
    (state) => state.assessment === "supported" || state.assessment === "gap" || state.assessment === "not_applicable"
  ).length;
  return `已回答 ${answeredSafe} 问 · 六维覆盖 ${covered}/${AUDIT_DIMENSIONS.length} · 上限 ${AUDIT_QUESTION_HARD_LIMIT} 问（维度都有结论即收尾）`;
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

/** pending_continuation 时审计页的提示文案（上一问处理失败） */
export const CONTINUATION_NOTICE_TEXT = "上一问处理失败，可重试继续或结束审计";

/** 审计进入 closing 且没有可等待的 Run 时的提示文案 */
export const CLOSING_NOTICE_TEXT = "正在生成报告，可稍候或点刷新";

/**
 * 上一问处理失败（pending_continuation）且当前没有可回答的问题：
 * 页面应显示「重试继续 / 结束审计」，而不是空等。
 */
export function canRetryContinuation(
  audit: NormalizedAudit | null,
  hasCurrentQuestion: boolean
): boolean {
  if (!audit || hasCurrentQuestion) return false;
  return audit.status === "active" && audit.pendingContinuation;
}

/**
 * 审计收尾（closing）时的退避轮询间隔：1s/2s/4s/8s/16s/29s，累计 60s 封顶。
 * 没有 related_run_id 可订阅时，页面按这些间隔拉取审计快照。
 */
export const CLOSING_POLL_DELAYS_MS: readonly number[] = [1000, 2000, 4000, 8000, 16000, 29000];

/** closing 轮询耗尽（60s 封顶）后的提示：报告仍未生成出来 */
export const CLOSING_TIMEOUT_TEXT = "报告生成超时，可重试";
/** closing 关联 Run 失败后的提示 */
export const CLOSING_FAILED_TEXT = "报告生成失败，可重试";

export interface ClosingRecoveryInput {
  status: AuditStatus | null | undefined;
  /** 退避轮询是否已耗尽 */
  pollExhausted: boolean;
  /** 是否有失败、可重试的 Run */
  hasFailedRun: boolean;
}

export interface ClosingRecoveryState {
  /** 是否展示「重试生成报告 / 返回工作台」恢复入口 */
  showRecovery: boolean;
  noticeText: string;
}

/**
 * closing 分支的提示与恢复入口：
 * 轮询耗尽或关联 Run 失败后不能静默停在加载条上，必须给出可点的重试入口。
 */
export function resolveClosingRecoveryState(
  input: ClosingRecoveryInput,
): ClosingRecoveryState {
  const showRecovery =
    input.status === "closing" && (input.pollExhausted || input.hasFailedRun);
  return {
    showRecovery,
    noticeText: showRecovery
      ? input.hasFailedRun
        ? CLOSING_FAILED_TEXT
        : CLOSING_TIMEOUT_TEXT
      : CLOSING_NOTICE_TEXT,
  };
}

/** 点「重试生成报告」的目标：有 failed run 就重试该 run，否则重新拉取快照 */
export function resolveClosingRetryTarget(
  lastFailedRunId: string | null | undefined,
): "retry_failed_run" | "refresh_snapshot" {
  return lastFailedRunId ? "retry_failed_run" : "refresh_snapshot";
}

// --------------------------------------------------------------------------- //
// failed 审计的失败原因与恢复入口（DX-R3-01）
// --------------------------------------------------------------------------- //

export interface AuditFailureRecoveryInput {
  status: AuditStatus | null | undefined;
  /** 后端原始 end_reason（可能是错误码） */
  endReasonRaw?: string | null;
  /** 是否有可重试的失败 Run（store.lastFailedRunId） */
  hasFailedRun: boolean;
  /** 失败 Run 的 error_message（有则优先展示，比错误码更可读） */
  runErrorText?: string | null;
}

export interface AuditFailureRecoveryState {
  /** 审计是否失败 */
  isFailed: boolean;
  /** 是否展示「重试生成报告 / 返回工作台」恢复入口 */
  showRecovery: boolean;
  /** 失败原因文案（不再折叠成「正常结束」） */
  reasonText: string;
  /** 下一步指引 */
  hintText: string;
}

/** 审计失败时的原因与恢复入口：失败必须可解释、可重试，不能只显示「报告未生成」 */
export function resolveAuditFailureRecoveryState(
  input: AuditFailureRecoveryInput,
): AuditFailureRecoveryState {
  const isFailed = input.status === "failed";
  if (!isFailed) {
    return { isFailed: false, showRecovery: false, reasonText: "", hintText: "" };
  }
  const runError = (input.runErrorText ?? "").trim();
  const reasonText = runError
    ? runError
    : input.endReasonRaw
      ? endReasonLabel(input.endReasonRaw)
      : "执行失败";
  return {
    isFailed: true,
    showRecovery: true,
    reasonText,
    hintText: input.hasFailedRun
      ? "可重试该失败任务；若反复失败，返回工作台调整方案后重新审计。"
      : "可直接重试生成报告（会重新发起审计），或返回工作台调整方案后重新审计。",
  };
}

// --------------------------------------------------------------------------- //
// 审计开场/回答阶段的失败（R4 P1-1）
// --------------------------------------------------------------------------- //

export interface AuditStepFailureInput {
  status: AuditStatus | null | undefined;
  /**
   * `lastFailedRunId` 对应的 Run 是否属于当前审计。
   * 只有失败 Run 的 audit_id === 当前 audit.id 时才能认领，否则会把上一次
   * 审计（或其它会话）残留的失败误显示到当前审计上。
   */
  failedRunBelongsToAudit: boolean;
  /** 失败 Run 的 error_message（有则优先展示，比错误码更可读） */
  runErrorText?: string | null;
  /** 后端原始 end_reason（可能是错误码） */
  endReasonRaw?: string | null;
}

export interface AuditStepFailureState {
  /** 是否展示「开场/回答失败」卡片 */
  show: boolean;
  /** 失败原因文案 */
  reasonText: string;
  /** 下一步指引（含「重试该步骤」按钮的语义说明） */
  hintText: string;
}

/**
 * 开场（opening）或回答（active）阶段的模型失败：Run 已 failed，但审计快照仍停在
 * opening/active（backend/workflows/audit.py:1229-1235、:1350-1356 只捕获 AppError，
 * LLMError 不被 workflow 捕获；runner 只终结 Run、不落审计状态）。
 *
 * 页面不能因此永久停在「审计开场中，暂无待回答的问题」：只要失败 Run 属于当前审计，
 * 就必须给出原因与恢复入口。status 已是 failed 时交给
 * resolveAuditFailureRecoveryState，避免两张失败卡叠加。
 */
export function resolveAuditStepFailureState(
  input: AuditStepFailureInput,
): AuditStepFailureState {
  const running = input.status === "opening" || input.status === "active";
  if (!running || !input.failedRunBelongsToAudit) {
    return { show: false, reasonText: "", hintText: "" };
  }
  const runError = (input.runErrorText ?? "").trim();
  const reasonText = runError
    ? runError
    : input.endReasonRaw
      ? endReasonLabel(input.endReasonRaw)
      : "审计步骤执行失败";
  return {
    show: true,
    reasonText,
    hintText:
      "已提交的回答不会丢失；「重试该步骤」会重新执行失败的审计任务。若反复失败，可返回工作台调整方案后重新开始审计。",
  };
}

// --------------------------------------------------------------------------- //
// 报告过期（旧版本简报生成）(R4 P1-4)
// --------------------------------------------------------------------------- //

export interface AuditStalenessInput {
  /** 报告生成时的产品版本（AuditSnapshot.product_version） */
  auditProductVersion?: number | null;
  /** 当前会话的产品版本（修订简报后 +1） */
  currentProductVersion?: number | null;
}

export interface AuditStalenessState {
  stale: boolean;
  /** stale 为 true 时的提示文案，否则为空串 */
  text: string;
}

/** 报告过期提示前缀，页面据此外加「重新审计」按钮 */
export const AUDIT_STALE_PREFIX = "本报告基于";

/**
 * 旧版本简报生成的审计报告必须显式标注「过期」：
 * 修订已确认简报会回退到 brainstorming 并提示「审计结果已过期」，
 * 但审计页若只显示 `vN` 徽标，用户会把过期结论当成当前版本的问题清单。
 */
export function resolveAuditStaleness(input: AuditStalenessInput): AuditStalenessState {
  const auditVersion = input.auditProductVersion ?? 0;
  const currentVersion = input.currentProductVersion ?? 0;
  if (auditVersion < 1 || currentVersion < 1) {
    return { stale: false, text: "" };
  }
  if (auditVersion >= currentVersion) {
    return { stale: false, text: "" };
  }
  return {
    stale: true,
    text: `${AUDIT_STALE_PREFIX} v${auditVersion} 生成，当前产品已到 v${currentVersion}；如刚修订过简报，建议重新审计。`,
  };
}

// --------------------------------------------------------------------------- //
// 答案校验
// --------------------------------------------------------------------------- //

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
  AUDIT_NOT_READY: "请先完成澄清或跳过引导，再发起审计",
  INTERNAL: "服务暂时不可用，请稍后重试",
  NETWORK_ERROR: "网络连接失败，请检查网络后重试",
  TIMEOUT: "请求超时，请稍后重试",
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

/**
 * 审计是否仍可被控制（终结后按钮禁用）。
 * created/opening 允许 cancel_audit（domain/state_machine.py AUDIT_TRANSITIONS: created/opening → cancelled），
 * 但结束审计只能从 active 发起，因此「结束」按钮单独用 canEndAudit 收紧（PRD-08）。
 */
export function canControlAudit(audit: NormalizedAudit | null): boolean {
  if (!audit) return false;
  return audit.status === "active" || audit.status === "opening" || audit.status === "created";
}

/**
 * 是否可以「结束审计并生成报告」。
 * 后端 validate_audit_transition 只允许 active → closing；created/opening 点击会得到
 * 「非法审计状态迁移」，所以前端提前隐藏并提示「准备中」（PRD-08）。
 */
export function canEndAudit(audit: NormalizedAudit | null): boolean {
  if (!audit) return false;
  return audit.status === "active";
}

/** 审计快照读取失败的统一文案（读取失败不得渲染成「开始审计」，避免误判为还没建审计） */
export const AUDIT_LOAD_ERROR_TEXT = "审计信息读取失败，请重试";
/** 有 auditId 但快照还没回来时的占位文案 */
export const AUDIT_LOADING_TEXT = "正在读取审计信息…";
/** 会话还处于 clarifying（或其它不允许阶段）时的入口替代文案 */
export const AUDIT_NOT_READY_TEXT = "请先完成澄清或跳过引导，再发起审计";
/**
 * 审计/闭环等「已过澄清」阶段却没有审计快照时的文案（DX-06）：
 * 此时再让用户「先完成澄清」是误导，真实原因是记录不存在或已翻篇。
 */
export const AUDIT_NOT_READY_STALE_TEXT = "该审计记录已不存在，请回工作台重新进入审计";

/**
 * 动作失败后是否需要重新同步会话/审计（DX-07）。
 * 用 error.code 判定，不再匹配中文文案；文案只用于展示。
 */
export function shouldResyncAuditAfterError(code: string | null | undefined): boolean {
  return code === "VERSION_CONFLICT" || code === "AUDIT_VERSION_CONFLICT";
}

export interface AuditEntryStateInput {
  /** 当前页面要展示的 auditId（URL 参数或会话快照里的 current_audit_id） */
  auditId: string | null;
  /** 已有可渲染的审计快照 */
  hasAudit: boolean;
  /** store 里记录的读取失败（404 视为「确实没有审计」） */
  auditError: { message: string; notFound: boolean } | null;
  /** 页面是否正在读取审计快照 */
  auditLoading: boolean;
  sessionLoading: boolean;
  legacyMode: boolean;
  starting: boolean;
  /** v2 会话阶段；缺省（旧调用/旧路由）时不拦截 */
  v2Phase?: SessionPhase | null;
  /**
   * 会话快照是否已加载（UX-D3）。审计入口依赖 sessionView.current_audit_id 等字段，
   * 未加载时不能放行「开始审计」；缺省视为 true，保持旧调用语义。
   */
  sessionLoaded?: boolean;
}

export interface AuditEntryState {
  /** 展示「审计信息读取失败，请重试」+ 重试按钮 */
  showLoadError: boolean;
  /** 展示「正在读取审计信息…」占位 */
  showLoading: boolean;
  /** 展示「开始审计」面板 */
  showStartPanel: boolean;
  /** 展示「请先完成澄清或跳过引导」替代态（不渲染「开始审计」） */
  showNotReady: boolean;
  /** 「开始审计」按钮是否可点：仅在没有 auditId、或已确认 404 时才为真 */
  canStart: boolean;
  /** 失败时附带的细节文案 */
  loadErrorText: string | null;
  /** 阶段不满足时的说明文案 */
  notReadyText: string | null;
}

/**
 * 审计页入口状态机：
 * - 有 auditId 且快照未命中、读取又失败（非 404）→ 错误态，禁止展示「开始审计」；
 * - 有 auditId 且还没读完 → 加载态；
 * - 没有 auditId，或已确认 404 → 才允许展示 / 点击「开始审计」；
 * - 会话仍在 clarifying 等不允许阶段 → 替代态，引导先完成澄清。
 */
export function resolveAuditEntryState(input: AuditEntryStateInput): AuditEntryState {
  const hasAuditId = Boolean(input.auditId);
  const error = input.auditError;
  const notFound = Boolean(error && error.notFound);
  const showLoadError = hasAuditId && !input.hasAudit && Boolean(error) && !notFound;
  const wouldStart = !input.hasAudit && !showLoadError && (!hasAuditId || notFound);
  const phaseBlocked = Boolean(input.v2Phase) && !canStartAuditPhase(input.v2Phase);
  const showNotReady = wouldStart && phaseBlocked;
  const showStartPanel = wouldStart && !phaseBlocked;
  const showLoading = !input.hasAudit && !showLoadError && !wouldStart;
  const canStart =
    showStartPanel &&
    !input.starting &&
    !input.sessionLoading &&
    !input.legacyMode &&
    input.sessionLoaded !== false;
  return {
    showLoadError,
    showLoading,
    showStartPanel,
    showNotReady,
    canStart,
    loadErrorText: showLoadError && error ? error.message || AUDIT_LOAD_ERROR_TEXT : null,
    notReadyText: showNotReady
      ? input.v2Phase === "clarifying" || !input.v2Phase
        ? AUDIT_NOT_READY_TEXT
        : AUDIT_NOT_READY_STALE_TEXT
      : null,
  };
}
