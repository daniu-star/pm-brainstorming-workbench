"use client";

import { useEffect, useState } from "react";
import { getQuota, quotaSummary, resetInText, type QuotaView } from "@/lib/quota";

/**
 * 顶栏的紧凑额度徽标：只在账户确实设了上限时出现（未设上限不占位）。
 * 悬停可看到具体用量与恢复时间，点进设置页看完整卡片。
 */
export function QuotaBadge() {
  const [view, setView] = useState<QuotaView | null>(null);

  useEffect(() => {
    let alive = true;
    const controller = new AbortController();
    getQuota(controller.signal)
      .then((next) => {
        if (alive) setView(next);
      })
      .catch(() => {
        // 顶栏徽标属于锦上添花，读不到就安静地不显示。
      });
    return () => {
      alive = false;
      controller.abort();
    };
  }, []);

  if (!view || !view.enabled) return null;

  const summary = quotaSummary(view);
  const reset = resetInText(view.reset_at);
  const title = [summary.detail, reset, summary.kind === "exceeded" ? "额度已用尽" : null]
    .filter(Boolean)
    .join(" · ");
  const tone =
    summary.kind === "exceeded"
      ? "bg-red-500/10 text-red-300 border-red-500/30"
      : summary.kind === "warning"
        ? "bg-amber-500/10 text-amber-300 border-amber-500/30"
        : "bg-zinc-800/60 text-zinc-400 border-zinc-700/60";

  return (
    <span
      className={`hidden items-center rounded-full border px-2 py-0.5 text-[11px] md:inline-flex ${tone}`}
      title={title}
      aria-label={`模型额度：${summary.label}`}
      data-quota-kind={summary.kind}
    >
      {summary.label}
    </span>
  );
}