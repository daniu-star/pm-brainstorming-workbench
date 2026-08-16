"use client";

import Link from "next/link";
import { BrainIcon, PhoneIcon, ShieldIcon } from "@/components/icons";
import { useSessionStore } from "@/store/sessionStore";
import { NavButtons } from "@/components/NavButtons";
import { VoiceToggle } from "@/components/chat/VoiceToggle";

export function InterviewHeader({
  // B041/B087：结束面试（导出监考数据并返回工作台）
  onEndInterview,
}: {
  onEndInterview?: () => void;
} = {}) {
  const { sessionId, interviewMode, setInterviewMode, toggleHistory } = useSessionStore();

  return (
    <header className="z-20 flex h-16 shrink-0 items-center justify-between gap-2 border-b border-white/10 bg-[#070b11] px-4 safe-top">
      <div className="flex min-w-0 items-center gap-3">
        <Link
          href={sessionId ? `/session/${sessionId}` : "/"}
          className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-cyan-300/20 bg-cyan-300/10 text-cyan-200"
          aria-label="返回产品工作台"
        >
          <BrainIcon size={17} />
        </Link>
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <h1 className="truncate text-sm font-semibold text-white">AI 审计专业通话</h1>
            <span className="hidden rounded border border-cyan-300/20 bg-cyan-300/5 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-cyan-200/70 sm:inline">
              Secure
            </span>
          </div>
          <p className="mt-0.5 flex items-center gap-1.5 text-[10px] text-zinc-500">
            <ShieldIcon size={10} className="text-emerald-300" />
            产品方案审计频道 · 通话内容实时结构化
          </p>
        </div>
      </div>

      {/* B094：flex-wrap + gap 防小屏挤压 */}
      <div className="flex flex-wrap items-center justify-end gap-2">
        <VoiceToggle mode={interviewMode} onChange={setInterviewMode} />
        <button
          type="button"
          onClick={onEndInterview}
          disabled={!onEndInterview}
          aria-label="结束面试并返回工作台"
          title="结束面试并导出监考数据"
          className="flex min-h-[38px] shrink-0 items-center gap-1.5 rounded-lg border border-cyan-300/30 bg-cyan-300/10 px-3 text-xs font-medium text-cyan-200 transition-colors hover:bg-cyan-300/20 disabled:cursor-not-allowed disabled:opacity-40"
        >
          <PhoneIcon size={13} />
          结束面试
        </button>
        <NavButtons currentPage="interview" sessionId={sessionId} onToggleHistory={toggleHistory} />
      </div>
    </header>
  );
}
