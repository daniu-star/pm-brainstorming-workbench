"use client";

/**
 * 账户模型额度（quota）的客户端调用与展示用纯函数。
 *
 * 额度按滚动 24 小时统计（`ACCOUNT_QUOTA_WINDOW_SECONDS`）：超出后异步处理命令会被
 * 后端以 429 `ACCOUNT_QUOTA_EXCEEDED` 拒绝，直到窗口滑过。上限为 0 表示不限。
 * 见 `GET /api/v2/quota` 与 `backend/services/quota_service.py`。
 */
import { apiV2 } from "@/lib/api";

export interface QuotaView {
  enabled: boolean;
  window_seconds: number;
  window_hours: number;
  reset_at: string | null;
  token_limit: number;
  call_limit: number;
  used_tokens: number;
  reserved_tokens: number;
  total_tokens: number;
  used_calls: number;
  reserved_calls: number;
  total_calls: number;
  scanned_runs: number;
  exceeded: boolean;
}

export function getQuota(signal?: AbortSignal): Promise<QuotaView> {
  return apiV2<QuotaView>("/api/v2/quota", { signal });
}

export type QuotaKind = "unlimited" | "ok" | "warning" | "exceeded";

export interface QuotaSummary {
  kind: QuotaKind;
  /** 已用百分比（0–100，取 token / 调用两项中较高者）。 */
  percent: number;
  tokenPercent: number;
  callPercent: number;
  /** 紧凑场景用：例如「额度 42%」「额度已用尽」「未设上限」。 */
  label: string;
  /** 说明用：例如「已用 12.3k / 30k tokens · 调用 4 / 10 次」。 */
  detail: string;
}

/** 把 token 数压成 1.2k / 3.4m，便于在窄条里显示。 */
export function formatTokens(value: number): string {
  const count = Math.max(0, Math.round(Number(value) || 0));
  if (count < 1000) return String(count);
  if (count < 1_000_000) {
    const k = count / 1000;
    return `${k >= 100 || Number.isInteger(k) ? Math.round(k) : k.toFixed(1)}k`;
  }
  const m = count / 1_000_000;
  return `${m >= 100 || Number.isInteger(m) ? Math.round(m) : m.toFixed(1)}m`;
}

function percentOf(used: number, limit: number): number {
  if (!limit || limit <= 0) return 0;
  const raw = (Math.max(0, used) / limit) * 100;
  return Math.min(100, Math.max(0, Math.round(raw)));
}

//: 到达这个百分比就提示「快用完了」，留出收尾余地。
export const QUOTA_WARNING_PERCENT = 80;

export function quotaSummary(view: QuotaView): QuotaSummary {
  const tokenPercent = percentOf(view.total_tokens, view.token_limit);
  const callPercent = percentOf(view.total_calls, view.call_limit);
  const percent = Math.max(tokenPercent, callPercent);
  const detailParts: string[] = [];
  if (view.token_limit > 0) {
    detailParts.push(`已用 ${formatTokens(view.total_tokens)} / ${formatTokens(view.token_limit)} tokens`);
  } else {
    detailParts.push(`已用 ${formatTokens(view.total_tokens)} tokens`);
  }
  if (view.call_limit > 0) {
    detailParts.push(`调用 ${view.total_calls} / ${view.call_limit} 次`);
  } else {
    detailParts.push(`调用 ${view.total_calls} 次`);
  }
  const detail = detailParts.join(" · ");

  if (!view.enabled) {
    return {
      kind: "unlimited",
      percent: 0,
      tokenPercent: 0,
      callPercent: 0,
      label: "未设上限",
      detail,
    };
  }
  if (view.exceeded) {
    return { kind: "exceeded", percent: 100, tokenPercent, callPercent, label: "额度已用尽", detail };
  }
  return {
    kind: percent >= QUOTA_WARNING_PERCENT ? "warning" : "ok",
    percent,
    tokenPercent,
    callPercent,
    label: `额度 ${percent}%`,
    detail,
  };
}

/** 「额度重置时间」文案：`约 3 小时 12 分后恢复`（无法解析时返回 null）。 */
export function resetInText(resetAt: string | null | undefined, nowMs: number = Date.now()): string | null {
  if (!resetAt) return null;
  const target = Date.parse(resetAt);
  if (Number.isNaN(target)) return null;
  const diffSeconds = Math.round((target - nowMs) / 1000);
  if (diffSeconds <= 0) return "即将恢复";
  // 先归到分钟再拆小时，避免 3599 秒被算成「23 小时 60 分钟」。
  const totalMinutes = Math.max(1, Math.round(diffSeconds / 60));
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (hours <= 0) return `约 ${minutes} 分钟后恢复`;
  if (minutes === 0) return `约 ${hours} 小时后恢复`;
  return `约 ${hours} 小时 ${minutes} 分钟后恢复`;
}