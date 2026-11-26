/**
 * 闭环工作室（Loop Studio）视图层纯函数。
 *
 * 为什么单独抽一个模块：Loop 页面的判断（阶段时间线补位、当前轮选择、决策按钮可用性、
 * 快照归一化）都是「有输入就有确定输出」的纯逻辑，放在这里可以用 node --test 直接覆盖，
 * 避免为了测试去装 jsdom/RTL。页面只负责把这里的输出渲染成 Tailwind 组件。
 *
 * 三条硬规则：
 * 1. 绝不 `as` 强转后端数据：所有外部数据先过 `normalizeXxx`，字段缺失/类型不对就退回安全默认值；
 * 2. 绝不编造内容：缺失的产物显示「未生成」，而不是空字符串或假数据；
 * 3. 不 import React / 不依赖 DOM，保证纯函数（tsconfig.test.json 会单独编译本文件）。
 */

import {
  CHECK_RESULTS,
  EFFORT_SIZES,
  FEATURE_PRIORITIES,
  LOOP_ARTIFACT_KINDS,
  LOOP_ROUNDS_DEFAULT,
  LOOP_SCORE_KEYS,
  LOOP_SCORE_MAX,
  LOOP_STAGES,
  LOOP_STAGE_STATUSES,
  LOOP_STATUSES,
  LOOP_TARGET_SCORE_DEFAULT,
  LOOP_TARGET_SCORE_MAX,
  LOOP_TARGET_SCORE_MIN,
  LOOP_VERDICTS,
  MAX_LOOP_DECISION_NOTE_CHARS,
  MAX_LOOP_FOCUS_CHARS,
  MAX_LOOP_ROUNDS,
  MIN_LOOP_ROUNDS,
  TEST_CASE_STATUSES,
} from "./contracts.generated";
import type {
  CheckResult,
  EffortSize,
  FeaturePriority,
  LoopArtifactKind,
  LoopAssumption,
  LoopDecisionKind,
  LoopMvpSpec,
  LoopRoundView,
  LoopScoreCard,
  LoopScoreDimension,
  LoopScoreKey,
  LoopSnapshot,
  LoopStage,
  LoopStageStatus,
  LoopStageView,
  LoopStatus,
  LoopTestCase,
  LoopTestReport,
  LoopValidation,
  LoopVerdict,
  MvpFeature,
  PrototypeFile,
  PrototypeSpec,
  SessionPhase,
  ValidationCheck,
} from "./contracts.generated";

export const NOT_GENERATED = "未生成";

/** 允许开启/继续闭环的会话阶段（与 backend/domain/state_machine.py 一致）。 */
export const LOOP_PHASES = ["brainstorming", "audit_completed"] as const;

// --------------------------------------------------------------------------- //
// 文案表（全部中文；未知 key 一律回退原值，绝不显示 undefined）
// --------------------------------------------------------------------------- //

export const STAGE_LABELS: Record<LoopStage, string> = {
  ideate: "创意发散",
  validate: "假设验证",
  develop: "方案开发",
  test: "测试验证",
  evaluate: "评分复盘",
};

export const STAGE_HINTS: Record<LoopStage, string> = {
  ideate: "把目标拆成可交付的功能清单",
  validate: "用证据检验关键假设与风险",
  develop: "产出可运行的原型代码",
  test: "按用例执行并记录证据",
  evaluate: "七维评分并给出下一步建议",
};

export const STAGE_STATUS_LABELS: Record<LoopStageStatus, string> = {
  pending: "待开始",
  running: "进行中",
  done: "已完成",
  failed: "失败",
  skipped: "已跳过",
};

export const LOOP_STATUS_LABELS: Record<LoopStatus, string> = {
  running: "进行中",
  awaiting_decision: "等待决策",
  shipped: "已交付",
  stopped: "已停止",
  failed: "失败",
};

export const ROUND_STATUS_LABELS: Record<"running" | "done" | "failed", string> = {
  running: "进行中",
  done: "已完成",
  failed: "失败",
};

export const VERDICT_LABELS: Record<LoopVerdict, string> = {
  pending: "待评级",
  pass: "通过",
  iterate: "需迭代",
  blocked: "受阻",
};

export const SCOPE_LABELS: Record<"mvp" | "optimize", string> = {
  mvp: "MVP 闭环",
  optimize: "优化闭环",
};

export const DECISION_LABELS: Record<LoopDecisionKind, string> = {
  iterate: "继续迭代",
  ship: "交付",
  stop: "停止",
};

export const ARTIFACT_TITLES: Record<LoopArtifactKind, string> = {
  mvp_spec: "MVP 规格",
  validation: "假设检验",
  prototype: "原型代码",
  test_report: "测试报告",
  scorecard: "评分卡",
  sandbox_run: "执行记录",
};

export const SCORE_LABELS: Record<LoopScoreKey, string> = {
  functionality: "功能可用性",
  feasibility: "技术可行性",
  user_value: "用户价值",
  risk: "风险与验证",
  delivery: "交付完整度",
  testability: "可测试性",
  docs: "文档与交接",
};

export const PRIORITY_LABELS: Record<FeaturePriority, string> = {
  must: "必须",
  should: "应该",
  could: "可选",
};

export const EFFORT_LABELS: Record<EffortSize, string> = {
  S: "小",
  M: "中",
  L: "大",
};

export const CHECK_RESULT_LABELS: Record<CheckResult, string> = {
  unknown: "待验证",
  pass: "通过",
  fail: "未通过",
};

export const TEST_CASE_STATUS_LABELS: Record<"untested" | "passed" | "failed" | "skipped", string> = {
  untested: "未执行",
  passed: "通过",
  failed: "失败",
  skipped: "跳过",
};

// --------------------------------------------------------------------------- //
// 观感色板（只有语义色名，Tailwind 类名写在页面里，保证 content 扫描得到）
// --------------------------------------------------------------------------- //

export type Tone = "zinc" | "brand" | "emerald" | "amber" | "red";

export function loopStatusTone(status: LoopStatus): Tone {
  switch (status) {
    case "running":
      return "brand";
    case "awaiting_decision":
      return "amber";
    case "shipped":
      return "emerald";
    case "stopped":
      return "zinc";
    case "failed":
      return "red";
    default:
      return "zinc";
  }
}

export function stageStatusTone(status: LoopStageStatus): Tone {
  switch (status) {
    case "running":
      return "brand";
    case "done":
      return "emerald";
    case "failed":
      return "red";
    case "skipped":
      return "zinc";
    case "pending":
    default:
      return "zinc";
  }
}

export function verdictTone(verdict: LoopVerdict): Tone {
  switch (verdict) {
    case "pass":
      return "emerald";
    case "iterate":
      return "amber";
    case "blocked":
      return "red";
    case "pending":
    default:
      return "zinc";
  }
}

export function checkResultTone(result: CheckResult): Tone {
  switch (result) {
    case "pass":
      return "emerald";
    case "fail":
      return "red";
    case "unknown":
    default:
      return "zinc";
  }
}

export function testCaseStatusTone(status: string): Tone {
  switch (status) {
    case "passed":
      return "emerald";
    case "failed":
      return "red";
    case "skipped":
      return "zinc";
    case "untested":
    default:
      return "zinc";
  }
}

// --------------------------------------------------------------------------- //
// 标签函数
// --------------------------------------------------------------------------- //

export function stageLabel(stage: string | null | undefined): string {
  if (!stage) return "未知阶段";
  return (STAGE_LABELS as Record<string, string>)[stage] ?? stage;
}

export function stageHint(stage: string | null | undefined): string {
  if (!stage) return "";
  return (STAGE_HINTS as Record<string, string>)[stage] ?? "";
}

export function stageStatusLabel(status: string | null | undefined): string {
  if (!status) return STAGE_STATUS_LABELS.pending;
  return (STAGE_STATUS_LABELS as Record<string, string>)[status] ?? status;
}

export function statusLabel(status: string | null | undefined): string {
  if (!status) return "未知状态";
  return (LOOP_STATUS_LABELS as Record<string, string>)[status] ?? status;
}

export function roundStatusLabel(status: string | null | undefined): string {
  if (!status) return ROUND_STATUS_LABELS.running;
  return (ROUND_STATUS_LABELS as Record<string, string>)[status] ?? status;
}

export function verdictLabel(verdict: string | null | undefined): string {
  if (!verdict) return VERDICT_LABELS.pending;
  return (VERDICT_LABELS as Record<string, string>)[verdict] ?? verdict;
}

export function scopeLabel(scope: string | null | undefined): string {
  if (!scope) return SCOPE_LABELS.mvp;
  return (SCOPE_LABELS as Record<string, string>)[scope] ?? scope;
}

export function decisionLabel(decision: string | null | undefined): string {
  if (!decision) return "";
  return (DECISION_LABELS as Record<string, string>)[decision] ?? decision;
}

export function artifactTitle(kind: string | null | undefined): string {
  if (!kind) return "产物";
  return (ARTIFACT_TITLES as Record<string, string>)[kind] ?? kind;
}

export function scoreKeyLabel(key: string | null | undefined): string {
  if (!key) return "未知维度";
  return (SCORE_LABELS as Record<string, string>)[key] ?? key;
}

export function priorityLabel(priority: string | null | undefined): string {
  if (!priority) return PRIORITY_LABELS.should;
  return (PRIORITY_LABELS as Record<string, string>)[priority] ?? priority;
}

export function effortLabel(effort: string | null | undefined): string {
  if (!effort) return "M";
  const label = (EFFORT_LABELS as Record<string, string>)[effort];
  return label ? `${effort}（${label}）` : effort;
}

export function checkResultLabel(result: string | null | undefined): string {
  if (!result) return CHECK_RESULT_LABELS.unknown;
  return (CHECK_RESULT_LABELS as Record<string, string>)[result] ?? result;
}

export function testCaseStatusLabel(status: string | null | undefined): string {
  if (!status) return TEST_CASE_STATUS_LABELS.untested;
  return (TEST_CASE_STATUS_LABELS as Record<string, string>)[status] ?? status;
}

// --------------------------------------------------------------------------- //
// 防御性归一化工具
// --------------------------------------------------------------------------- //

function isRecordValue(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function asNullableString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function asNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function asBoolean(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string" && item.trim().length > 0);
}

function clampInt(value: number, min: number, max: number): number {
  const rounded = Math.round(value);
  if (!Number.isFinite(rounded)) return min;
  return Math.min(max, Math.max(min, rounded));
}

export function isKnownValue<T extends string>(values: readonly T[], value: unknown): value is T {
  return typeof value === "string" && (values as readonly string[]).includes(value);
}

// --------------------------------------------------------------------------- //
// 产物归一化
// --------------------------------------------------------------------------- //

function normalizeAssumption(raw: unknown): LoopAssumption | null {
  if (!isRecordValue(raw)) return null;
  const id = asString(raw.id);
  const statement = asString(raw.statement);
  if (!id || !statement) return null;
  return {
    id,
    statement,
    risk: asString(raw.risk),
    verification: asString(raw.verification),
  };
}

function normalizeFeature(raw: unknown): MvpFeature | null {
  if (!isRecordValue(raw)) return null;
  const id = asString(raw.id);
  const title = asString(raw.title);
  if (!id || !title) return null;
  return {
    id,
    title,
    description: asString(raw.description),
    acceptance: asString(raw.acceptance),
    priority: isKnownValue(FEATURE_PRIORITIES, raw.priority) ? raw.priority : "should",
    effort: isKnownValue(EFFORT_SIZES, raw.effort) ? raw.effort : "M",
  };
}

export function normalizeMvpSpec(raw: unknown): LoopMvpSpec | null {
  if (!isRecordValue(raw)) return null;
  const features = (Array.isArray(raw.features) ? raw.features : [])
    .map(normalizeFeature)
    .filter((feature): feature is MvpFeature => feature !== null);
  const assumptions = (Array.isArray(raw.assumptions) ? raw.assumptions : [])
    .map(normalizeAssumption)
    .filter((assumption): assumption is LoopAssumption => assumption !== null);
  return {
    title: asString(raw.title, "MVP 规格"),
    summary: asString(raw.summary),
    features,
    assumptions,
    not_in_scope: asStringArray(raw.not_in_scope),
  };
}

function normalizeCheck(raw: unknown): ValidationCheck | null {
  if (!isRecordValue(raw)) return null;
  const id = asString(raw.id);
  const question = asString(raw.question);
  if (!id || !question) return null;
  return {
    id,
    feature_id: asNullableString(raw.feature_id),
    question,
    result: isKnownValue(CHECK_RESULTS, raw.result) ? raw.result : "unknown",
    evidence_refs: asStringArray(raw.evidence_refs),
    notes: asString(raw.notes),
  };
}

export function normalizeValidation(raw: unknown): LoopValidation | null {
  if (!isRecordValue(raw)) return null;
  const checks = (Array.isArray(raw.checks) ? raw.checks : [])
    .map(normalizeCheck)
    .filter((check): check is ValidationCheck => check !== null);
  return {
    checks,
    summary: asString(raw.summary),
    unknowns: asStringArray(raw.unknowns),
  };
}

function normalizePrototypeFile(raw: unknown): PrototypeFile | null {
  if (!isRecordValue(raw)) return null;
  const path = asString(raw.path);
  if (!path) return null;
  return {
    path,
    language: asString(raw.language, "text"),
    content: asString(raw.content),
    purpose: asString(raw.purpose),
  };
}

export function normalizePrototype(raw: unknown): PrototypeSpec | null {
  if (!isRecordValue(raw)) return null;
  const files = (Array.isArray(raw.files) ? raw.files : [])
    .map(normalizePrototypeFile)
    .filter((file): file is PrototypeFile => file !== null);
  return {
    title: asString(raw.title, "原型代码"),
    summary: asString(raw.summary),
    entry_file: asString(raw.entry_file),
    run_command: asString(raw.run_command),
    files,
    limitations: asString(raw.limitations),
    requires_human_build: asBoolean(raw.requires_human_build, true),
  };
}

function normalizeTestCase(raw: unknown): LoopTestCase | null {
  if (!isRecordValue(raw)) return null;
  const id = asString(raw.id);
  const title = asString(raw.title);
  if (!id || !title) return null;
  return {
    id,
    feature_id: asNullableString(raw.feature_id),
    title,
    steps: asStringArray(raw.steps),
    expected: asString(raw.expected),
    status: isKnownValue(TEST_CASE_STATUSES, raw.status) ? raw.status : "untested",
    evidence: asString(raw.evidence),
  };
}

export function normalizeTestReport(raw: unknown): LoopTestReport | null {
  if (!isRecordValue(raw)) return null;
  const cases = (Array.isArray(raw.cases) ? raw.cases : [])
    .map(normalizeTestCase)
    .filter((testCase): testCase is LoopTestCase => testCase !== null);
  const simulated = asBoolean(raw.simulated, true);
  if (cases.length > 0) {
    const passed = cases.filter((item) => item.status === "passed").length;
    const failed = cases.filter((item) => item.status === "failed").length;
    const skipped = cases.filter((item) => item.status === "skipped").length;
    return {
      total: cases.length,
      passed,
      failed,
      skipped,
      simulated,
      cases,
      notes: asString(raw.notes),
    };
  }
  return {
    total: Math.max(0, Math.round(asNumber(raw.total, 0))),
    passed: Math.max(0, Math.round(asNumber(raw.passed, 0))),
    failed: Math.max(0, Math.round(asNumber(raw.failed, 0))),
    skipped: Math.max(0, Math.round(asNumber(raw.skipped, 0))),
    simulated,
    cases,
    notes: asString(raw.notes),
  };
}

function normalizeScoreDimension(raw: unknown): LoopScoreDimension | null {
  if (!isRecordValue(raw)) return null;
  if (!isKnownValue(LOOP_SCORE_KEYS, raw.key)) return null;
  const key = raw.key;
  const max = clampInt(asNumber(raw.max, LOOP_SCORE_MAX[key]), 1, 100);
  return {
    key,
    score: clampInt(asNumber(raw.score, 0), 0, 100),
    max,
    reason: asString(raw.reason),
  };
}

export function normalizeScoreCard(raw: unknown): LoopScoreCard | null {
  if (!isRecordValue(raw)) return null;
  const dimensions: LoopScoreDimension[] = [];
  const seen = new Set<LoopScoreKey>();
  for (const item of Array.isArray(raw.dimensions) ? raw.dimensions : []) {
    const dimension = normalizeScoreDimension(item);
    if (!dimension || seen.has(dimension.key)) continue;
    seen.add(dimension.key);
    dimensions.push(dimension);
  }
  return {
    total: clampInt(asNumber(raw.total, 0), 0, 100),
    dimensions,
    verdict: isKnownValue(LOOP_VERDICTS, raw.verdict) ? raw.verdict : "pending",
    blockers: asStringArray(raw.blockers),
    highlights: asStringArray(raw.highlights),
    next_actions: asStringArray(raw.next_actions),
    generated_by: raw.generated_by === "harness" ? "harness" : "model",
    hard_capped: asBoolean(raw.hard_capped, false),
  };
}

function normalizeStageView(raw: unknown): LoopStageView | null {
  if (!isRecordValue(raw)) return null;
  if (!isKnownValue(LOOP_STAGES, raw.stage)) return null;
  return {
    stage: raw.stage,
    status: isKnownValue(LOOP_STAGE_STATUSES, raw.status) ? raw.status : "pending",
    summary: asString(raw.summary),
    error_code: asNullableString(raw.error_code),
    updated_at: asString(raw.updated_at),
  };
}

export function normalizeArtifactView(raw: unknown): LoopRoundView["artifacts"][number] | null {
  if (!isRecordValue(raw)) return null;
  const id = asString(raw.id);
  if (!id || !isKnownValue(LOOP_ARTIFACT_KINDS, raw.kind)) return null;
  return {
    id,
    kind: raw.kind,
    title: asString(raw.title, artifactTitle(raw.kind)),
    size_bytes: Math.max(0, Math.round(asNumber(raw.size_bytes, 0))),
    created_at: asString(raw.created_at),
  };
}

export function normalizeRound(raw: unknown): LoopRoundView | null {
  if (!isRecordValue(raw)) return null;
  const id = asString(raw.id);
  const ordinal = Math.round(asNumber(raw.ordinal, 0));
  if (!id || ordinal < 1) return null;
  const stageMap = new Map<LoopStage, LoopStageView>();
  for (const item of Array.isArray(raw.stages) ? raw.stages : []) {
    const stage = normalizeStageView(item);
    if (stage) stageMap.set(stage.stage, stage);
  }
  const stages: LoopStageView[] = LOOP_STAGES.filter((stage) => stageMap.has(stage)).map(
    (stage) => stageMap.get(stage) as LoopStageView
  );
  const artifacts: LoopRoundView["artifacts"] = [];
  for (const item of Array.isArray(raw.artifacts) ? raw.artifacts : []) {
    const artifact = normalizeArtifactView(item);
    if (artifact) artifacts.push(artifact);
  }
  const score = typeof raw.score === "number" && Number.isFinite(raw.score) ? clampInt(raw.score, 0, 100) : null;
  return {
    id,
    ordinal,
    status: isKnownValue(["running", "done", "failed"], raw.status) ? raw.status : "running",
    score,
    verdict: isKnownValue(LOOP_VERDICTS, raw.verdict) ? raw.verdict : "pending",
    stages,
    artifacts,
    started_at: asString(raw.started_at),
    finished_at: asNullableString(raw.finished_at),
  };
}

/**
 * 把后端 LoopSnapshot 归一化成页面可直接消费的结构。
 * 返回 null 表示「没有闭环」（未启动 / 404），页面据此渲染 StartLoopBar。
 */
export function normalizeLoopSnapshot(raw: unknown): LoopSnapshot | null {
  if (!isRecordValue(raw)) return null;
  const loopId = asString(raw.loop_id);
  if (!loopId) return null;
  const rounds: LoopRoundView[] = [];
  const seenOrdinals = new Set<number>();
  for (const item of Array.isArray(raw.rounds) ? raw.rounds : []) {
    const round = normalizeRound(item);
    if (!round || seenOrdinals.has(round.ordinal)) continue;
    seenOrdinals.add(round.ordinal);
    rounds.push(round);
  }
  rounds.sort((left, right) => left.ordinal - right.ordinal);
  return {
    loop_id: loopId,
    session_id: asString(raw.session_id),
    owner_email: asString(raw.owner_email),
    status: isKnownValue(LOOP_STATUSES, raw.status) ? raw.status : "running",
    scope: isKnownValue(["mvp", "optimize"], raw.scope) ? raw.scope : "mvp",
    current_round: clampInt(asNumber(raw.current_round, 1), 1, MAX_LOOP_ROUNDS),
    max_rounds: clampInt(asNumber(raw.max_rounds, LOOP_ROUNDS_DEFAULT), MIN_LOOP_ROUNDS, MAX_LOOP_ROUNDS),
    target_score: clampInt(
      asNumber(raw.target_score, LOOP_TARGET_SCORE_DEFAULT),
      LOOP_TARGET_SCORE_MIN,
      LOOP_TARGET_SCORE_MAX
    ),
    loop_version: clampInt(asNumber(raw.loop_version, 1), 1, Number.MAX_SAFE_INTEGER),
    focus: asString(raw.focus),
    rounds,
    mvp_spec: normalizeMvpSpec(raw.mvp_spec),
    validation: normalizeValidation(raw.validation),
    prototype: normalizePrototype(raw.prototype),
    test_report: normalizeTestReport(raw.test_report),
    scorecard: normalizeScoreCard(raw.scorecard),
    latest_run_id: asNullableString(raw.latest_run_id),
    created_at: asString(raw.created_at),
    updated_at: asString(raw.updated_at),
  };
}

/**
 * 合并 `loop.updated` 局部事件到已有快照。
 *
 * 事件 payload 是「局部更新」（可能只有 status / current_round / stage_status），
 * 因此只覆盖它明确带上的字段；loop_id 不一致时原样返回，绝不串会话。
 * `loop_version` 单调递增，避免乱序事件把版本回退。
 */
export function mergeLoopUpdate(snapshot: LoopSnapshot, rawPayload: unknown): LoopSnapshot {
  if (!isRecordValue(rawPayload)) return snapshot;
  const loopId = asString(rawPayload.loop_id);
  if (loopId && loopId !== snapshot.loop_id) return snapshot;

  const payload = rawPayload;
  const status = isKnownValue(LOOP_STATUSES, payload.status) ? payload.status : snapshot.status;
  const currentRound = clampInt(
    asNumber(payload.current_round, snapshot.current_round),
    1,
    MAX_LOOP_ROUNDS
  );
  const loopVersion = Math.max(
    snapshot.loop_version,
    clampInt(asNumber(payload.loop_version, snapshot.loop_version), 1, Number.MAX_SAFE_INTEGER)
  );
  const score =
    typeof payload.score === "number" && Number.isFinite(payload.score)
      ? clampInt(payload.score, 0, 100)
      : null;
  const verdict = isKnownValue(LOOP_VERDICTS, payload.verdict) ? payload.verdict : null;
  const stage = isKnownValue(LOOP_STAGES, payload.stage) ? payload.stage : null;
  const stageStatus = isKnownValue(LOOP_STAGE_STATUSES, payload.stage_status)
    ? payload.stage_status
    : null;
  const roundStatus = isKnownValue(["running", "done", "failed"], payload.round_status)
    ? payload.round_status
    : null;

  const rounds = snapshot.rounds.map((round) => {
    if (round.ordinal !== currentRound) return round;
    const stages = round.stages.map((item): LoopStageView => {
      if (item.stage !== stage) return item;
      return stageStatus ? { ...item, status: stageStatus } : item;
    });
    return {
      ...round,
      status: roundStatus ?? round.status,
      score: score ?? round.score,
      verdict: verdict ?? round.verdict,
      stages,
    };
  });

  return {
    ...snapshot,
    status,
    current_round: currentRound,
    loop_version: loopVersion,
    rounds,
  };
}

// --------------------------------------------------------------------------- //
// 轮次 / 阶段
// --------------------------------------------------------------------------- //
/** 当前轮：优先 current_round 对应的轮次，缺失时退回最后一轮。 */
export function pickCurrentRound(snapshot: LoopSnapshot | null | undefined): LoopRoundView | null {
  if (!snapshot || snapshot.rounds.length === 0) return null;
  const matched = snapshot.rounds.find((round) => round.ordinal === snapshot.current_round);
  if (matched) return matched;
  return snapshot.rounds[snapshot.rounds.length - 1] ?? null;
}

export interface StageProgress {
  stage: LoopStage;
  index: number;
  label: string;
  hint: string;
  status: LoopStageStatus;
  statusLabel: string;
  tone: Tone;
  summary: string;
  errorCode: string | null;
}

/** 五阶段时间线：未开始的阶段补 pending 占位，保证永远是 5 个节点。 */
export function stageProgress(round: LoopRoundView | null | undefined): StageProgress[] {
  const byStage = new Map<LoopStage, LoopStageView>();
  for (const stage of round?.stages ?? []) byStage.set(stage.stage, stage);
  return LOOP_STAGES.map((stage, index) => {
    const view = byStage.get(stage);
    const status: LoopStageStatus = view?.status ?? "pending";
    return {
      stage,
      index,
      label: STAGE_LABELS[stage],
      hint: STAGE_HINTS[stage],
      status,
      statusLabel: STAGE_STATUS_LABELS[status],
      tone: stageStatusTone(status),
      summary: view?.summary ?? "",
      errorCode: view?.error_code ?? null,
    };
  });
}

export interface RoundProgress {
  done: number;
  total: number;
  failed: number;
  running: number;
  percent: number;
}

export function roundProgress(round: LoopRoundView | null | undefined): RoundProgress {
  const stages = stageProgress(round);
  const done = stages.filter((stage) => stage.status === "done").length;
  const failed = stages.filter((stage) => stage.status === "failed").length;
  const running = stages.filter((stage) => stage.status === "running").length;
  return {
    done,
    total: stages.length,
    failed,
    running,
    percent: Math.round((done / stages.length) * 100),
  };
}

export function currentStageKey(round: LoopRoundView | null | undefined): LoopStage | null {
  const running = stageProgress(round).find((stage) => stage.status === "running");
  if (running) return running.stage;
  const pending = stageProgress(round).find((stage) => stage.status === "pending");
  return pending ? pending.stage : null;
}

/** 「第 2 / 3 轮」；缺失时用 1 / 默认轮次。 */
export function formatRound(currentRound: number | null | undefined, maxRounds: number | null | undefined): string {
  const current = clampInt(asNumber(currentRound, 1), 1, MAX_LOOP_ROUNDS);
  const max = clampInt(asNumber(maxRounds, LOOP_ROUNDS_DEFAULT), MIN_LOOP_ROUNDS, MAX_LOOP_ROUNDS);
  return `第 ${current} / ${max} 轮`;
}

export function roundScoreText(round: LoopRoundView | null | undefined): string {
  if (!round || round.score === null) return NOT_GENERATED;
  return `${round.score}/100`;
}

// --------------------------------------------------------------------------- //
// 历史轮次回看与多轮对比（R4 P1-3）
// --------------------------------------------------------------------------- //

/**
 * 选中轮次：优先用户选中的 ordinal，其次快照 current_round，最后退回最后一轮。
 * 越界/已删除的 ordinal 必须回落，不能渲染空白页。
 */
export function selectRound(
  rounds: LoopRoundView[] | null | undefined,
  selectedOrdinal: number | null | undefined,
  currentRound: number | null | undefined
): LoopRoundView | null {
  if (!rounds || rounds.length === 0) return null;
  if (typeof selectedOrdinal === "number" && Number.isFinite(selectedOrdinal)) {
    const matched = rounds.find((round) => round.ordinal === selectedOrdinal);
    if (matched) return matched;
  }
  if (typeof currentRound === "number" && Number.isFinite(currentRound)) {
    const matched = rounds.find((round) => round.ordinal === currentRound);
    if (matched) return matched;
  }
  return rounds[rounds.length - 1] ?? null;
}

export interface RoundSwitcherOption {
  ordinal: number;
  label: string;
  scoreText: string;
  statusLabel: string;
  active: boolean;
}

/** 轮次切换条：每轮一个按钮（第 N 轮 · 状态 · 分数），按 ordinal 升序。 */
export function roundSwitcherOptions(
  rounds: LoopRoundView[] | null | undefined,
  selectedOrdinal: number | null | undefined
): RoundSwitcherOption[] {
  return [...(rounds ?? [])]
    .sort((a, b) => a.ordinal - b.ordinal)
    .map((round) => ({
      ordinal: round.ordinal,
      label: `第 ${round.ordinal} 轮`,
      scoreText: roundScoreText(round),
      statusLabel: ROUND_STATUS_LABELS[round.status] ?? round.status,
      active: selectedOrdinal ? round.ordinal === selectedOrdinal : false,
    }));
}

export interface RoundComparisonRow {
  key: LoopScoreKey;
  label: string;
  before: number | null;
  after: number | null;
  delta: number | null;
}

export interface RoundComparison {
  hasPrevious: boolean;
  previousOrdinal: number | null;
  currentOrdinal: number | null;
  /** 「72 → 78（+6）」；缺分显示「未生成」 */
  scoreDeltaText: string;
  scoreDelta: number | null;
  /** 「需迭代 → 通过」 */
  verdictText: string;
  verdictChanged: boolean;
  /** 产物索引差异（新增 / 缺少） */
  artifactDeltaText: string;
  dimensionRows: RoundComparisonRow[];
  summary: string;
}

function scoreOf(round: LoopRoundView | null | undefined, card: LoopScoreCard | null | undefined): number | null {
  if (card && typeof card.total === "number") return card.total;
  if (round && typeof round.score === "number") return round.score;
  return null;
}

function signedDelta(delta: number): string {
  if (delta === 0) return "±0";
  return delta > 0 ? `+${delta}` : `${delta}`;
}

/**
 * 「对比上一轮」：总分、七维分差、verdict 变化、产物索引差异。
 * 没有上一轮时 hasPrevious=false，页面不渲染对比条（不编造对比数据）。
 */
/**
 * 闭环下拉候选：当前会话的历史列表 + 已缓存快照，按 loop_id 去重、按创建时间倒序。
 *
 * 缓存（store.loops）是跨会话保留的，若不过滤会把别的会话的闭环显示成本会话历史
 * （FE-DX-02）。因此这里只接受 `session_id` 与当前会话一致的缓存条目。
 */
export function selectLoopChoices(
  history: LoopSnapshot[] | null | undefined,
  cached: Record<string, LoopSnapshot> | null | undefined,
  sessionId: string | null | undefined,
): LoopSnapshot[] {
  const map = new Map<string, LoopSnapshot>();
  for (const item of history ?? []) {
    if (!item) continue;
    if (sessionId && item.session_id !== sessionId) continue;
    map.set(item.loop_id, item);
  }
  for (const item of Object.values(cached ?? {})) {
    if (!item) continue;
    if (sessionId && item.session_id !== sessionId) continue;
    map.set(item.loop_id, item);
  }
  return [...map.values()].sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""));
}


export function compareRounds(
  previous: LoopRoundView | null | undefined,
  current: LoopRoundView | null | undefined,
  previousCard?: LoopScoreCard | null,
  currentCard?: LoopScoreCard | null
): RoundComparison {
  const empty: RoundComparison = {
    hasPrevious: false,
    previousOrdinal: previous?.ordinal ?? null,
    currentOrdinal: current?.ordinal ?? null,
    scoreDeltaText: "",
    scoreDelta: null,
    verdictText: "",
    verdictChanged: false,
    artifactDeltaText: "",
    dimensionRows: [],
    summary: "",
  };
  if (!previous || !current) return empty;

  const before = scoreOf(previous, previousCard);
  const after = scoreOf(current, currentCard);
  const delta = before !== null && after !== null ? after - before : null;
  const scoreDeltaText =
    before === null && after === null
      ? "两轮均未生成评分"
      : `${before === null ? NOT_GENERATED : `${before}/100`} → ${
          after === null ? NOT_GENERATED : `${after}/100`
        }${delta === null ? "" : `（${signedDelta(delta)}）`}`;

  const beforeVerdict = verdictLabel(previous.verdict);
  const afterVerdict = verdictLabel(current.verdict);
  const verdictChanged = previous.verdict !== current.verdict;

  const beforeKinds = new Set((previous.artifacts ?? []).map((artifact) => artifact.kind));
  const afterKinds = new Set((current.artifacts ?? []).map((artifact) => artifact.kind));
  const added = LOOP_ARTIFACT_KINDS.filter((kind) => afterKinds.has(kind) && !beforeKinds.has(kind));
  const missing = LOOP_ARTIFACT_KINDS.filter((kind) => beforeKinds.has(kind) && !afterKinds.has(kind));
  const artifactDeltaText =
    beforeKinds.size === 0 && afterKinds.size === 0
      ? "两轮产物索引均为空"
      : added.length === 0 && missing.length === 0
        ? "产物索引无变化"
        : [
            added.length > 0 ? `新增：${added.map((kind) => ARTIFACT_TITLES[kind]).join("、")}` : null,
            missing.length > 0 ? `本轮缺少：${missing.map((kind) => ARTIFACT_TITLES[kind]).join("、")}` : null,
          ]
            .filter(Boolean)
            .join("；");

  const beforeRows = new Map(scoreDimensionRows(previousCard).map((row) => [row.key, row.score]));
  const afterRows = new Map(scoreDimensionRows(currentCard).map((row) => [row.key, row.score]));
  // 七维固定输出（缺失维度显示「—」），避免两轮维度集合不同时行数跳动
  const dimensionRows: RoundComparisonRow[] = LOOP_SCORE_KEYS.map((key) => {
    const beforeScore = beforeRows.get(key);
    const afterScore = afterRows.get(key);
    return {
      key,
      label: scoreKeyLabel(key),
      before: beforeScore ?? null,
      after: afterScore ?? null,
      delta:
        beforeScore !== undefined && afterScore !== undefined ? afterScore - beforeScore : null,
    };
  });

  const summary =
    delta === null
      ? `第 ${previous.ordinal} 轮 → 第 ${current.ordinal} 轮，评分不完整，无法计算分差`
      : delta > 0
        ? `第 ${previous.ordinal} 轮 → 第 ${current.ordinal} 轮，总分提升 ${signedDelta(delta)}`
        : delta < 0
          ? `第 ${previous.ordinal} 轮 → 第 ${current.ordinal} 轮，总分下降 ${Math.abs(delta)}`
          : `第 ${previous.ordinal} 轮 → 第 ${current.ordinal} 轮，总分持平`;

  return {
    hasPrevious: true,
    previousOrdinal: previous.ordinal,
    currentOrdinal: current.ordinal,
    scoreDeltaText,
    scoreDelta: delta,
    verdictText: `${beforeVerdict} → ${afterVerdict}`,
    verdictChanged,
    artifactDeltaText,
    dimensionRows,
    summary,
  };
}

/** 对比条的 tone：涨=emerald、跌=red、平=zinc */
export function comparisonTone(delta: number | null | undefined): Tone {
  if (delta === null || delta === undefined || delta === 0) return "zinc";
  return delta > 0 ? "emerald" : "red";
}

// --------------------------------------------------------------------------- //
// 评分卡
// --------------------------------------------------------------------------- //

/** 评分卡总分：有卡片显示 `78/100`，没有卡片显示「未生成」。 */
export function formatScore(card: LoopScoreCard | null | undefined): string {
  if (!card) return NOT_GENERATED;
  return `${clampInt(card.total, 0, 100)}/100`;
}

export interface ScoreDimensionRow {
  key: LoopScoreKey;
  label: string;
  score: number;
  max: number;
  percent: number;
  reason: string;
}

/** 七维评分条：按契约固定顺序输出，缺失维度直接不渲染（不补 0 分假数据）。 */
export function scoreDimensionRows(card: LoopScoreCard | null | undefined): ScoreDimensionRow[] {
  if (!card) return [];
  const byKey = new Map<LoopScoreKey, LoopScoreDimension>();
  for (const dimension of card.dimensions) {
    if (!byKey.has(dimension.key)) byKey.set(dimension.key, dimension);
  }
  const rows: ScoreDimensionRow[] = [];
  for (const key of LOOP_SCORE_KEYS) {
    const dimension = byKey.get(key);
    if (!dimension) continue;
    const max = Math.max(1, dimension.max);
    rows.push({
      key,
      label: SCORE_LABELS[key],
      score: dimension.score,
      max,
      percent: Math.min(100, Math.max(0, Math.round((dimension.score / max) * 100))),
      reason: dimension.reason,
    });
  }
  return rows;
}

export function scoreDimensionMaxTotal(): number {
  return LOOP_SCORE_KEYS.reduce((sum, key) => sum + LOOP_SCORE_MAX[key], 0);
}

// --------------------------------------------------------------------------- //
// 决策条
// --------------------------------------------------------------------------- //

export interface DecisionOptionState {
  enabled: boolean;
  reason: string;
}

export interface DecisionAvailability {
  awaiting: boolean;
  iterate: DecisionOptionState;
  ship: DecisionOptionState;
  stop: DecisionOptionState;
}

/**
 * 三个决策按钮的可用性：
 * - 只有 `awaiting_decision` 才能决策；
 * - 会话阶段必须是 brainstorming / audit_completed（与后端
 *   backend/domain/state_machine.py:42 的 ACTION_PHASES[loop_decision] 对齐），
 *   审计进行中时按钮禁用并给出原因，避免「可点但必被 INVALID_REQUEST」；
 * - `iterate` 还要求 current_round < max_rounds（与后端 LOOP_ROUNDS_EXHAUSTED 对齐）；
 * - ship / stop 在等待决策且阶段允许时始终可点。
 */
export function decisionAvailability(
  snapshot: LoopSnapshot | null | undefined,
  phase?: SessionPhase | null
): DecisionAvailability {
  if (!snapshot) {
    const reason = "闭环尚未启动";
    return {
      awaiting: false,
      iterate: { enabled: false, reason },
      ship: { enabled: false, reason },
      stop: { enabled: false, reason },
    };
  }
  if (snapshot.status !== "awaiting_decision") {
    const reason =
      snapshot.status === "running"
        ? "当前轮仍在执行，暂不能决策"
        : "闭环已结束，无需再决策";
    return {
      awaiting: false,
      iterate: { enabled: false, reason },
      ship: { enabled: false, reason },
      stop: { enabled: false, reason },
    };
  }
  const exhausted = snapshot.current_round >= snapshot.max_rounds;
  if (phase && !(LOOP_PHASES as readonly string[]).includes(phase)) {
    const reason =
      phase === "auditing"
        ? "审计进行中，请先在审计页结束审计，再继续迭代/交付/停止"
        : "当前阶段不能决策，请回到脑暴或结束审计";
    return {
      awaiting: true,
      iterate: { enabled: false, reason },
      ship: { enabled: false, reason },
      stop: { enabled: false, reason },
    };
  }
  return {
    awaiting: true,
    iterate: exhausted
      ? {
          enabled: false,
          reason: `轮次已用尽（${snapshot.current_round}/${snapshot.max_rounds}），只能交付或停止`,
        }
      : { enabled: true, reason: "" },
    ship: { enabled: true, reason: "" },
    stop: { enabled: true, reason: "" },
  };
}

/** 决策备注：去掉首尾空白并截断到契约上限，避免超长请求。 */
export function normalizeDecisionNote(note: string | null | undefined): string {
  if (!note) return "";
  return note.trim().slice(0, MAX_LOOP_DECISION_NOTE_CHARS);
}

// --------------------------------------------------------------------------- //
// 阻塞项 / 错误文案
// --------------------------------------------------------------------------- //

export function blockerList(blockers: readonly string[] | null | undefined): string[] {
  if (!blockers) return [];
  return blockers.filter((item) => typeof item === "string" && item.trim().length > 0);
}

/** 阻塞项摘要；没有阻塞项时明确说「暂无阻塞项」，不留空白。 */
export function blockerText(blockers: readonly string[] | null | undefined): string {
  const items = blockerList(blockers);
  if (items.length === 0) return "暂无阻塞项";
  return `共 ${items.length} 个阻塞项：${items.join("；")}`;
}

const LOOP_ERROR_TEXT: Record<string, string> = {
  LOOP_ACTIVE: "已有进行中的闭环，正在切换到该闭环",
  LOOP_NOT_AWAITING_DECISION: "闭环当前不在决策阶段，请刷新后重试",
  LOOP_ROUNDS_EXHAUSTED: "轮次已用尽，只能交付或停止",
  LOOP_CLAIMED: "该轮闭环已被其他任务领取，请稍后刷新",
  LOOP_VERSION_CONFLICT: "闭环状态已在别处更新，请刷新后重试",
  INVALID_REQUEST: "当前阶段不能执行该决策，请先结束审计或回到脑暴阶段",
  VERSION_CONFLICT: "内容已在别处更新，请刷新后重试",
  NOT_FOUND: "闭环不存在或已删除",
  FORBIDDEN: "没有权限查看该闭环",
  NETWORK_ERROR: "网络连接失败，请检查网络后重试",
  TIMEOUT: "请求超时，请稍后重试",
};

export function loopErrorText(code: string | null | undefined, message?: string | null): string {
  if (code && LOOP_ERROR_TEXT[code]) return LOOP_ERROR_TEXT[code];
  const trimmed = (message ?? "").trim();
  if (trimmed) return trimmed;
  return "操作失败，请稍后重试";
}

// --------------------------------------------------------------------------- //
// 产物 Tab
// --------------------------------------------------------------------------- //

export interface ArtifactTabState {
  kind: LoopArtifactKind;
  title: string;
  available: boolean;
}

/**
 * 本轮五类产物的固定顺序。
 *
 * `sandbox_run` 是第 38 批新增的「执行记录」产物，由沙箱面板单独展示，
 * 不属于闭环五阶段的结构化产物，因此不进入 Tab。
 */
const ROUND_ARTIFACT_KINDS: LoopArtifactKind[] = [
  "mvp_spec",
  "validation",
  "prototype",
  "test_report",
  "scorecard",
];

/** 五类产物 Tab 是否已有内容：看快照里的产物对象，或本轮 artifacts 列表。 */
export function artifactTabs(
  snapshot: LoopSnapshot | null | undefined,
  round?: LoopRoundView | null
): ArtifactTabState[] {
  const present = new Set<LoopArtifactKind>();
  if (snapshot) {
    if (snapshot.mvp_spec) present.add("mvp_spec");
    if (snapshot.validation) present.add("validation");
    if (snapshot.prototype) present.add("prototype");
    if (snapshot.test_report) present.add("test_report");
    if (snapshot.scorecard) present.add("scorecard");
  }
  for (const artifact of round?.artifacts ?? []) present.add(artifact.kind);
  return ROUND_ARTIFACT_KINDS.map((kind) => ({
    kind,
    title: ARTIFACT_TITLES[kind],
    available: present.has(kind),
  }));
}

export function defaultArtifactTab(
  snapshot: LoopSnapshot | null | undefined,
  round?: LoopRoundView | null
): LoopArtifactKind {
  const tabs = artifactTabs(snapshot, round);
  const firstAvailable = tabs.find((tab) => tab.available);
  return firstAvailable ? firstAvailable.kind : "mvp_spec";
}

export function hasArtifact(snapshot: LoopSnapshot | null | undefined, kind: LoopArtifactKind): boolean {
  return artifactTabs(snapshot).some((tab) => tab.kind === kind && tab.available);
}

export function formatBytes(size: number | null | undefined): string {
  const bytes = typeof size === "number" && Number.isFinite(size) ? Math.max(0, size) : 0;
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** 时间戳展示：按用户本地时区格式化到分钟，避免把 UTC ISO 原文当本地时间。 */
export function formatTimestamp(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
    hour12: false,
  }).format(date);
}

// --------------------------------------------------------------------------- //
// 启动表单
// --------------------------------------------------------------------------- //

export interface StartLoopFormInput {
  scope?: string | null;
  maxRounds?: number | string | null;
  targetScore?: number | string | null;
  focus?: string | null;
}

export interface StartLoopFormResult {
  ok: boolean;
  scope: "mvp" | "optimize";
  max_rounds: number;
  target_score: number;
  focus: string;
  error: string | null;
}

function toInteger(value: number | string | null | undefined, fallback: number): number {
  if (typeof value === "number" && Number.isFinite(value)) return Math.round(value);
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return Math.round(parsed);
  }
  return fallback;
}

/** 启动表单归一化：轮次 1..5、目标分 60..100、聚焦说明 ≤2000 字。 */
export function validateStartForm(input: StartLoopFormInput): StartLoopFormResult {
  const scope = input.scope === "optimize" ? "optimize" : "mvp";
  const maxRounds = clampInt(toInteger(input.maxRounds, LOOP_ROUNDS_DEFAULT), MIN_LOOP_ROUNDS, MAX_LOOP_ROUNDS);
  const targetScore = clampInt(
    toInteger(input.targetScore, LOOP_TARGET_SCORE_DEFAULT),
    LOOP_TARGET_SCORE_MIN,
    LOOP_TARGET_SCORE_MAX
  );
  const focus = typeof input.focus === "string" ? input.focus.trim() : "";
  const tooLong = focus.length > MAX_LOOP_FOCUS_CHARS;
  return {
    ok: !tooLong,
    scope,
    max_rounds: maxRounds,
    target_score: targetScore,
    focus,
    error: tooLong ? `聚焦说明最多 ${MAX_LOOP_FOCUS_CHARS} 字` : null,
  };
}

// --------------------------------------------------------------------------- //
// 页面入口状态
// --------------------------------------------------------------------------- //

export function canStartLoopPhase(phase: string | null | undefined): boolean {
  return isKnownValue(LOOP_PHASES, phase);
}

export function shouldShowLoopEntry(input: {
  v2Phase?: string | null;
  legacyMode: boolean;
}): boolean {
  if (input.legacyMode) return false;
  return canStartLoopPhase(input.v2Phase ?? null);
}

export interface LoopEntryInput {
  sessionLoading: boolean;
  /**
   * 会话快照是否已加载（DX-02）。缺省视为 true（保持旧调用语义）；
   * 显式 false 表示「会话已读完但仍然没有快照」——这是读取失败，不是阶段不满足。
   */
  sessionLoaded?: boolean;
  /**
   * 会话读取失败：sessionSettled && !sessionView && !sessionLoading（DX-R3-03）。
   * 与 sessionLoaded 拆开，避免把「加载中」也误判成读取失败。
   */
  sessionLoadFailed?: boolean;
  sessionPhase?: string | null;
  legacyMode: boolean;
  loop: LoopSnapshot | null;
  loopLoading: boolean;
  loopError?: { message: string; notFound?: boolean } | null;
}

export interface LoopEntryState {
  showLoading: boolean;
  showGate: boolean;
  gateText: string;
  showStartPanel: boolean;
  showLoop: boolean;
  showLoadError: boolean;
  loadErrorText: string;
}

/** 决定 Loop 页面首屏渲染哪一块：加载中 / 阶段不允许 / 加载失败 / 启动表单 / 闭环主体。 */
export function resolveLoopEntryState(input: LoopEntryInput): LoopEntryState {
  const loading = {
    showLoading: true,
    showGate: false,
    gateText: "",
    showStartPanel: false,
    showLoop: false,
    showLoadError: false,
    loadErrorText: "",
  };
  if (input.legacyMode) {
    return {
      ...loading,
      showLoading: false,
      showGate: true,
      gateText: "旧版会话不支持闭环工作室",
    };
  }
  if (input.loop) {
    return { ...loading, showLoading: false, showLoop: true };
  }
  if (input.loopLoading) return loading;
  // 会话尚未加载时 phase 一定是 null，不能据此判定「阶段不允许」；
  // 否则首屏会先闪一块「暂不可用」，文案却还是「正在加载会话…」。
  if (input.sessionLoading) return loading;
  // 会话数据缺失 ≠ 阶段不满足：读取失败时必须给「重试」入口，否则用户永远无法恢复（DX-02）
  // DX-R3-03：优先用显式的 sessionLoadFailed；旧调用仍兼容 sessionLoaded === false
  if (input.sessionLoadFailed ?? input.sessionLoaded === false) {
    return {
      ...loading,
      showLoading: false,
      showLoadError: true,
      loadErrorText: "会话状态读取失败，请重试",
    };
  }
  if (!canStartLoopPhase(input.sessionPhase ?? null)) {
    return {
      ...loading,
      showLoading: false,
      showGate: true,
      gateText: "需要先完成头脑风暴或审计，才能开启闭环工作室",
    };
  }
  if (input.loopError) {
    if (input.loopError.notFound) {
      return {
        ...loading,
        showLoading: false,
        showStartPanel: true,
        loadErrorText: "闭环不存在或已删除，可重新启动",
      };
    }
    return {
      ...loading,
      showLoading: false,
      showLoadError: true,
      loadErrorText: input.loopError.message,
    };
  }
  return { ...loading, showLoading: false, showStartPanel: true };
}

/** 顶部状态条文案。 */
export function loopStatusText(snapshot: LoopSnapshot | null | undefined): string {
  if (!snapshot) return "未启动";
  return statusLabel(snapshot.status);
}

/**
 * 轮次失败后是否可重试本轮（PRD-07）。
 * 后端 retry_run 只接受 loop.status ∈ {running, failed} 且 loop.latest_run_id 指向失败 Run
 * （backend/services/command_service.py retry_run），因此这里必须同时满足两个条件。
 */
export function canRetryLoopRound(snapshot: LoopSnapshot | null | undefined): boolean {
  if (!snapshot) return false;
  if (snapshot.status !== "failed") return false;
  return Boolean(snapshot.latest_run_id);
}

/** 评分卡 verdict 展示文案；没有评分卡时明确「未评级」。 */
/**
 * 硬性封顶的解释文案：只有「硬性封顶」徽标而没有原因，用户无法判断该修什么（P2）。
 * 返回空串表示没有封顶或没有评分卡。
 */
export function scoreCapNotice(card: LoopScoreCard | null | undefined): string {
  if (!card || !card.hard_capped) return "";
  const blockers = (card.blockers ?? []).filter((item) => String(item ?? "").trim().length > 0);
  const verdict = String(card.verdict ?? "");
  if (blockers.length > 0) {
    return `因存在 ${blockers.length} 条阻塞项，总分按规则封顶（未达目标分不代表可交付）`;
  }
  if (verdict === "blocked" || verdict === "fail") {
    return "因存在未解决的阻塞项 / 致命问题，总分按规则封顶";
  }
  return "评分命中硬性规则（阻塞项或 P0 问题），总分被封顶";
}

export function scoreVerdictText(snapshot: LoopSnapshot | null | undefined): string {
  if (!snapshot || !snapshot.scorecard) return VERDICT_LABELS.pending;
  return verdictLabel(snapshot.scorecard.verdict);
}

export interface TestReportStats {
  total: number;
  passed: number;
  failed: number;
  skipped: number;
  passRate: number;
}

export function testReportStats(report: LoopTestReport | null | undefined): TestReportStats {
  if (!report) return { total: 0, passed: 0, failed: 0, skipped: 0, passRate: 0 };
  const total = Math.max(0, report.total);
  return {
    total,
    passed: report.passed,
    failed: report.failed,
    skipped: report.skipped,
    passRate: total > 0 ? Math.round((report.passed / total) * 100) : 0,
  };
}
