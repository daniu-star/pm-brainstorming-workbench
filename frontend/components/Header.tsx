"use client";

import { useSessionStore } from "@/store/sessionStore";
import Link from "next/link";
import { BrainIcon } from "@/components/icons";
import { NavButtons } from "@/components/NavButtons";

const PHASE_LABELS: Record<string, { label: string; color: string }> = {
  coach: { label: "产品教练 · 思路梳理", color: "text-amber-400" },
  brainstorm: { label: "多角色脑暴", color: "text-brand-300" },
  interview: { label: "AI 面试官", color: "text-red-400" },
};

export function Header() {
  const { phase, sessionId, toggleHistory } = useSessionStore();

  const phaseInfo = PHASE_LABELS[phase] || { label: "脑暴中", color: "text-zinc-400" };

  return (
    <header className="h-14 bg-dark-800/80 backdrop-blur border-b border-zinc-800/50 flex items-center justify-between px-4 shrink-0">
      <div className="flex items-center gap-4">
        <Link
          href="/"
          className="flex items-center gap-2.5 transition-colors"
          aria-label="PM Brainstorm 首页"
        >
          <span className="flex size-8 items-center justify-center rounded-lg border border-brand-300/30 bg-brand-300/10 text-brand-300">
            <BrainIcon size={17} />
          </span>
          <span className="leading-tight">
            <span className="block text-xs font-bold tracking-wide text-brand-300">PM BRAINSTORM</span>
            <span className="block text-[10px] text-zinc-500">产品决策智能工作台</span>
          </span>
        </Link>
        <span className="text-zinc-500">|</span>
        <span className={`text-xs font-medium ${phaseInfo.color}`}>{phaseInfo.label}</span>
      </div>

      <div className="flex items-center gap-3">
        <NavButtons
          currentPage="workbench"
          sessionId={sessionId}
          onToggleHistory={toggleHistory}
        />
      </div>
    </header>
  );
}
