"use client";

/**
 * `/ops` 运维健康页（第 38 批）。
 *
 * 只读聚合 `GET /health/detail`：整体状态、Worker 存活、队列深度、存储用量、
 * 模型配置、报告质量、沙箱开关。目的是「出问题时不用登服务器也能定位」——
 * 例如「发消息一直没反应」先看 Worker 是否 stalled，「执行按钮是灰的」看沙箱块。
 */
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { ArrowRightIcon, SignalIcon } from "@/components/icons";
import { errorMessage } from "@/lib/api";
import {
  budgetSummary,
  getHealthDetail,
  llmSummary,
  queueRows,
  reportQualitySummary,
  sandboxSummary,
  statusTone,
  STATUS_LABELS,
  storageRows,
  workerSummary,
  type HealthDetail,
  type OpsRow,
  type OpsTone,
} from "@/lib/ops";
import { formatTimestamp } from "@/lib/loopView";

const TONE_TEXT: Record<OpsTone, string> = {
  zinc: "text-zinc-400",
  emerald: "text-emerald-300",
  amber: "text-amber-300",
  red: "text-red-400",
};

const TONE_CHIP: Record<OpsTone, string> = {
  zinc: "border-zinc-700/60 bg-zinc-800/40 text-zinc-300",
  emerald: "border-emerald-700/40 bg-emerald-900/20 text-emerald-300",
  amber: "border-amber-700/40 bg-amber-900/20 text-amber-300",
  red: "border-red-700/40 bg-red-900/20 text-red-300",
};

function Badge({ text, tone = "zinc" }: { text: string; tone?: OpsTone }) {
  return (
    <span className={`rounded-full border px-2 py-0.5 text-[10px] leading-relaxed ${TONE_CHIP[tone]}`}>
      {text}
    </span>
  );
}

function Card({
  title,
  badge,
  tone = "zinc",
  children,
}: {
  title: string;
  badge?: string;
  tone?: OpsTone;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-2xl border border-zinc-800/70 bg-dark-800/60 p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-zinc-100">{title}</h2>
        {badge ? <Badge text={badge} tone={tone} /> : null}
      </div>
      {children}
    </section>
  );
}

function RowTable({ rows, emptyText = "暂无数据" }: { rows: OpsRow[]; emptyText?: string }) {
  if (rows.length === 0) {
    return <p className="text-[11px] leading-relaxed text-zinc-500">{emptyText}</p>;
  }
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-1 sm:grid-cols-2">
      {rows.map((row) => (
        <div key={row.key} className="flex items-baseline justify-between gap-3 border-b border-zinc-800/40 py-1">
          <dt className="text-[11px] text-zinc-500">{row.label}</dt>
          <dd className="font-mono text-[11px] text-zinc-200">{row.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function OpsPageContent() {
  const [detail, setDetail] = useState<HealthDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loadedAt, setLoadedAt] = useState<string | null>(null);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const body = await getHealthDetail();
      setDetail(body);
      setLoadError(null);
      setLoadedAt(new Date().toISOString());
    } catch (err) {
      setLoadError(errorMessage(err));
      // 读取失败时清空 detail：宁可显示「—」，也不能拿上次的成功数据冒充当前状态。
      setDetail(null);
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // 第 42 批：detail 为 null 时以前会渲染「Worker 正常 / 排队 0 / 模型 standard」——
  // 故障时误导值班判断。这里显式区分 loading / error / ready 三态。
  const ready = detail !== null;
  const pendingText = busy && !ready ? "读取中…" : void 0;
  const overall = detail?.status ?? null;
  const worker = workerSummary(detail?.worker);
  const reportQuality = reportQualitySummary(detail?.report_quality);
  const sandbox = sandboxSummary(detail?.sandbox);

  return (
    <main id="main-content" className="mx-auto flex min-h-dvh w-full max-w-5xl flex-col gap-5 px-4 py-10">
      <header className="flex flex-col gap-2">
        <Link
          href="/"
          className="inline-flex w-fit items-center gap-1 text-xs text-zinc-500 transition-colors hover:text-zinc-300"
        >
          <ArrowRightIcon size={14} className="rotate-180" />
          返回工作台
        </Link>
        <div className="flex flex-wrap items-center gap-2">
          <SignalIcon size={22} className="text-brand-300" />
          <h1 className="text-xl font-semibold text-zinc-100">运维健康</h1>
          <Badge
            text={overall ? STATUS_LABELS[overall] ?? overall : "读取中…"}
            tone={statusTone(overall)}
          />
          <button
            type="button"
            onClick={() => void load()}
            disabled={busy}
            className="ml-auto min-h-[36px] rounded-lg border border-zinc-700/60 px-3 text-[11px] text-zinc-300 disabled:opacity-50"
          >
            {busy ? "刷新中…" : "刷新"}
          </button>
        </div>
        <p className="text-sm leading-6 text-zinc-400">
          只读聚合 <code className="rounded bg-black/40 px-1 text-[11px]">GET /health/detail</code>
          ：Worker 存活、队列深度、存储用量、模型配置、报告质量与沙箱开关。
          {loadedAt ? ` 最近读取：${formatTimestamp(loadedAt)}。` : ""}
        </p>
      </header>

      {loadError ? (
        <p role="alert" className="rounded-xl border border-red-700/40 bg-red-900/20 px-4 py-3 text-[11px] leading-relaxed text-red-300">
          {loadError}
          {loadError.includes("请先登录") || loadError.includes("登录") ? "（请先登录再访问 /ops）" : ""}
        </p>
      ) : null}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Card
          title="Worker"
          badge={ready ? (worker.tone === "red" ? "需要处理" : "正常") : pendingText ?? "—"}
          tone={ready ? worker.tone : "zinc"}
        >
          <p className={`text-[11px] leading-relaxed ${TONE_TEXT[worker.tone]}`}>{worker.text}</p>
          {detail?.worker?.hint ? (
            <p className="mt-2 rounded-lg border border-zinc-800/60 bg-black/30 px-2 py-1 text-[10px] leading-relaxed text-zinc-400">
              {detail.worker.hint}
            </p>
          ) : null}
        </Card>

        <Card
          title="队列"
          badge={ready ? `排队 ${detail.queues?.queued ?? 0}` : pendingText ?? "—"}
          tone={ready && (detail.queues?.queued ?? 0) > 0 ? "amber" : "zinc"}
        >
          <RowTable rows={queueRows(detail?.queues)} />
        </Card>

        <Card
          title="存储"
          badge={ready ? (detail.storage?.db ? "可读" : "不可用") : pendingText ?? "—"}
          tone={ready && !detail.storage?.db ? "red" : "zinc"}
        >
          <RowTable rows={storageRows(detail?.storage)} emptyText="读不到数据库用量" />
        </Card>

        <Card
          title="沙箱"
          badge={ready ? (detail.sandbox?.enabled ? "已开启" : "未开启") : pendingText ?? "—"}
          tone={ready ? sandbox.tone : "zinc"}
        >
          <p className={`text-[11px] leading-relaxed ${TONE_TEXT[sandbox.tone]}`}>{sandbox.text}</p>
          {detail?.sandbox?.note ? (
            <p className="mt-2 text-[10px] leading-relaxed text-zinc-500">{detail.sandbox.note}</p>
          ) : null}
        </Card>

        <Card title="模型" badge={ready ? (detail.budget_profile ?? "standard") : pendingText ?? "—"}>
          <RowTable rows={llmSummary(detail?.llm)} emptyText="读不到模型配置" />
          <p className="mt-2 text-[10px] leading-relaxed text-zinc-500">
            预算档位：{budgetSummary(detail?.budget_profile, detail?.budget_limits)}
          </p>
        </Card>

        <Card
          title="报告质量"
          badge={ready ? reportQuality.text : pendingText ?? "—"}
          tone={ready ? reportQuality.tone : "zinc"}
        >
          <p className={`text-[11px] leading-relaxed ${TONE_TEXT[reportQuality.tone]}`}>
            {detail?.report_quality?.hint ?? "报告里的数字是否都有来源，会在这里汇总成信号。"}
          </p>
        </Card>
      </div>

      <section className="rounded-xl border border-zinc-800/80 bg-dark-900/60 p-4 text-[11px] leading-6 text-zinc-500">
        <p className="font-medium text-zinc-400">常见问题的排查顺序</p>
        <ol className="mt-1 list-decimal space-y-1 pl-5">
          <li>「发消息一直没反应」→ 看 Worker 是否 stalled；stalled 时按提示重启 Worker。</li>
          <li>「更新很慢」→ 看队列排队数与最久排队时长，确认 Worker 数量是否够。</li>
          <li>「执行按钮是灰的」→ 看沙箱块：未开启或白名单为空都会让按钮禁用。</li>
          <li>「报告里全是没来源的数字」→ 看报告质量 verdict 是否 frequent，必要时补证据。</li>
        </ol>
      </section>
    </main>
  );
}

export default function OpsPage() {
  return <OpsPageContent />;
}
