"use client";

import { useSessionStore } from "@/store/sessionStore";
import { exportSessionAsMarkdown } from "@/lib/export";
import Link from "next/link";
import { Brain, Download, Settings, AlertTriangle, Wallet } from "lucide-react";
import { NavButtons } from "@/components/NavButtons";
import { SettingsModal } from "@/components/SettingsModal";
import { RechargeModal } from "@/components/RechargeModal";
import { OnboardingModal } from "@/components/OnboardingModal";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

const PHASE_LABELS: Record<string, { label: string; variant: "default" | "secondary" | "destructive" }> = {
  coach: { label: "产品教练 · 思路梳理", variant: "secondary" },
  brainstorm: { label: "多角色脑暴", variant: "default" },
  interview: { label: "AI 面试官", variant: "destructive" },
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

  const phaseInfo = PHASE_LABELS[phase] || { label: "脑暴中", variant: "default" as const };
  const remaining = tokenQuota - tokensUsed;
  const isByok = !!userApiKey;
  const quotaLow = !isByok && remaining < 10000;
  const quotaEmpty = !isByok && remaining <= 0;
  const needsSetup = !isByok && !hasCompletedOnboarding;

  return (
    <>
      <header className="h-14 bg-background/90 backdrop-blur border-b border-border shadow-sm flex items-center justify-between px-4 shrink-0">
        <div className="flex items-center gap-4">
          <Link
            href="/"
            aria-label="返回首页"
            className="text-primary hover:text-primary/80 font-bold text-sm transition-all duration-200 flex items-center gap-2 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none rounded"
          >
            <Brain className="h-5 w-5 text-primary" />
            PM Brainstorm
          </Link>
          <span className="border-l border-border h-4" />
          <Badge variant={phaseInfo.variant}>{phaseInfo.label}</Badge>
        </div>

        <div className="flex items-center gap-2">
          {needsSetup && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => setOnboardingOpen(true)}
              className="text-xs h-8 border-amber-300 bg-amber-50 text-amber-600 hover:bg-amber-100"
            >
              <AlertTriangle className="h-3 w-3 mr-1" />
              未配置 API
            </Button>
          )}

          {isByok ? (
            <Badge variant="secondary" className="text-xs bg-emerald-50 text-emerald-600 border-emerald-200">
              <span className="w-1.5 h-1.5 bg-emerald-500 rounded-full mr-1" />
              自带 Key
            </Badge>
          ) : (
            <Button
              variant="outline"
              size="sm"
              onClick={() => setRechargeOpen(true)}
              className={`text-xs h-8 ${
                quotaEmpty
                  ? "border-red-200 bg-red-50 text-red-500 hover:bg-red-100"
                  : quotaLow
                    ? "border-amber-200 bg-amber-50 text-amber-500 hover:bg-amber-100"
                    : "border-border bg-muted text-muted-foreground hover:bg-accent"
              }`}
            >
              <Wallet className="h-3 w-3 mr-1" />
              {formatQuota(remaining)} tokens
            </Button>
          )}

          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="h-9 w-9"
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
              >
                <Download className="h-4 w-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>导出会话</TooltipContent>
          </Tooltip>

          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="h-9 w-9"
                onClick={() => setSettingsOpen(true)}
                aria-label="API 设置"
              >
                <Settings className="h-4 w-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>API 设置</TooltipContent>
          </Tooltip>

          <ThemeToggle />

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
