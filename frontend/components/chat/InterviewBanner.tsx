"use client";

import { SearchIcon, ArrowRightIcon } from "@/components/icons";
import { useSessionStore } from "@/store/sessionStore";
import { canStartAuditPhase } from "@/lib/phaseRouter";

export function InterviewBanner() {
  const { phase, v2Phase, startInterview, isStreaming } = useSessionStore();
  // 旧 phase 会把 auditing 与 audit_completed 混成 interview，这里以 v2 阶段原值为准
  const auditing = v2Phase ? v2Phase === "auditing" : phase === "interview";
  const completed = v2Phase === "audit_completed";
  const canRestart = Boolean(v2Phase && canStartAuditPhase(v2Phase));

  if (completed) {
    return (
      <div className="mx-4 my-2 px-3 py-2 bg-emerald-900/20 border border-emerald-800/50 rounded-lg">
        <div className="flex flex-wrap items-center gap-2">
          <SearchIcon size={14} className="text-emerald-400" />
          <span className="text-emerald-400 text-sm font-medium">审计已完成</span>
          <span className="text-zinc-400 text-xs">
            — 可回到工作台继续讨论，或重新发起一轮审计。
          </span>
          {canRestart ? (
            <button
              type="button"
              onClick={startInterview}
              disabled={isStreaming}
              className="ml-auto min-h-[36px] rounded-lg border border-emerald-600/50 bg-emerald-600/20 px-2.5 text-xs font-medium text-emerald-300 hover:bg-emerald-600/30 disabled:cursor-not-allowed disabled:opacity-50 inline-flex items-center gap-1"
            >
              重新审计 <ArrowRightIcon size={12} />
            </button>
          ) : null}
        </div>
      </div>
    );
  }

  if (!auditing) return null;

  return (
    <div className="mx-4 my-2 px-3 py-2 bg-red-900/20 border border-red-800/50 rounded-lg">
      <div className="flex items-center gap-2">
        <SearchIcon size={14} className="text-red-400" />
        <span className="text-red-400 text-sm font-medium">AI 面试官模式</span>
        <span className="text-zinc-400 text-xs">
          — AI 面试官正在对你的产品方案进行压力测试。回答每个问题以继续。
        </span>
      </div>
    </div>
  );
}
