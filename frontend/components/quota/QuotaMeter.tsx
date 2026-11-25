"use client";

import { useEffect, useState } from "react";
import { SignalIcon } from "@/components/icons";
import { formatTokens, getQuota, quotaSummary, resetInText, type QuotaView } from "@/lib/quota";

/**
 * 账户模型额度卡片：把「滚动 24 小时用了多少」画出来，避免用户只在被 429 拒绝时才知道超限。
 * 上限由部署方通过 `ACCOUNT_DAILY_TOKEN_LIMIT` / `ACCOUNT_DAILY_CALL_LIMIT` 设置，0 表示不限。
 */
export function QuotaMeter() {
  const [view, setView] = useState<QuotaView | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    const controller = new AbortController();
    getQuota(controller.signal)
      .then((next) => {
        if (alive) setView(next);
      })
      .catch(() => {
        if (alive) setFailed(true);
      });
    return () => {
      alive = false;
      controller.abort();
    };
  }, []);

  const summary = view ? quotaSummary(view) : null;
  const reset = view ? resetInText(view.reset_at) : null;
  const barColor =
    summary?.kind === "exceeded"
      ? "bg-red-400/80"
      : summary?.kind === "warning"
        ? "bg-amber-300/80"
        : "bg-brand-300/80";

  return (
    <section className="rounded-2xl border border-zinc-700/60 bg-dark-800/70 p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-zinc-200">模型额度与用量</h2>
        {summary ? (
          <span
            className={`rounded-full px-2 py-0.5 text-[11px] ${
              summary.kind === "exceeded"
                ? "bg-red-500/10 text-red-300"
                : summary.kind === "warning"
                  ? "bg-amber-500/10 text-amber-300"
                  : "bg-zinc-700/40 text-zinc-400"
            }`}
          >
            {summary.label}
          </span>
        ) : null}
      </div>

      {failed ? (
        <p className="mt-3 text-xs text-zinc-500">额度信息读取失败，稍后刷新页面再试。</p>
      ) : null}

      {!failed && !view ? <p className="mt-3 text-xs text-zinc-500">正在读取额度…</p> : null}

      {view && summary ? (
        <>
          <p className="mt-1 text-xs text-zinc-500">
            按最近 {view.window_hours} 小时滚动统计（token 与调用次数都算），到点自动恢复。
          </p>

          <div className="mt-4 space-y-3">
            <QuotaRow
              label="token"
              used={formatTokens(view.total_tokens)}
              limit={view.token_limit > 0 ? formatTokens(view.token_limit) : "不限"}
              percent={summary.tokenPercent}
              barColor={barColor}
            />
            <QuotaRow
              label="调用次数"
              used={String(view.total_calls)}
              limit={view.call_limit > 0 ? String(view.call_limit) : "不限"}
              percent={summary.callPercent}
              barColor={barColor}
            />
          </div>

          {reset ? <p className="mt-3 text-[11px] text-zinc-500">{reset}</p> : null}
          {view.reserved_tokens > 0 || view.reserved_calls > 0 ? (
            <p className="mt-1 text-[11px] text-zinc-600">
              其中排队/执行中的任务预占 {formatTokens(view.reserved_tokens)} tokens、{view.reserved_calls} 次调用。
            </p>
          ) : null}

          {summary.kind === "exceeded" ? (
            <p className="mt-3 flex items-start gap-1.5 rounded-lg bg-red-500/10 px-3 py-2 text-[11px] leading-5 text-red-300">
              <SignalIcon size={14} className="mt-0.5 shrink-0" />
              额度已用尽：需要模型的处理会被拒绝；改稿、登记证据、确认决策、取消与结束审计仍可继续。
            </p>
          ) : null}
          {summary.kind === "unlimited" ? (
            <p className="mt-3 text-[11px] text-zinc-600">平台未对该账户设置额度上限，此处仅展示实际用量。</p>
          ) : null}
        </>
      ) : null}
    </section>
  );
}

function QuotaRow({
  label,
  used,
  limit,
  percent,
  barColor,
}: {
  label: string;
  used: string;
  limit: string;
  percent: number;
  barColor: string;
}) {
  return (
    <div>
      <div className="flex items-baseline justify-between text-[11px] text-zinc-400">
        <span>{label}</span>
        <span className="text-zinc-300">
          {used} / {limit}
        </span>
      </div>
      <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-zinc-800">
        <div className={`h-full rounded-full ${barColor}`} style={{ width: `${percent}%` }} />
      </div>
    </div>
  );
}