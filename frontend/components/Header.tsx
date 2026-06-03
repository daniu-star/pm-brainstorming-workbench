"use client";

import { useSessionStore } from "@/store/sessionStore";
import { exportSessionAsMarkdown } from "@/lib/export";
import Link from "next/link";
import { BrainIcon } from "@/components/icons";
import { NavButtons } from "@/components/NavButtons";
import { SettingsModal } from "@/components/SettingsModal";
import { RechargeModal } from "@/components/RechargeModal";
import { OnboardingModal } from "@/components/OnboardingModal";

const PHASE_LABELS: Record<string, { label: string; color: string }> = {
  coach: { label: "产品教练 · 思路梳理", color: "text-amber-400" },
  brainstorm: { label: "多角色脑暴", color: "text-amber-600" },
  interview: { label: "AI 面试官", color: "text-red-400" },
};

function formatQuota(remaining: number): string {
  if (remaining >= 10000) return `${(remaining / 10000).toFixed(1)}万`;
  return remaining.toLocaleString();
}

export function Header() {
  const phase = useSessionStore((s) => s.phase);
  const sessionId = useSessionStore((s) => s.sessionId);
  const messages = useSessionStore((s) => s.messages);
  const discussionMap = useSessionStore((s) => s.discussionMap);
  const toggleHistory = useSessionStore((s) => s.toggleHistory);
  const userApiKey = useSessionStore((s) => s.userApiKey);
  const tokenQuota = useSessionStore((s) => s.tokenQuota);
  const tokensUsed = useSessionStore((s) => s.tokensUsed);
  const setSettingsOpen = useSessionStore((s) => s.setSettingsOpen);
  const setRechargeOpen = useSessionStore((s) => s.setRechargeOpen);
  const setOnboardingOpen = useSessionStore((s) => s.setOnboardingOpen);
  const hasCompletedOnboarding = useSessionStore((s) => s.hasCompletedOnboarding);

  const phaseInfo = PHASE_LABELS[phase] || { label: "脑暴中", color: "text-warm-500" };
  const remaining = tokenQuota - tokensUsed;
  const isByok = !!userApiKey;
  const quotaLow = !isByok && remaining < 10000;
  const quotaEmpty = !isByok && remaining <= 0;
  const needsSetup = !isByok && !hasCompletedOnboarding;

  return (
    <>
      <header className="h-14 bg-white/90 backdrop-blur border-b border-warm-200 shadow-sm flex items-center justify-between px-4 shrink-0">
        <div className="flex items-center gap-4">
          <Link
            href="/"
            aria-label="返回首页"
            className="text-amber-600 hover:text-amber-700 active:text-amber-800 font-bold text-sm transition-all duration-200 flex items-center gap-2 focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none rounded"
          >
            <BrainIcon size={20} className="text-amber-600" />
            PM Brainstorm
          </Link>
          <span className="border-l border-warm-200 h-4" />
          <span className={`text-sm font-semibold px-2 py-0.5 rounded-full ${phase === 'interview' ? 'bg-red-50 text-red-600' : phase === 'coach' ? 'bg-amber-50 text-amber-600' : 'bg-amber-50 text-amber-700'}`}>{phaseInfo.label}</span>
        </div>

        <div className="flex items-center gap-3">
          {needsSetup && (
            <button
              onClick={() => setOnboardingOpen(true)}
              aria-label="配置 API"
              className="text-xs flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-amber-400 bg-amber-50 border-amber-200 hover:bg-amber-100 active:bg-amber-200 transition-all duration-200 min-h-[32px] focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none"
            >
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z M12 9v4 M12 17h.01" />
              </svg>
              未配置 API
            </button>
          )}

          {isByok ? (
            <span className="text-xs text-emerald-600 flex items-center gap-1.5 px-2 py-1 bg-emerald-50 rounded-full border border-emerald-200">
              <span className="w-1.5 h-1.5 bg-emerald-500 rounded-full" />
              自带 Key
            </span>
          ) : (
            <button
              onClick={() => setRechargeOpen(true)}
              aria-label="查看额度"
              className={`text-xs flex items-center gap-1.5 px-2 py-1 rounded-full border transition-all duration-200 min-h-[32px] active:scale-95 focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none ${
                quotaEmpty
                  ? "text-red-400 bg-red-50 border-red-200 hover:bg-red-100"
                  : quotaLow
                    ? "text-amber-400 bg-amber-50 border-amber-200 hover:bg-amber-100"
                    : "text-warm-500 bg-warm-100 border-warm-200 hover:bg-warm-200"
              }`}
            >
              <span className={`w-1.5 h-1.5 rounded-full ${quotaEmpty ? "bg-red-500" : quotaLow ? "bg-amber-500" : "bg-warm-400"}`} />
              {formatQuota(remaining)} tokens
            </button>
          )}

          <button
            onClick={() => {
              if (sessionId && messages.length > 0) {
                exportSessionAsMarkdown(
                  messages[0]?.content?.slice(0, 50) || "脑暴会话",
                  messages,
                  discussionMap
                );
              }
            }}
            disabled={!sessionId || messages.length === 0}
            aria-label="导出会话"
            className="text-warm-500 hover:text-warm-600 active:text-warm-700 transition-all duration-200 min-h-[44px] min-w-[44px] flex items-center justify-center focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none rounded disabled:opacity-40"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
              <polyline points="7 10 12 15 17 10"/>
              <line x1="12" y1="15" x2="12" y2="3"/>
            </svg>
          </button>

          <button
            onClick={() => setSettingsOpen(true)}
            className="text-warm-500 hover:text-warm-600 active:text-warm-700 transition-all duration-200 min-h-[44px] min-w-[44px] flex items-center justify-center focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none rounded"
            aria-label="API 设置"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/>
              <circle cx="12" cy="12" r="3"/>
            </svg>
          </button>

          <NavButtons
            currentPage="workbench"
            sessionId={sessionId}
            onToggleHistory={toggleHistory}
          />
        </div>
      </header>
      <SettingsModal />
      <RechargeModal />
      <OnboardingModal />
    </>
  );
}
