"use client";

/**
 * 闭环工作室（Loop Studio）页面。
 *
 * 数据来源全部是 store 里缓存的 LoopSnapshot：
 * - 首屏：`loadSession` → `sessionView.current_loop_id` → `hydrateLoopById`；
 * - 运行中：`loop.updated` 事件由 store 合并并兜底重新拉取快照；
 * - 决策后：`loop_decision` 回执里的 Run 被订阅，Run 收尾时 `finalizeRun` 会再刷新一次。
 *
 * 页面只做「渲染 + 交互」，所有判断都走 `lib/loopView.ts` 的纯函数，方便 node --test 覆盖。
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { LayersIcon } from "@/components/icons";
import { toast } from "@/components/Toast";
import { isApiError, getLoopArtifact, getLoopRound, listLoops } from "@/lib/api";
import { useSessionStore } from "@/store/sessionStore";
import type {
  LoopArtifactKind,
  LoopDecisionKind,
  LoopMvpSpec,
  LoopRoundView,
  LoopScoreCard,
  LoopSnapshot,
  LoopTestCase,
  LoopTestReport,
  LoopValidation,
  PrototypeSpec,
  SessionPhase,
  ValidationCheck,
  MvpFeature,
} from "@/lib/contracts.generated";
import { NOT_GENERATED, SCOPE_LABELS, artifactTabs, artifactTitle, blockerList, canRetryLoopRound, checkResultLabel, checkResultTone, compareRounds, comparisonTone, currentStageKey, decisionAvailability, defaultArtifactTab, effortLabel, formatBytes, formatRound, formatScore, formatTimestamp, loopErrorText, loopStatusText, loopStatusTone, normalizeMvpSpec, normalizePrototype, normalizeScoreCard, normalizeTestReport, normalizeValidation, priorityLabel, resolveLoopEntryState, roundProgress, roundScoreText, roundSwitcherOptions, scoreCapNotice, scoreDimensionRows, scoreVerdictText, selectLoopChoices, selectRound, stageProgress, testReportStats, type RoundComparison, type RoundSwitcherOption, type Tone, validateStartForm, verdictLabel, verdictTone } from "@/lib/loopView";

// --------------------------------------------------------------------------- //
// Tailwind 色板映射（类名必须是字面量，Tailwind content 扫描才认）
// --------------------------------------------------------------------------- //

const TONE_TEXT: Record<Tone, string> = {
  zinc: "text-zinc-400",
  brand: "text-brand-300",
  emerald: "text-emerald-300",
  amber: "text-amber-300",
  red: "text-red-400",
};

const TONE_CHIP: Record<Tone, string> = {
  zinc: "border-zinc-600/60 bg-zinc-800/40 text-zinc-300",
  brand: "border-brand-500/40 bg-brand-600/15 text-brand-300",
  emerald: "border-emerald-700/40 bg-emerald-900/20 text-emerald-300",
  amber: "border-amber-700/40 bg-amber-900/20 text-amber-300",
  red: "border-red-800/40 bg-red-900/20 text-red-400",
};

const TONE_DOT: Record<Tone, string> = {
  zinc: "border-zinc-600/70 bg-dark-900 text-zinc-500",
  brand: "border-brand-400 bg-brand-500/20 text-brand-200 animate-pulse",
  emerald: "border-emerald-500/70 bg-emerald-500/15 text-emerald-300",
  amber: "border-amber-500/70 bg-amber-500/15 text-amber-300",
  red: "border-red-500/70 bg-red-500/15 text-red-300",
};

const TONE_BAR: Record<Tone, string> = {
  zinc: "bg-zinc-600",
  brand: "bg-brand-500",
  emerald: "bg-emerald-500",
  amber: "bg-amber-500",
  red: "bg-red-500",
};

const BTN_PRIMARY =
  "min-h-[44px] rounded-lg border border-brand-500/40 bg-brand-600/20 px-4 text-sm font-medium text-brand-300 transition-colors hover:bg-brand-600/30 disabled:cursor-not-allowed disabled:opacity-50";
const BTN_SECONDARY =
  "min-h-[40px] rounded-lg border border-zinc-600/60 px-3 text-xs font-medium text-zinc-200 transition-colors hover:bg-zinc-700/30 disabled:cursor-not-allowed disabled:opacity-50";
const BTN_AMBER =
  "min-h-[40px] rounded-lg border border-amber-600/50 px-3 text-xs font-medium text-amber-200 transition-colors hover:bg-amber-800/30 disabled:cursor-not-allowed disabled:opacity-50";
const BTN_DANGER =
  "min-h-[40px] rounded-lg border border-red-700/60 bg-red-900/20 px-3 text-xs font-medium text-red-300 transition-colors hover:bg-red-900/30 disabled:cursor-not-allowed disabled:opacity-50";
const INPUT_CLASS =
  "w-full rounded-lg border border-zinc-700/60 bg-dark-900/60 px-2.5 py-2 text-xs text-zinc-200 outline-none transition-colors focus:border-brand-500/60";

// --------------------------------------------------------------------------- //
// 通用小组件
// --------------------------------------------------------------------------- //

function Badge({ text, tone = "zinc" }: { text: string; tone?: Tone }) {
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium ${TONE_CHIP[tone]}`}
    >
      {text}
    </span>
  );
}

function Section({
  title,
  extra,
  children,
  sticky = false,
}: {
  title: string;
  extra?: React.ReactNode;
  children: React.ReactNode;
  /** 吸底：决策条在长页面里必须始终可见（否则用户要滚到最底部才能做决策） */
  sticky?: boolean;
}) {
  return (
    <section
      className={[
        "rounded-xl border border-zinc-700/40 bg-dark-800/40 p-4",
        sticky
          ? "sticky bottom-2 z-20 border-zinc-600/60 bg-dark-900/95 shadow-[0_-10px_28px_-14px_rgba(0,0,0,0.9)] backdrop-blur"
          : "",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <div className="mb-2 flex items-center gap-2">
        <h3 className="text-xs font-semibold tracking-wide text-zinc-400">{title}</h3>
        {extra ? <div className="ml-auto flex items-center gap-2">{extra}</div> : null}
      </div>
      {children}
    </section>
  );
}

function EmptyHint({ text }: { text: string }) {
  return <p className="text-[11px] text-zinc-500">{text}</p>;
}

function StringList({ items, emptyText }: { items: string[]; emptyText?: string }) {
  if (items.length === 0) return <EmptyHint text={emptyText ?? NOT_GENERATED} />;
  return (
    <ul className="space-y-1.5">
      {items.map((item, index) => (
        <li key={`${index}-${item}`} className="text-[11px] leading-relaxed text-zinc-300">
          · {item}
        </li>
      ))}
    </ul>
  );
}

function Spinner() {
  return (
    <span className="h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
  );
}

function ProgressBar({ percent, tone = "brand" }: { percent: number; tone?: Tone }) {
  const width = Math.min(100, Math.max(0, Math.round(percent)));
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-zinc-800">
      <div className={`h-full rounded-full ${TONE_BAR[tone]}`} style={{ width: `${width}%` }} />
    </div>
  );
}

// --------------------------------------------------------------------------- //
// 顶部概要
// --------------------------------------------------------------------------- //

function LoopSummary({
  snapshot,
  round,
  isCurrent,
}: {
  snapshot: LoopSnapshot;
  round: LoopRoundView | null;
  isCurrent: boolean;
}) {
  const stageKey = currentStageKey(round);
  const skeleton = Boolean(round && round.started_at === "");
  return (
    <Section
      title="闭环概要"
      extra={
        <>
          <Badge text={SCOPE_LABELS[snapshot.scope]} />
          <Badge text={loopStatusText(snapshot)} tone={loopStatusTone(snapshot.status)} />
          {isCurrent ? null : <Badge text="历史闭环 · 只读" tone="amber" />}
        </>
      }
    >
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        <div>
          <p className="text-[10px] text-zinc-500">当前轮 / 总轮</p>
          <p className="mt-0.5 text-sm font-semibold text-zinc-100">
            {formatRound(snapshot.current_round, snapshot.max_rounds)}
          </p>
        </div>
        <div>
          <p className="text-[10px] text-zinc-500">目标分</p>
          <p className="mt-0.5 text-sm font-semibold text-zinc-100">{snapshot.target_score}</p>
        </div>
        <div>
          <p className="text-[10px] text-zinc-500">查看轮次</p>
          <p className="mt-0.5 text-sm font-semibold text-zinc-100">
            {round ? `第 ${round.ordinal} 轮` : NOT_GENERATED}
          </p>
        </div>
        <div>
          <p className="text-[10px] text-zinc-500">该轮评分</p>
          <p className="mt-0.5 text-sm font-semibold text-zinc-100">{roundScoreText(round)}</p>
        </div>
        <div>
          <p className="text-[10px] text-zinc-500">当前阶段</p>
          <p className="mt-0.5 text-sm font-semibold text-zinc-100">
            {stageKey ? stageProgress(round).find((item) => item.stage === stageKey)?.label ?? "—" : "—"}
          </p>
        </div>
      </div>
      {skeleton ? (
        <p className="mt-3 rounded-lg border border-zinc-700/40 bg-dark-900/40 px-3 py-2 text-[11px] leading-relaxed text-zinc-400">
          暂无记录：闭环尚未推进到第 {round?.ordinal ?? snapshot.current_round} 轮，该轮还没有阶段与产物数据。
        </p>
      ) : null}
      {snapshot.focus ? (
        <p className="mt-3 rounded-lg border border-zinc-700/40 bg-dark-900/40 px-3 py-2 text-[11px] leading-relaxed text-zinc-300">
          <span className="text-zinc-500">聚焦：</span>
          {snapshot.focus}
        </p>
      ) : null}
      <p className="mt-2 text-[10px] text-zinc-600">
        最近更新 {formatTimestamp(snapshot.updated_at)}
        {snapshot.latest_run_id ? ` · Run ${snapshot.latest_run_id}` : ""}
      </p>
    </Section>
  );
}

// --------------------------------------------------------------------------- //
// 轮次切换 / 多轮对比（R4 ③）
// --------------------------------------------------------------------------- //

function RoundSwitcher({
  options,
  onSelect,
}: {
  options: RoundSwitcherOption[];
  onSelect: (ordinal: number) => void;
}) {
  if (options.length <= 1) return null;
  return (
    <Section
      title="轮次回看"
      extra={<span className="text-[10px] text-zinc-500">共 {options.length} 轮 · 历史轮只读</span>}
    >
      <div className="flex flex-wrap gap-1.5">
        {options.map((option) => (
          <button
            key={option.ordinal}
            type="button"
            onClick={() => onSelect(option.ordinal)}
            aria-pressed={option.active}
            className={`flex min-h-[40px] flex-col items-start justify-center rounded-xl border px-3 text-left transition-colors ${
              option.active
                ? "border-brand-500/50 bg-brand-600/15 text-brand-200"
                : "border-zinc-700/60 text-zinc-300 hover:border-zinc-600 hover:text-zinc-100"
            }`}
          >
            <span className="text-[11px] font-medium">{option.label}</span>
            <span className="text-[10px] text-zinc-500">
              {option.statusLabel} · {option.scoreText}
            </span>
          </button>
        ))}
      </div>
    </Section>
  );
}

function RoundComparisonBar({ comparison }: { comparison: RoundComparison }) {
  if (!comparison.hasPrevious) return null;
  const tone = TONE_TEXT[comparisonTone(comparison.scoreDelta)];
  return (
    <Section
      title="对比上一轮"
      extra={
        <Badge
          text={`第 ${comparison.previousOrdinal} 轮 → 第 ${comparison.currentOrdinal} 轮`}
          tone={comparisonTone(comparison.scoreDelta)}
        />
      }
    >
      <p className={`text-[11px] font-medium ${tone}`}>{comparison.summary}</p>
      <div className="mt-2 grid gap-1.5 sm:grid-cols-2">
        <p className="rounded-lg border border-zinc-700/40 bg-dark-900/40 px-2.5 py-2 text-[11px] text-zinc-300">
          <span className="text-zinc-500">总分：</span>
          {comparison.scoreDeltaText}
        </p>
        <p className="rounded-lg border border-zinc-700/40 bg-dark-900/40 px-2.5 py-2 text-[11px] text-zinc-300">
          <span className="text-zinc-500">结论：</span>
          {comparison.verdictText}
          {comparison.verdictChanged ? <span className="ml-1 text-amber-300">（已变化）</span> : null}
        </p>
        <p className="rounded-lg border border-zinc-700/40 bg-dark-900/40 px-2.5 py-2 text-[11px] text-zinc-300 sm:col-span-2">
          <span className="text-zinc-500">产物：</span>
          {comparison.artifactDeltaText}
        </p>
      </div>
      <div className="mt-2 space-y-1">
        {comparison.dimensionRows.map((row) => (
          <div key={row.key} className="flex items-center gap-2 text-[10px]">
            <span className="w-20 shrink-0 text-zinc-400">{row.label}</span>
            <span className="text-zinc-500">
              {row.before === null ? "—" : row.before} → {row.after === null ? "—" : row.after}
            </span>
            <span
              className={`ml-auto ${
                row.delta === null || row.delta === 0
                  ? "text-zinc-600"
                  : row.delta > 0
                    ? "text-emerald-300"
                    : "text-red-400"
              }`}
            >
              {row.delta === null ? "无对比" : row.delta > 0 ? `+${row.delta}` : `${row.delta}`}
            </span>
          </div>
        ))}
      </div>
    </Section>
  );
}

// --------------------------------------------------------------------------- //
// 阶段时间线
// --------------------------------------------------------------------------- //

function StageTimeline({ round }: { round: LoopRoundView | null }) {
  const stages = stageProgress(round);
  const progress = roundProgress(round);
  return (
    <Section
      title="阶段时间线"
      extra={<span className="text-[10px] text-zinc-500">已完成 {progress.done}/{progress.total}</span>}
    >
      <ol className="flex items-start gap-1">
        {stages.map((stage, index) => (
          <li key={stage.stage} className="min-w-0 flex-1">
            <div className="flex items-center">
              <span
                className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-[10px] font-medium ${TONE_DOT[stage.tone]}`}
              >
                {index + 1}
              </span>
              {index < stages.length - 1 ? (
                <span
                  className={`h-px flex-1 ${
                    stage.status === "done" ? "bg-emerald-600/50" : "bg-zinc-700/60"
                  }`}
                />
              ) : null}
            </div>
            <p className="mt-1.5 truncate text-[11px] font-medium text-zinc-200" title={stage.hint}>
              {stage.label}
            </p>
            <p className={`text-[10px] ${TONE_TEXT[stage.tone]}`}>{stage.statusLabel}</p>
            {stage.summary ? (
              <p className="mt-0.5 line-clamp-2 text-[10px] leading-relaxed text-zinc-500">{stage.summary}</p>
            ) : null}
            {stage.errorCode ? (
              <p className="mt-0.5 break-all text-[10px] leading-relaxed text-red-400/80">
                {`错误码：${stage.errorCode}`}
              </p>
            ) : null}
          </li>
        ))}
      </ol>
      <div className="mt-3">
        <ProgressBar percent={progress.percent} tone={progress.failed > 0 ? "red" : "brand"} />
        {progress.failed > 0 ? (
          <p className="mt-1 text-[10px] text-red-400">有 {progress.failed} 个阶段失败，可继续迭代或停止闭环</p>
        ) : null}
      </div>
    </Section>
  );
}

// --------------------------------------------------------------------------- //
// 五类产物
// --------------------------------------------------------------------------- //

function FeatureTable({ features }: { features: MvpFeature[] }) {
  if (features.length === 0) return <EmptyHint text="MVP 规格里没有功能项" />;
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-[11px]">
        <thead>
          <tr className="text-left text-zinc-500">
            <th className="border-b border-zinc-700/60 py-1.5 pr-2 font-medium">功能</th>
            <th className="border-b border-zinc-700/60 py-1.5 pr-2 font-medium">优先级</th>
            <th className="border-b border-zinc-700/60 py-1.5 pr-2 font-medium">工作量</th>
            <th className="border-b border-zinc-700/60 py-1.5 font-medium">验收标准</th>
          </tr>
        </thead>
        <tbody>
          {features.map((feature) => (
            <tr key={feature.id} className="align-top">
              <td className="border-b border-zinc-800/40 py-1.5 pr-2">
                <p className="font-medium text-zinc-200">{feature.title}</p>
                {feature.description ? (
                  <p className="mt-0.5 text-[10px] leading-relaxed text-zinc-500">{feature.description}</p>
                ) : null}
              </td>
              <td className="border-b border-zinc-800/40 py-1.5 pr-2 text-zinc-300">
                {priorityLabel(feature.priority)}
              </td>
              <td className="border-b border-zinc-800/40 py-1.5 pr-2 whitespace-nowrap text-zinc-300">
                {effortLabel(feature.effort)}
              </td>
              <td className="border-b border-zinc-800/40 py-1.5 text-zinc-400">
                {feature.acceptance || NOT_GENERATED}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MvpSpecView({ spec }: { spec: LoopMvpSpec | null }) {
  if (!spec) return <EmptyHint text="MVP 规格尚未生成" />;
  return (
    <div className="space-y-3">
      <div>
        <p className="text-sm font-semibold text-zinc-100">{spec.title}</p>
        {spec.summary ? (
          <p className="mt-1 text-[11px] leading-relaxed text-zinc-400">{spec.summary}</p>
        ) : null}
      </div>
      <FeatureTable features={spec.features} />
      <div>
        <p className="mb-1.5 text-[11px] font-medium text-zinc-400">关键假设</p>
        {spec.assumptions.length === 0 ? (
          <EmptyHint text={NOT_GENERATED} />
        ) : (
          <ul className="space-y-1.5">
            {spec.assumptions.map((assumption) => (
              <li key={assumption.id} className="rounded-lg border border-zinc-700/40 bg-dark-900/40 px-2.5 py-2">
                <p className="text-[11px] leading-relaxed text-zinc-300">{assumption.statement}</p>
                {assumption.risk ? (
                  <p className="mt-0.5 text-[10px] text-amber-300/80">风险：{assumption.risk}</p>
                ) : null}
                {assumption.verification ? (
                  <p className="mt-0.5 text-[10px] text-zinc-500">验证：{assumption.verification}</p>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </div>
      {spec.not_in_scope.length > 0 ? (
        <div>
          <p className="mb-1.5 text-[11px] font-medium text-zinc-400">明确不做</p>
          <div className="flex flex-wrap gap-1.5">
            {spec.not_in_scope.map((item) => (
              <Badge key={item} text={item} />
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function ChecksTable({ checks }: { checks: ValidationCheck[] }) {
  if (checks.length === 0) return <EmptyHint text="还没有检验项" />;
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-[11px]">
        <thead>
          <tr className="text-left text-zinc-500">
            <th className="border-b border-zinc-700/60 py-1.5 pr-2 font-medium">问题</th>
            <th className="border-b border-zinc-700/60 py-1.5 pr-2 font-medium">结果</th>
            <th className="border-b border-zinc-700/60 py-1.5 font-medium">证据数</th>
          </tr>
        </thead>
        <tbody>
          {checks.map((check) => (
            <tr key={check.id} className="align-top">
              <td className="border-b border-zinc-800/40 py-1.5 pr-2 text-zinc-300">
                <p>{check.question}</p>
                {check.notes ? <p className="mt-0.5 text-[10px] text-zinc-500">{check.notes}</p> : null}
              </td>
              <td className="border-b border-zinc-800/40 py-1.5 pr-2">
                <Badge text={checkResultLabel(check.result)} tone={checkResultTone(check.result)} />
              </td>
              <td className="border-b border-zinc-800/40 py-1.5 text-zinc-400">{check.evidence_refs.length}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ValidationView({ validation }: { validation: LoopValidation | null }) {
  if (!validation) return <EmptyHint text="假设检验尚未生成" />;
  return (
    <div className="space-y-3">
      <ChecksTable checks={validation.checks} />
      {validation.summary ? (
        <p className="rounded-lg border border-zinc-700/40 bg-dark-900/40 px-3 py-2 text-[11px] leading-relaxed text-zinc-300">
          {validation.summary}
        </p>
      ) : null}
      <div>
        <p className="mb-1.5 text-[11px] font-medium text-zinc-400">仍未知</p>
        <StringList items={validation.unknowns} emptyText="没有记录未知项" />
      </div>
    </div>
  );
}

function PrototypeView({ prototype }: { prototype: PrototypeSpec | null }) {
  if (!prototype) return <EmptyHint text="原型代码尚未生成" />;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-sm font-semibold text-zinc-100">{prototype.title}</p>
        {prototype.requires_human_build ? <Badge text="需人工落地" tone="amber" /> : null}
      </div>
      {prototype.summary ? (
        <p className="text-[11px] leading-relaxed text-zinc-400">{prototype.summary}</p>
      ) : null}
      <div className="grid gap-2 sm:grid-cols-2">
        <div className="rounded-lg border border-zinc-700/40 bg-dark-900/40 px-2.5 py-2">
          <p className="text-[10px] text-zinc-500">入口文件</p>
          <p className="mt-0.5 break-all font-mono text-[11px] text-zinc-300">
            {prototype.entry_file || NOT_GENERATED}
          </p>
        </div>
        <div className="rounded-lg border border-zinc-700/40 bg-dark-900/40 px-2.5 py-2">
          <p className="text-[10px] text-zinc-500">运行命令</p>
          <p className="mt-0.5 break-all font-mono text-[11px] text-zinc-300">
            {prototype.run_command || NOT_GENERATED}
          </p>
        </div>
      </div>
      {prototype.requires_human_build ? (
        <p className="rounded-xl border border-amber-700/40 bg-amber-900/20 px-3 py-2 text-[11px] leading-relaxed text-amber-300">
          原型代码由模型生成，未在真实环境构建运行；落地前需人工搭环境、补依赖并自测。
        </p>
      ) : null}
      {prototype.files.length === 0 ? (
        <EmptyHint text="没有原型文件" />
      ) : (
        <div className="space-y-2">
          {prototype.files.map((file) => (
            <div key={file.path} className="overflow-hidden rounded-lg border border-zinc-700/40 bg-dark-900/60">
              <div className="flex items-center gap-2 border-b border-zinc-700/40 px-2.5 py-1.5">
                <span className="break-all font-mono text-[11px] text-zinc-300">{file.path}</span>
                <span className="ml-auto text-[10px] text-zinc-500">{file.language}</span>
              </div>
              {file.purpose ? (
                <p className="px-2.5 pt-1.5 text-[10px] text-zinc-500">{file.purpose}</p>
              ) : null}
              <pre className="max-h-72 overflow-auto px-2.5 py-2 text-[11px] leading-relaxed text-zinc-300">
                <code>{file.content}</code>
              </pre>
            </div>
          ))}
        </div>
      )}
      {prototype.limitations ? (
        <div>
          <p className="mb-1.5 text-[11px] font-medium text-zinc-400">已知限制</p>
          <p className="text-[11px] leading-relaxed text-zinc-400">{prototype.limitations}</p>
        </div>
      ) : null}
    </div>
  );
}

function TestReportView({ report }: { report: LoopTestReport | null }) {
  if (!report) return <EmptyHint text="测试报告尚未生成" />;
  const stats = testReportStats(report);
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-4 gap-2">
        {[
          { label: "总数", value: stats.total, tone: "zinc" as Tone },
          { label: "通过", value: stats.passed, tone: "emerald" as Tone },
          { label: "失败", value: stats.failed, tone: "red" as Tone },
          { label: "跳过", value: stats.skipped, tone: "zinc" as Tone },
        ].map((item) => (
          <div key={item.label} className="rounded-lg border border-zinc-700/40 bg-dark-900/40 px-2.5 py-2">
            <p className="text-[10px] text-zinc-500">{item.label}</p>
            <p className={`mt-0.5 text-sm font-semibold ${TONE_TEXT[item.tone]}`}>{item.value}</p>
          </div>
        ))}
      </div>
      {report.simulated ? (
        <p className="rounded-xl border border-amber-700/40 bg-amber-900/20 px-3 py-2 text-[11px] leading-relaxed text-amber-300">
          本报告为模拟执行结果（simulated），不等于真实构建/运行通过，交付前需人工复测。
        </p>
      ) : null}
      {report.cases.length === 0 ? (
        <EmptyHint text="没有测试用例" />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-[11px]">
            <thead>
              <tr className="text-left text-zinc-500">
                <th className="border-b border-zinc-700/60 py-1.5 pr-2 font-medium">用例</th>
                <th className="border-b border-zinc-700/60 py-1.5 pr-2 font-medium">结果</th>
                <th className="border-b border-zinc-700/60 py-1.5 pr-2 font-medium">预期</th>
                <th className="border-b border-zinc-700/60 py-1.5 font-medium">证据</th>
              </tr>
            </thead>
            <tbody>
              {report.cases.map((testCase: LoopTestCase) => (
                <tr key={testCase.id} className="align-top">
                  <td className="border-b border-zinc-800/40 py-1.5 pr-2 text-zinc-300">
                    <p>{testCase.title}</p>
                    {testCase.steps.length > 0 ? (
                      <ol className="mt-0.5 list-decimal space-y-0.5 pl-3 text-[10px] text-zinc-500">
                        {testCase.steps.map((step, stepIndex) => (
                          <li key={`${testCase.id}-${stepIndex}`}>{step}</li>
                        ))}
                      </ol>
                    ) : null}
                  </td>
                  <td className="border-b border-zinc-800/40 py-1.5 pr-2">
                    <Badge text={checkResultLabel(testCase.status === "passed" ? "pass" : testCase.status === "failed" ? "fail" : "unknown")} tone={checkResultTone(testCase.status === "passed" ? "pass" : testCase.status === "failed" ? "fail" : "unknown")} />
                  </td>
                  <td className="border-b border-zinc-800/40 py-1.5 pr-2 text-zinc-400">{testCase.expected || NOT_GENERATED}</td>
                  <td className="border-b border-zinc-800/40 py-1.5 text-zinc-500">{testCase.evidence || NOT_GENERATED}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {report.notes ? (
        <p className="text-[11px] leading-relaxed text-zinc-400">{report.notes}</p>
      ) : null}
    </div>
  );
}

function ScorecardView({ card }: { card: LoopScoreCard | null }) {
  if (!card) return <EmptyHint text="评分卡尚未生成" />;
  const rows = scoreDimensionRows(card);
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-lg font-semibold text-zinc-100">{formatScore(card)}</span>
        <Badge text={verdictLabel(card.verdict)} tone={verdictTone(card.verdict)} />
        <span className="text-[10px] text-zinc-500">
          生成方：{card.generated_by === "harness" ? "规则校验" : "模型"}
        </span>
        {card.hard_capped ? <Badge text="硬性封顶" tone="red" /> : null}
      </div>
      {scoreCapNotice(card) ? (
        <p className="text-[11px] leading-relaxed text-amber-300/90">{scoreCapNotice(card)}</p>
      ) : null}
      {rows.length === 0 ? (
        <EmptyHint text="没有维度评分" />
      ) : (
        <div className="space-y-2.5">
          {rows.map((row) => (
            <div key={row.key}>
              <div className="mb-1 flex items-center gap-2">
                <span className="text-[11px] text-zinc-300">{row.label}</span>
                <span className="ml-auto text-[10px] text-zinc-500">
                  {row.score}/{row.max}
                </span>
              </div>
              <ProgressBar
                percent={row.percent}
                tone={row.percent >= 80 ? "emerald" : row.percent >= 50 ? "amber" : "red"}
              />
              {row.reason ? (
                <p className="mt-1 text-[10px] leading-relaxed text-zinc-500">{row.reason}</p>
              ) : null}
            </div>
          ))}
        </div>
      )}
      <div>
        <p className="mb-1.5 text-[11px] font-medium text-red-400">阻塞项</p>
        <StringList items={blockerList(card.blockers)} emptyText="暂无阻塞项" />
      </div>
      <div>
        <p className="mb-1.5 text-[11px] font-medium text-emerald-300">亮点</p>
        <StringList items={card.highlights} emptyText="暂无亮点记录" />
      </div>
      <div>
        <p className="mb-1.5 text-[11px] font-medium text-brand-300">下一步建议</p>
        <StringList items={card.next_actions} emptyText="暂无下一步建议" />
      </div>
    </div>
  );
}

const ARTIFACT_KIND_ORDER: LoopArtifactKind[] = [
  "mvp_spec",
  "validation",
  "prototype",
  "test_report",
  "scorecard",
];

/** 历史轮产物内容按「该轮 artifact id」读取，绝不用快照里的 latest-of-kind 冒充其它轮。 */
function HistoricalArtifactView({
  kind,
  payload,
}: {
  kind: LoopArtifactKind;
  payload: Record<string, unknown>;
}) {
  if (kind === "mvp_spec") return <MvpSpecView spec={normalizeMvpSpec(payload)} />;
  if (kind === "validation") return <ValidationView validation={normalizeValidation(payload)} />;
  if (kind === "prototype") return <PrototypeView prototype={normalizePrototype(payload)} />;
  if (kind === "test_report") return <TestReportView report={normalizeTestReport(payload)} />;
  return <ScorecardView card={normalizeScoreCard(payload)} />;
}

function ArtifactPanel({
  snapshot,
  round,
  loopId,
  isCurrent,
}: {
  snapshot: LoopSnapshot;
  round: LoopRoundView | null;
  loopId: string;
  isCurrent: boolean;
}) {
  // 只有「当前闭环的当前轮」才能用快照（快照产物是 latest-of-kind）；
  // 回看同一闭环的旧轮次也必须按 round.artifacts 取，否则会把最新一轮的内容当成历史轮（LOOP-01）。
  const isLiveRound = isCurrent && round !== null && round.ordinal === snapshot.current_round;
  const tabs = artifactTabs(isLiveRound ? snapshot : null, round);
  const [tab, setTab] = useState<LoopArtifactKind>(() =>
    defaultArtifactTab(isLiveRound ? snapshot : null, round)
  );
  const [payload, setPayload] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    setTab(defaultArtifactTab(isLiveRound ? snapshot : null, round));
    // 闭环 / 轮次 / 只读态切换时重置 Tab；同一上下文内的快照刷新保留用户选择
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [snapshot.loop_id, round?.ordinal, isLiveRound]);

  useEffect(() => {
    if (!tabs.some((item) => item.kind === tab && item.available)) {
      setTab(defaultArtifactTab(isLiveRound ? snapshot : null, round));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [snapshot.loop_id, round?.ordinal, isLiveRound, tab]);

  const selectedArtifacts = round ? round.artifacts : [];
  const artifact = selectedArtifacts.find((item) => item.kind === tab) ?? null;
  const skeleton = round?.started_at === "";

  useEffect(() => {
    setPayload(null);
    setError(null);
    if (isLiveRound || !artifact || !loopId) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    void (async () => {
      try {
        // 先用该轮最新索引兜底（快照可能早于产物重新生成），再按 artifact id 取内容
        const freshRound = await getLoopRound(loopId, round?.ordinal ?? snapshot.current_round);
        const freshArtifact = freshRound.artifacts.find((item) => item.kind === tab) ?? artifact;
        const response = await getLoopArtifact(loopId, freshArtifact.id);
        if (!cancelled) setPayload(response.payload ?? null);
      } catch (err) {
        if (!cancelled) {
          setError(
            loopErrorText(
              isApiError(err) ? err.code : null,
              isApiError(err) ? err.message : "读取该轮产物失败"
            )
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isLiveRound, artifact?.id, loopId, tab, reloadKey, round?.ordinal]);

  return (
    <Section
      title={`第 ${round?.ordinal ?? snapshot.current_round} 轮产物`}
      extra={
        <>
          {isLiveRound ? null : <Badge text="历史轮次 · 只读" tone="amber" />}
          <span className="text-[10px] text-zinc-500">{artifactTitle(tab)}</span>
        </>
      }
    >
      <div className="mb-3 flex flex-wrap gap-1.5">
        {tabs.map((item) => {
          const active = item.kind === tab;
          return (
            <button
              key={item.kind}
              type="button"
              onClick={() => setTab(item.kind)}
              className={`min-h-[32px] rounded-lg border px-2.5 text-[11px] font-medium transition-colors ${
                active
                  ? "border-brand-500/50 bg-brand-600/15 text-brand-300"
                  : "border-zinc-700/60 text-zinc-400 hover:text-zinc-200"
              } ${item.available ? "" : "opacity-60"}`}
            >
              {item.title}
              {item.available ? "" : "（未生成）"}
            </button>
          );
        })}
      </div>
      {skeleton ? (
        <EmptyHint text="暂无记录：该轮尚未开始，还没有产物。" />
      ) : isLiveRound ? (
        <>
          {tab === "mvp_spec" ? <MvpSpecView spec={snapshot.mvp_spec} /> : null}
          {tab === "validation" ? <ValidationView validation={snapshot.validation} /> : null}
          {tab === "prototype" ? <PrototypeView prototype={snapshot.prototype} /> : null}
          {tab === "test_report" ? <TestReportView report={snapshot.test_report} /> : null}
          {tab === "scorecard" ? <ScorecardView card={snapshot.scorecard} /> : null}
        </>
      ) : !artifact ? (
        <EmptyHint text={`第 ${round?.ordinal ?? snapshot.current_round} 轮没有${artifactTitle(tab)}产物。`} />
      ) : loading ? (
        <p className="text-[11px] text-zinc-500">正在读取第 {round?.ordinal} 轮产物…</p>
      ) : error ? (
        <div className="space-y-2">
          <p className="break-words rounded-lg border border-red-700/40 bg-red-900/20 px-2.5 py-2 text-[11px] leading-relaxed text-red-300">
            {error}
          </p>
          <button type="button" className={BTN_SECONDARY} onClick={() => setReloadKey((key) => key + 1)}>
            重试读取
          </button>
        </div>
      ) : payload ? (
        <div className="space-y-2">
          <p className="text-[10px] text-zinc-600">
            {artifactTitle(artifact.kind)} · {formatBytes(artifact.size_bytes)} ·{" "}
            {formatTimestamp(artifact.created_at)}
          </p>
          <HistoricalArtifactView kind={tab} payload={payload} />
        </div>
      ) : (
        <EmptyHint text="该轮产物内容为空。" />
      )}
      {!isLiveRound && round ? (
        <div className="mt-3 border-t border-zinc-800/60 pt-2">
          <p className="mb-1 text-[10px] text-zinc-500">该轮产物索引</p>
          <div className="flex flex-wrap gap-1.5">
            {selectedArtifacts.length === 0 ? (
              <span className="text-[10px] text-zinc-600">暂无产物记录</span>
            ) : (
              selectedArtifacts
                .slice()
                .sort(
                  (a, b) => ARTIFACT_KIND_ORDER.indexOf(a.kind) - ARTIFACT_KIND_ORDER.indexOf(b.kind)
                )
                .map((item) => (
                  <span
                    key={item.id}
                    className="rounded border border-zinc-700/50 px-1.5 py-0.5 text-[10px] text-zinc-400"
                  >
                    {artifactTitle(item.kind)} · {formatBytes(item.size_bytes)}
                  </span>
                ))
            )}
          </div>
        </div>
      ) : null}
    </Section>
  );
}

// --------------------------------------------------------------------------- //
// 启动表单
// --------------------------------------------------------------------------- //

export interface StartLoopFormValues {
  scope: "mvp" | "optimize";
  maxRounds: number;
  targetScore: number;
  focus: string;
}

function StartLoopBar({
  busy,
  error,
  onSubmit,
}: {
  busy: boolean;
  error: string | null;
  onSubmit: (values: StartLoopFormValues) => void;
}) {
  const [scope, setScope] = useState<"mvp" | "optimize">("mvp");
  const [maxRounds, setMaxRounds] = useState("3");
  const [targetScore, setTargetScore] = useState("85");
  const [focus, setFocus] = useState("");
  const form = validateStartForm({ scope, maxRounds, targetScore, focus });

  return (
    <Section
      title="启动闭环工作室"
      extra={<Badge text="五阶段 × 多轮" tone="brand" />}
    >
      <p className="mb-3 text-[11px] leading-relaxed text-zinc-500">
        闭环会按「创意发散 → 假设验证 → 方案开发 → 测试验证 → 评分复盘」推进，每轮产出可用规格、
        检验结论、原型代码与测试报告；达到目标分后可交付，或继续迭代。
      </p>
      {error ? (
        <p className="mb-3 rounded-xl border border-red-800/40 bg-red-900/20 px-3 py-2 text-[11px] text-red-400">
          {error}
        </p>
      ) : null}
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block">
          <span className="mb-1 block text-[11px] text-zinc-500">闭环范围</span>
          <select
            className={INPUT_CLASS}
            value={scope}
            onChange={(event) => setScope(event.target.value === "optimize" ? "optimize" : "mvp")}
          >
            <option value="mvp">{SCOPE_LABELS.mvp}</option>
            <option value="optimize">{SCOPE_LABELS.optimize}</option>
          </select>
        </label>
        <label className="block">
          <span className="mb-1 block text-[11px] text-zinc-500">最大轮次（1-5）</span>
          <input
            className={INPUT_CLASS}
            type="number"
            min={1}
            max={5}
            step={1}
            value={maxRounds}
            onChange={(event) => setMaxRounds(event.target.value)}
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-[11px] text-zinc-500">目标分（60-100）</span>
          <input
            className={INPUT_CLASS}
            type="number"
            min={60}
            max={100}
            step={1}
            value={targetScore}
            onChange={(event) => setTargetScore(event.target.value)}
          />
        </label>
        <div className="rounded-lg border border-zinc-700/40 bg-dark-900/40 px-2.5 py-2">
          <p className="text-[10px] text-zinc-500">生效参数</p>
          <p className="mt-0.5 text-[11px] text-zinc-300">
            {SCOPE_LABELS[form.scope]} · 最多 {form.max_rounds} 轮 · 目标 {form.target_score} 分
          </p>
        </div>
      </div>
      <label className="mt-3 block">
        <span className="mb-1 block text-[11px] text-zinc-500">聚焦说明（可选）</span>
        <textarea
          className={`${INPUT_CLASS} min-h-[72px] resize-y`}
          maxLength={2000}
          value={focus}
          placeholder="这一轮想重点解决什么？例如：只做最小可验证的单人协作流程"
          onChange={(event) => setFocus(event.target.value)}
        />
        <span className="mt-1 block text-right text-[10px] text-zinc-600">{focus.length}/2000</span>
      </label>
      {form.error ? <p className="mt-1 text-[11px] text-red-400">{form.error}</p> : null}
      <button
        type="button"
        className={`mt-3 ${BTN_PRIMARY}`}
        disabled={busy || !form.ok}
        onClick={() =>
          onSubmit({
            scope: form.scope,
            maxRounds: form.max_rounds,
            targetScore: form.target_score,
            focus: form.focus,
          })
        }
      >
        {busy ? (
          <span className="inline-flex items-center gap-2">
            <Spinner /> 正在启动
          </span>
        ) : (
          "启动闭环"
        )}
      </button>
    </Section>
  );
}

// --------------------------------------------------------------------------- //
// 决策条
// --------------------------------------------------------------------------- //

function DecisionBar({
  snapshot,
  phase,
  sessionId,
  busy,
  abortBusy,
  note,
  onNoteChange,
  onDecide,
  onAbort,
}: {
  snapshot: LoopSnapshot;
  /** 会话 v2 阶段：审计进行中时三键必须禁用（backend ACTION_PHASES 只允许 brainstorming/audit_completed） */
  phase: SessionPhase | null;
  sessionId: string;
  busy: LoopDecisionKind | null;
  abortBusy: boolean;
  note: string;
  onNoteChange: (value: string) => void;
  onDecide: (decision: LoopDecisionKind) => void;
  onAbort: () => void;
}) {
  const availability = decisionAvailability(snapshot, phase);
  const iterateReason = availability.iterate.reason;
  // 阶段门控时三键全禁：给一条明确原因 + 「去结束审计」入口
  const phaseBlocked = availability.awaiting && !availability.stop.enabled;
  return (
    <Section
      title="本轮决策"
      sticky={availability.awaiting}
      extra={<Badge text={loopStatusText(snapshot)} tone={loopStatusTone(snapshot.status)} />}
    >
      {phaseBlocked ? (
        <div className="mb-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-2.5 py-2 text-[11px] leading-relaxed text-amber-300">
          <span>{availability.stop.reason}</span>
          {phase === "auditing" ? (
            <Link
              href={`/session/${sessionId}/audit`}
              className="ml-2 font-medium text-amber-200 underline underline-offset-2 hover:text-amber-100"
            >
              去结束审计
            </Link>
          ) : null}
        </div>
      ) : null}
      {availability.awaiting ? (
        <>
          <p className="mb-2 text-[11px] leading-relaxed text-zinc-400">
            本轮已停在「等待决策」。继续迭代会新建下一轮；交付/停止会立即生成收尾 Run 并关闭闭环。
          </p>
          <label className="mb-3 block">
            <span className="mb-1 block text-[11px] text-zinc-500">决策备注（可选）</span>
            <textarea
              className={`${INPUT_CLASS} min-h-[60px] resize-y`}
              maxLength={2000}
              value={note}
              placeholder="例如：补齐登录态用例后再交付"
              onChange={(event) => onNoteChange(event.target.value)}
            />
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              className={BTN_AMBER}
              disabled={busy !== null || !availability.iterate.enabled}
              title={availability.iterate.enabled ? undefined : iterateReason}
              onClick={() => onDecide("iterate")}
            >
              {busy === "iterate" ? "提交中…" : "继续迭代"}
            </button>
            <button
              type="button"
              className={BTN_PRIMARY}
              disabled={busy !== null || !availability.ship.enabled}
              onClick={() => onDecide("ship")}
            >
              {busy === "ship" ? "提交中…" : "交付"}
            </button>
            <button
              type="button"
              className={BTN_DANGER}
              disabled={busy !== null || !availability.stop.enabled}
              onClick={() => onDecide("stop")}
            >
              {busy === "stop" ? "提交中…" : "停止"}
            </button>
          </div>
          {!availability.iterate.enabled && iterateReason ? (
            <p className="mt-2 text-[10px] text-amber-300">{iterateReason}</p>
          ) : null}
        </>
      ) : (
        <>
          <p className="text-[11px] leading-relaxed text-zinc-400">
            {snapshot.status === "running"
              ? `第 ${snapshot.current_round} 轮正在执行；如果长时间没有进展，可以中止本轮后重新开启闭环。`
              : snapshot.status === "shipped"
                ? "闭环已交付：规格、检验、原型、测试与评分卡均已归档。"
                : snapshot.status === "stopped"
                  ? "闭环已停止：本轮产物保留，可重新开启新闭环。"
                  : snapshot.status === "failed"
                    ? "闭环执行失败：请查看阶段时间线里的失败阶段，或重新开启闭环。"
                    : "闭环当前没有待决策动作。"}
          </p>
          {snapshot.status === "running" ? (
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <button
                type="button"
                className={BTN_DANGER}
                disabled={busy !== null || abortBusy || !snapshot.latest_run_id}
                title={snapshot.latest_run_id ? undefined : "当前没有可中止的运行"}
                onClick={onAbort}
              >
                {abortBusy ? "中止中…" : "中止本轮"}
              </button>
              <span className="text-[10px] text-zinc-500">
                只中止当前运行，已生成的部分产物会保留。
              </span>
            </div>
          ) : null}
        </>
      )}
    </Section>
  );
}

// --------------------------------------------------------------------------- //
// 页面主体
// --------------------------------------------------------------------------- //

function readErrorLoopId(error: unknown): string | null {
  if (!isApiError(error)) return null;
  const details: unknown = error.details;
  if (!details || typeof details !== "object" || Array.isArray(details)) return null;
  const value = (details as Record<string, unknown>).loop_id;
  return typeof value === "string" && value ? value : null;
}

function LoopPageContent() {
  const params = useParams<{ id: string }>();
  const sessionId = params?.id ?? null;

  const sessionView = useSessionStore((state) => state.sessionView);
  const v2Phase = useSessionStore((state) => state.v2Phase);
  const sessionLoading = useSessionStore((state) => state.sessionLoading);
  const legacyMode = useSessionStore((state) => state.legacyMode);
  const loadSession = useSessionStore((state) => state.loadSession);
  const reloadSession = useSessionStore((state) => state.reloadSession);
  const hydrateLoopById = useSessionStore((state) => state.hydrateLoopById);
  const startLoop = useSessionStore((state) => state.startLoop);
  const decideLoopAction = useSessionStore((state) => state.decideLoop);
  const cancelRun = useSessionStore((state) => state.cancelRun);
  const retryFailedRun = useSessionStore((state) => state.retryFailedRun);
  const loops = useSessionStore((state) => state.loops);
  const runs = useSessionStore((state) => state.runs);
  const loopId = useSessionStore((state) => state.loopId);
  const loopLoading = useSessionStore((state) => state.loopLoading);
  const loopErrors = useSessionStore((state) => state.loopErrors);
  const stateWarnings = useSessionStore((state) => state.stateWarnings);
  const clearStateWarnings = useSessionStore((state) => state.clearStateWarnings);

  const [startBusy, setStartBusy] = useState(false);
  const [decideBusy, setDecideBusy] = useState<LoopDecisionKind | null>(null);
  const [abortBusy, setAbortBusy] = useState(false);
  const [retryBusy, setRetryBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  /** 首次会话读取是否已结束：区分「还没开始读」与「读完仍无快照」（DX-02） */
  const [sessionSettled, setSessionSettled] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [decisionNote, setDecisionNote] = useState("");
  const [newLoopOpen, setNewLoopOpen] = useState(false);

  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    setSessionSettled(false);
    void loadSession(sessionId).finally(() => {
      if (!cancelled) setSessionSettled(true);
    });
    return () => {
      cancelled = true;
    };
  }, [sessionId, loadSession]);

  const currentLoopId = loopId ?? sessionView?.current_loop_id ?? null;
  const currentLoop = currentLoopId ? loops[currentLoopId] ?? null : null;
  // ---- 历史轮次回看 / 多轮对比 / 历史闭环切换（R4 ③） ----
  const [selectedOrdinal, setSelectedOrdinal] = useState<number | null>(null);
  const [historyLoops, setHistoryLoops] = useState<LoopSnapshot[] | null>(null);
  const [historyBusy, setHistoryBusy] = useState(false);
  const [comparisonCards, setComparisonCards] = useState<{
    previous: LoopScoreCard | null;
    current: LoopScoreCard | null;
  }>({ previous: null, current: null });

  const selectedRound = useMemo(
    () => (currentLoop ? selectRound(currentLoop.rounds, selectedOrdinal, currentLoop.current_round) : null),
    [currentLoop, selectedOrdinal]
  );
  const roundOptions = useMemo(
    () => roundSwitcherOptions(currentLoop?.rounds ?? [], selectedRound?.ordinal ?? null),
    [currentLoop, selectedRound?.ordinal]
  );
  const previousRound = useMemo(() => {
    if (!currentLoop || !selectedRound) return null;
    return (
      currentLoop.rounds
        .filter((item) => item.ordinal < selectedRound.ordinal)
        .sort((a, b) => a.ordinal - b.ordinal)
        .pop() ?? null
    );
  }, [currentLoop, selectedRound]);
  const isCurrentLoop = currentLoop
    ? currentLoop.loop_id === (sessionView?.current_loop_id ?? currentLoop.loop_id)
    : true;
  const comparison = useMemo(
    () => compareRounds(previousRound, selectedRound, comparisonCards.previous, comparisonCards.current),
    [previousRound, selectedRound, comparisonCards]
  );
  const loopChoices = useMemo(
    // 只保留当前会话的闭环：缓存跨会话保留，不过滤会把别人的闭环显示成本会话历史（FE-DX-02）
    () => selectLoopChoices(historyLoops, loops, sessionId),
    [historyLoops, loops, sessionId],
  );

  useEffect(() => {
    // 切换闭环时回到「当前轮」，避免把上一闭环的 ordinal 带过来
    setSelectedOrdinal(null);
  }, [currentLoopId]);

  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    void listLoops(sessionId, { limit: 20 })
      .then((page) => {
        if (!cancelled) setHistoryLoops(page.items);
      })
      .catch(() => {
        // 历史闭环列表失败不影响主流程：仍可从已缓存的 loops 切换
        if (!cancelled) setHistoryLoops(null);
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  useEffect(() => {
    if (!currentLoop || !selectedRound) {
      setComparisonCards({ previous: null, current: null });
      return;
    }
    let cancelled = false;
    const loadCard = async (round: LoopRoundView | null): Promise<LoopScoreCard | null> => {
      if (!round) return null;
      if (round.ordinal === currentLoop.current_round && currentLoop.scorecard) {
        return currentLoop.scorecard;
      }
      const artifact = round.artifacts.find((item) => item.kind === "scorecard");
      if (!artifact) return null;
      try {
        const response = await getLoopArtifact(currentLoop.loop_id, artifact.id);
        return normalizeScoreCard(response.payload);
      } catch {
        return null;
      }
    };
    void Promise.all([loadCard(previousRound), loadCard(selectedRound)]).then(([previous, current]) => {
      if (!cancelled) setComparisonCards({ previous, current });
    });
    return () => {
      cancelled = true;
    };
  }, [currentLoop, selectedRound, previousRound]);
  const currentLoopError = useMemo(() => {
    if (currentLoopId && loopErrors[currentLoopId]) return loopErrors[currentLoopId];
    const first = Object.keys(loopErrors).find((key) => loopErrors[key]);
    return first ? loopErrors[first] : null;
  }, [currentLoopId, loopErrors]);

  useEffect(() => {
    if (sessionView?.current_loop_id && !loops[sessionView.current_loop_id]) {
      void hydrateLoopById(sessionView.current_loop_id);
    }
  }, [sessionView?.current_loop_id, loops, hydrateLoopById]);

  const entry = resolveLoopEntryState({
    sessionLoading,
    // 会话快照是否已加载：只有拿到 sessionView 才算已加载（DX-R3-03）
    sessionLoaded: Boolean(sessionView),
    // 读完仍没有 sessionView 且不在加载中 = 读取失败，必须给「重试」而不是阶段门文案
    sessionLoadFailed: sessionSettled && !sessionView && !sessionLoading,
    sessionPhase: sessionView?.phase ?? null,
    legacyMode,
    loop: currentLoop,
    loopLoading,
    loopError: currentLoopError,
  });

  const refresh = useCallback(async () => {
    setRefreshing(true);
    setActionError(null);
    try {
      // 没有 loop 时（会话读取失败 / 尚未启动）也要能重试：退化为重读会话（DX-02）
      if (currentLoopId) await hydrateLoopById(currentLoopId);
      await reloadSession();
    } finally {
      setRefreshing(false);
    }
  }, [currentLoopId, hydrateLoopById, reloadSession]);

  const handleStart = useCallback(
    async (values: StartLoopFormValues) => {
      setStartBusy(true);
      setActionError(null);
      setNotice(null);
      try {
        await startLoop({
          scope: values.scope,
          maxRounds: values.maxRounds,
          targetScore: values.targetScore,
          focus: values.focus,
        });
        setNotice("闭环已启动，第 1 轮已排队");
        setNewLoopOpen(false);
      } catch (error) {
        if (isApiError(error) && error.code === "LOOP_ACTIVE") {
          const activeLoopId = readErrorLoopId(error) ?? sessionView?.current_loop_id ?? null;
          if (activeLoopId) {
            await hydrateLoopById(activeLoopId);
            setNotice("已有进行中的闭环，已切换到该闭环");
          } else {
            setActionError(loopErrorText(error.code, error.message));
          }
        } else if (isApiError(error) && (error.code === "VERSION_CONFLICT" || error.code === "LOOP_VERSION_CONFLICT")) {
          if (currentLoopId) await hydrateLoopById(currentLoopId);
          const text = loopErrorText(error.code, error.message);
          setActionError(text);
          toast.error(text);
        } else {
          setActionError(isApiError(error) ? loopErrorText(error.code, error.message) : "启动闭环失败，请稍后重试");
        }
      } finally {
        setStartBusy(false);
      }
    },
    [startLoop, sessionView?.current_loop_id, hydrateLoopById, currentLoopId]
  );

  /** 历史闭环切换：hydrate 后 store.loopId 指向所选闭环，主视图整体切换（只读回看）。 */
  const handleSelectLoop = useCallback(
    async (targetLoopId: string) => {
      if (!targetLoopId || targetLoopId === currentLoopId) return;
      setHistoryBusy(true);
      setActionError(null);
      setNotice(null);
      try {
        const ok = await hydrateLoopById(targetLoopId);
        if (ok) {
          setSelectedOrdinal(null);
          setNotice(
            targetLoopId === sessionView?.current_loop_id
              ? "已回到当前闭环"
              : "已切换到历史闭环（只读回看，产物与评分均为该闭环记录）"
          );
        } else {
          setActionError("读取该闭环失败，请重试。");
        }
      } finally {
        setHistoryBusy(false);
      }
    },
    [currentLoopId, hydrateLoopById, sessionView?.current_loop_id]
  );

  const handleDecide = useCallback(
    async (decision: LoopDecisionKind) => {
      if (!currentLoop) return;
      setDecideBusy(decision);
      setActionError(null);
      setNotice(null);
      try {
        await decideLoopAction({
          loopId: currentLoop.loop_id,
          expectedLoopVersion: currentLoop.loop_version,
          decision,
          note: decisionNote,
        });
        setNotice(
          decision === "iterate"
            ? "已提交继续迭代，下一轮正在排队"
            : decision === "ship"
              ? "已提交交付，闭环即将完成"
              : "已提交停止"
        );
        setDecisionNote("");
      } catch (error) {
        if (
          isApiError(error) &&
          (error.code === "VERSION_CONFLICT" ||
            error.code === "LOOP_VERSION_CONFLICT" ||
            error.code === "LOOP_NOT_AWAITING_DECISION" ||
            error.code === "LOOP_ROUNDS_EXHAUSTED")
        ) {
          await hydrateLoopById(currentLoop.loop_id);
          const text = loopErrorText(error.code, error.message);
          setActionError(text);
          if (error.code === "VERSION_CONFLICT" || error.code === "LOOP_VERSION_CONFLICT") {
            toast.error(text);
          }
        } else {
          setActionError(isApiError(error) ? loopErrorText(error.code, error.message) : "提交决策失败，请稍后重试");
        }
      } finally {
        setDecideBusy(null);
      }
    },
    [currentLoop, decideLoopAction, decisionNote, hydrateLoopById]
  );

  const finished = currentLoop
    ? currentLoop.status === "shipped" || currentLoop.status === "stopped" || currentLoop.status === "failed"
    : false;

  // 失败 Run 的真实错误文案（RunState.errorMessage），比只给错误码更能指导恢复（PRD-07）
  const failedRunError = currentLoop?.latest_run_id
    ? runs[currentLoop.latest_run_id]?.errorMessage ?? null
    : null;

  const handleAbort = useCallback(async () => {
    if (!currentLoop) return;
    const runId = currentLoop.latest_run_id;
    if (!runId) {
      setActionError("当前没有可中止的运行");
      return;
    }
    if (typeof window !== "undefined" && !window.confirm("确定中止本轮？已生成的部分产物会保留。")) return;
    setAbortBusy(true);
    setActionError(null);
    setNotice(null);
    try {
      // cancelRun 返回 false 表示请求失败（store 已写 error）：不能提示成功（FE-DX-01）
      const ok = await cancelRun(runId);
      if (!ok) {
        setActionError("中止请求未成功，请查看错误提示后重试");
        return;
      }
      setNotice("已请求中止本轮，正在刷新状态…");
      await hydrateLoopById(currentLoop.loop_id);
    } finally {
      setAbortBusy(false);
    }
  }, [currentLoop, cancelRun, hydrateLoopById]);

  // 轮次失败后的「重试本轮」：后端 retry_run 支持 loop 子 Run（PRD-07）
  const handleRetryRound = useCallback(async () => {
    if (!currentLoop?.latest_run_id) {
      setActionError("当前没有可重试的运行");
      return;
    }
    setRetryBusy(true);
    setActionError(null);
    setNotice(null);
    try {
      const ok = await retryFailedRun(currentLoop.latest_run_id);
      if (!ok) {
        setActionError("重试本轮失败，请查看错误提示后重试");
        return;
      }
      setNotice("已重新提交本轮，正在刷新状态…");
      await hydrateLoopById(currentLoop.loop_id);
    } finally {
      setRetryBusy(false);
    }
  }, [currentLoop, retryFailedRun, hydrateLoopById]);

  return (
    <div className="min-h-screen bg-dark-900 bg-mesh">
      <header className="sticky top-0 z-10 flex min-h-[52px] items-center gap-3 border-b border-zinc-800/50 bg-dark-900/80 px-4 backdrop-blur">
        <Link
          href={sessionId ? `/session/${sessionId}` : "/"}
          className="text-xs text-zinc-500 underline-offset-2 hover:text-zinc-300 hover:underline"
        >
          ← 返回工作台
        </Link>
        <LayersIcon size={16} className="text-brand-400" />
        <h1 className="text-sm font-semibold text-zinc-100">循环工作室</h1>
        {currentLoop ? (
          <>
            <Badge text={loopStatusText(currentLoop)} tone={loopStatusTone(currentLoop.status)} />
            <span className="hidden text-[11px] text-zinc-500 sm:inline">
              {formatRound(currentLoop.current_round, currentLoop.max_rounds)} · 目标 {currentLoop.target_score} 分
            </span>
            {currentLoop.scorecard ? (
              <span className="hidden items-center gap-1.5 sm:flex">
                <span className="text-[11px] text-zinc-500">{formatScore(currentLoop.scorecard)}</span>
                <Badge
                  text={scoreVerdictText(currentLoop)}
                  tone={verdictTone(currentLoop.scorecard.verdict)}
                />
              </span>
            ) : null}
          </>
        ) : null}
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={refreshing}
          className="ml-auto inline-flex min-h-[36px] items-center gap-1.5 rounded-lg border border-zinc-700/60 px-2.5 text-[11px] text-zinc-400 transition-colors hover:text-zinc-200 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {refreshing ? <Spinner /> : null}
          刷新
        </button>
      </header>

      <main className="mx-auto w-full max-w-3xl space-y-3 px-4 py-4">
        {notice ? (
          <div className="rounded-xl border border-emerald-700/40 bg-emerald-900/20 px-3 py-2 text-xs text-emerald-300">
            {notice}
          </div>
        ) : null}
        {actionError ? (
          <div className="rounded-xl border border-red-800/40 bg-red-900/20 px-3 py-2 text-xs text-red-400">
            {actionError}
          </div>
        ) : null}
        {stateWarnings.length > 0 ? (
          <div className="flex items-start gap-2 rounded-xl border border-amber-700/40 bg-amber-900/20 px-3 py-2 text-xs text-amber-300">
            <span className="min-w-0 flex-1">{stateWarnings[stateWarnings.length - 1]}</span>
            <button
              type="button"
              onClick={clearStateWarnings}
              className="shrink-0 text-amber-200/80 hover:text-amber-100"
              aria-label="关闭告警"
            >
              关闭
            </button>
          </div>
        ) : null}

        {entry.showLoading ? (
          <Section title="加载中">
            <div className="flex items-center gap-2 text-[11px] text-zinc-500">
              <Spinner /> 正在读取会话与闭环状态…
            </div>
          </Section>
        ) : null}

        {entry.showGate ? (
          <Section title="暂不可用">
            <p className="text-[11px] leading-relaxed text-zinc-400">{entry.gateText}</p>
            <button
              type="button"
              className={`${BTN_SECONDARY} mt-3`}
              disabled={refreshing}
              onClick={() => void refresh()}
            >
              {refreshing ? "刷新中…" : "刷新状态"}
            </button>
          </Section>
        ) : null}

        {entry.showLoadError ? (
          <Section title="闭环读取失败">
            <p className="mb-3 text-[11px] leading-relaxed text-red-400">{entry.loadErrorText}</p>
            <button type="button" className={BTN_SECONDARY} onClick={() => void refresh()}>
              重试
            </button>
          </Section>
        ) : null}

        {entry.showStartPanel && !currentLoop ? (
          <StartLoopBar busy={startBusy} error={entry.loadErrorText || actionError} onSubmit={(values) => void handleStart(values)} />
        ) : null}

        {entry.showLoop && currentLoop ? (
          <>
            {loopChoices.length > 1 ? (
              <Section
                title="历史闭环"
                extra={<Badge text={`${loopChoices.length} 个闭环`} />}
              >
                <label htmlFor="loop-history" className="mb-2 block text-[11px] leading-relaxed text-zinc-500">
                  同一会话下的闭环都在这里；切换后概要与产物会整体替换，历史闭环只读回看。
                </label>
                <select
                  id="loop-history"
                  value={currentLoop.loop_id}
                  disabled={historyBusy}
                  onChange={(event) => void handleSelectLoop(event.target.value)}
                  className="min-h-[40px] w-full max-w-md rounded-lg border border-zinc-700/60 bg-dark-900/60 px-2 text-[11px] text-zinc-200 disabled:opacity-60"
                >
                  {loopChoices.map((item) => (
                    <option key={item.loop_id} value={item.loop_id}>
                      {`闭环 ${item.loop_id.slice(0, 8)} · 第 ${item.current_round}/${item.max_rounds} 轮 · ${loopStatusText(item)}`}
                      {item.loop_id === sessionView?.current_loop_id ? "（当前）" : ""}
                    </option>
                  ))}
                </select>
                {historyBusy ? <p className="mt-1 text-[10px] text-zinc-500">切换中…</p> : null}
              </Section>
            ) : null}
            <LoopSummary snapshot={currentLoop} round={selectedRound} isCurrent={isCurrentLoop} />
            <RoundSwitcher options={roundOptions} onSelect={setSelectedOrdinal} />
            <StageTimeline round={selectedRound} />
            <ArtifactPanel
              snapshot={currentLoop}
              round={selectedRound}
              loopId={currentLoop.loop_id}
              isCurrent={isCurrentLoop}
            />
            <RoundComparisonBar comparison={comparison} />
            {isCurrentLoop ? (
              <DecisionBar
                snapshot={currentLoop}
                phase={v2Phase ?? sessionView?.phase ?? null}
                sessionId={sessionId ?? ""}
                busy={decideBusy}
                abortBusy={abortBusy}
                note={decisionNote}
                onNoteChange={setDecisionNote}
                onDecide={(decision) => void handleDecide(decision)}
                onAbort={() => void handleAbort()}
              />
            ) : (
              <Section title="本轮决策" extra={<Badge text="历史闭环 · 只读" tone="amber" />}>
                <p className="text-[11px] leading-relaxed text-zinc-500">
                  历史闭环只读回看；需要继续迭代 / 交付，请先在「历史闭环」里切回当前闭环。
                </p>
              </Section>
            )}
            {isCurrentLoop && canRetryLoopRound(currentLoop) ? (
              <Section title="本轮失败" extra={<Badge text="可恢复" />}>
                <p className="mb-3 text-[11px] leading-relaxed text-red-400">
                  本轮运行失败，已生成的部分产物会保留。可以直接重试本轮（后端 retry_run 支持 loop 子 Run）。
                </p>
                {failedRunError ? (
                  <p className="mb-3 break-words rounded-lg border border-red-700/40 bg-red-900/20 px-2.5 py-2 text-[11px] leading-relaxed text-red-300">
                    {`失败原因：${failedRunError}`}
                  </p>
                ) : null}
                <button
                  type="button"
                  className={BTN_SECONDARY}
                  disabled={retryBusy}
                  onClick={() => void handleRetryRound()}
                >
                  {retryBusy ? "重试中…" : "重试本轮"}
                </button>
              </Section>
            ) : null}
            {isCurrentLoop && finished ? (
              <Section title="开启新闭环" extra={<Badge text="可选" />}>
                {newLoopOpen ? (
                  <StartLoopBar
                    busy={startBusy}
                    error={actionError}
                    onSubmit={(values) => void handleStart(values)}
                  />
                ) : (
                  <>
                    <p className="mb-3 text-[11px] leading-relaxed text-zinc-400">
                      当前闭环已结束，产物会保留；需要继续打磨时可以开启新的闭环。
                    </p>
                    <button type="button" className={BTN_SECONDARY} onClick={() => setNewLoopOpen(true)}>
                      开启新闭环
                    </button>
                  </>
                )}
              </Section>
            ) : null}
          </>
        ) : null}

        <p className="pt-1 text-center text-[10px] text-zinc-600">
          闭环工作室 · 五阶段推进 · 每轮产物均标注是否需人工落地
        </p>
      </main>
    </div>
  );
}

export default function LoopPage() {
  return <LoopPageContent />;
}
