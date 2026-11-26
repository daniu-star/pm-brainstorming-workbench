"use client";

/**
 * `/ops` 运维健康页的数据层（第 38 批）。
 *
 * 数据源是后端 `GET /health/detail`（`backend/main.py::health_detail`），
 * 这里只做**展示口径的纯函数**：把各块 JSON 翻成表格行 / 文案 / 色调，
 * 方便单测（不依赖浏览器）与页面复用。
 */
import { api } from "@/lib/api";
import { formatBytes } from "@/lib/loopView";

export type OpsTone = "zinc" | "emerald" | "amber" | "red";

export interface OpsStorageBlock {
  db: boolean;
  db_bytes: number;
  counts: Record<string, number>;
}

export interface OpsQueueBlock {
  queued: number;
  running: number;
  oldest_queued_age_seconds: number | null;
}

export interface OpsSandboxBlock {
  enabled: boolean;
  allowed_commands: string[];
  timeout_seconds: number;
  max_output_bytes: number;
  note: string;
}

export interface OpsWorkerBlock {
  db: boolean;
  running_runs: number;
  queued_runs: number;
  heartbeat_age_seconds: number | null;
  stalled: boolean;
  hint: string | null;
}

export interface OpsLlmBlock {
  model: string;
  base_url_host: string;
  disable_reasoning: boolean;
  timeout_seconds: number;
  max_retries: number;
}

export interface OpsReportQualityBlock {
  db: boolean;
  window: string;
  verdict: string;
  hint: string | null;
}

export interface HealthDetail {
  status: string;
  services?: Record<string, unknown>;
  worker?: OpsWorkerBlock;
  budget_profile?: string;
  budget_limits?: Record<string, number>;
  llm?: OpsLlmBlock;
  report_quality?: OpsReportQualityBlock;
  storage?: OpsStorageBlock;
  queues?: OpsQueueBlock;
  sandbox?: OpsSandboxBlock;
}

/** 表格行的统一形状（`keyvalue` 组件与 `<table>` 都好用）。 */
export interface OpsRow {
  key: string;
  label: string;
  value: string;
}

const COUNT_LABELS: Record<string, string> = {
  sessions: "会话",
  messages: "消息",
  runs: "任务 Run",
  steps: "步骤",
  events: "事件",
  evidence: "证据",
  loops: "闭环",
  loop_artifacts: "闭环产物",
};

export const STATUS_LABELS: Record<string, string> = {
  ok: "正常",
  degraded: "降级",
  error: "异常",
};

export function statusTone(status: string | null | undefined): OpsTone {
  if (status === "ok") return "emerald";
  if (status === "degraded") return "amber";
  if (status === "error") return "red";
  return "zinc";
}

/** 存储块 → 行（库大小 + 8 张主表的行数）。 */
export function storageRows(storage: OpsStorageBlock | null | undefined): OpsRow[] {
  if (!storage || !storage.db) return [];
  const rows: OpsRow[] = [{ key: "db_bytes", label: "库大小", value: formatBytes(storage.db_bytes) }];
  for (const [key, label] of Object.entries(COUNT_LABELS)) {
    const value = storage.counts?.[key];
    if (typeof value === "number") {
      rows.push({ key, label: `${label}行数`, value: value.toLocaleString("zh-CN") });
    }
  }
  return rows;
}

/** 队列深度 → 行；最久排队时长单独格式化（None → 无排队）。 */
export function queueRows(queues: OpsQueueBlock | null | undefined): OpsRow[] {
  if (!queues) return [];
  const age = queues.oldest_queued_age_seconds;
  return [
    { key: "queued", label: "排队中", value: String(queues.queued) },
    { key: "running", label: "运行中", value: String(queues.running) },
    {
      key: "oldest",
      label: "最久排队",
      value: typeof age === "number" ? formatSeconds(age) : "无",
    },
  ];
}

export function formatSeconds(seconds: number | null | undefined): string {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) return "—";
  if (seconds < 60) return `${Math.round(seconds)} 秒`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  if (minutes < 60) return rest === 0 ? `${minutes} 分钟` : `${minutes} 分 ${rest} 秒`;
  const hours = Math.floor(minutes / 60);
  return `${hours} 小时 ${minutes % 60} 分`;
}

/** Worker 存活摘要：卡死时必须一眼看出「排队但没有 Worker」。 */
export function workerSummary(worker: OpsWorkerBlock | null | undefined): { text: string; tone: OpsTone } {
  if (!worker || !worker.db) return { text: "读不到 Worker 状态", tone: "zinc" };
  if (worker.stalled) {
    return { text: worker.hint ?? "有任务排队但没有 Worker 在跑", tone: "red" };
  }
  if (worker.queued_runs > 0) {
    return { text: `${worker.queued_runs} 个任务排队中，Worker 正常领取`, tone: "amber" };
  }
  const age = worker.heartbeat_age_seconds;
  const suffix = typeof age === "number" ? `，最近心跳 ${formatSeconds(age)}前` : "";
  return { text: `空闲${suffix}`, tone: "emerald" };
}

export const REPORT_QUALITY_LABELS: Record<string, string> = {
  no_data: "还没有报告",
  insufficient: "样本不足",
  clean: "没有未验证数字",
  occasional: "偶有未验证数字",
  frequent: "经常出现未验证数字",
};

export function reportQualitySummary(
  block: OpsReportQualityBlock | null | undefined,
): { text: string; tone: OpsTone } {
  if (!block || !block.db) return { text: "读不到报告质量", tone: "zinc" };
  const label = REPORT_QUALITY_LABELS[block.verdict] ?? block.verdict;
  const window = block.window ? `（窗口 ${block.window}）` : "";
  const tone: OpsTone =
    block.verdict === "clean" ? "emerald" : block.verdict === "frequent" ? "red" : "amber";
  return { text: `${label}${window}`, tone };
}

/** 沙箱开关摘要：`已开启 · 白名单 python、node · 超时 20 秒`。 */
export function sandboxSummary(sandbox: OpsSandboxBlock | null | undefined): {
  text: string;
  tone: OpsTone;
} {
  if (!sandbox) return { text: "读不到沙箱配置", tone: "zinc" };
  if (!sandbox.enabled) return { text: "未开启（fail-closed）", tone: "zinc" };
  const allowed = sandbox.allowed_commands.length > 0 ? sandbox.allowed_commands.join("、") : "空白名单";
  return {
    text: `已开启 · 白名单 ${allowed} · 超时 ${sandbox.timeout_seconds} 秒 · 单次输出上限 ${formatBytes(
      sandbox.max_output_bytes,
    )}`,
    tone: sandbox.allowed_commands.length > 0 ? "emerald" : "amber",
  };
}

/** 模型配置摘要（不回显 Key，只显示模型名与主机）。 */
export function llmSummary(llm: OpsLlmBlock | null | undefined): OpsRow[] {
  if (!llm) return [];
  return [
    { key: "model", label: "模型", value: llm.model || "—" },
    { key: "host", label: "上游主机", value: llm.base_url_host || "—" },
    { key: "reasoning", label: "关闭推理", value: llm.disable_reasoning ? "是" : "否" },
    { key: "timeout", label: "单次超时", value: `${llm.timeout_seconds} 秒` },
    { key: "retries", label: "重试次数", value: String(llm.max_retries) },
  ];
}

/** 预算档位摘要：`lean · 上游调用 6 次 / 输出 900 tokens`。 */
export function budgetSummary(
  profile: string | null | undefined,
  limits: Record<string, number> | null | undefined,
): string {
  const name = profile || "standard";
  if (!limits || Object.keys(limits).length === 0) return `${name}（契约默认）`;
  const parts: string[] = [];
  if (typeof limits.max_upstream_calls === "number") parts.push(`上游调用 ${limits.max_upstream_calls} 次`);
  if (typeof limits.max_output_tokens === "number") parts.push(`输出 ${limits.max_output_tokens} tokens`);
  return parts.length > 0 ? `${name} · ${parts.join(" · ")}` : name;
}

export async function getHealthDetail(options: { signal?: AbortSignal } = {}): Promise<HealthDetail> {
  return api<HealthDetail>("/health/detail", { signal: options.signal });
}
