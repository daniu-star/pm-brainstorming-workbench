"use client";

import { useEffect, useState, Suspense } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useSessionStore } from "@/store/sessionStore";
import { Header } from "@/components/Header";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { CanvasPanel } from "@/components/canvas/CanvasPanel";
import { BriefCard } from "@/components/brief/BriefCard";
import { HistoryDrawer } from "@/components/HistoryDrawer";
import { ROLES } from "@/lib/types";
import { BrainIcon } from "@/components/icons";
import { shouldShowLoopEntry } from "@/lib/loopView";

type MobileTab = "canvas" | "chat";

function WorkbenchContent() {
  const params = useParams();
  const {
    sessionId,
    loadSession,
    abortStream,
    unsubscribe,
    isHistoryOpen,
    toggleHistory,
    sessionView,
    legacyMode,
  } = useSessionStore();
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [mobileTab, setMobileTab] = useState<MobileTab>("chat");

  useEffect(() => {
    const id = params?.id as string;
    if (!id) return;
    let cancelled = false;
    // 初始问题已由后端 create_session_core 写入并启动首个 clarify Run
    // （backend/api/v2/common.py:397-429）：这里只加载会话，绝不再发一次 clarify。
    loadSession(id)
      .then(() => {
        if (cancelled) return;
        setLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        setLoadError(err instanceof Error ? err.message : "加载会话失败");
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [params?.id, loadSession]);

  // Cleanup SSE / run subscription on unmount. Switching sessions only
  // cancels the local subscription — it never cancels the server-side run.
  useEffect(() => {
    return () => {
      abortStream();
      unsubscribe();
    };
  }, [abortStream, unsubscribe]);

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-dark-900 bg-mesh bg-grid">
        <div className="text-center">
          <div className="mb-6 flex justify-center">
            <BrainIcon size={48} className="text-brand-400 animate-pulse" />
          </div>
          <div className="text-zinc-300 text-lg font-medium mb-2">正在召集专家团队...</div>
          <div className="text-zinc-500 text-sm mb-6">技术负责人 · 设计师 · 运营负责人 · 目标用户</div>
          <div className="flex justify-center gap-4">
            {ROLES.map((r, i) => (
              <div
                key={r.id}
                className="w-10 h-10 rounded-full overflow-hidden animate-bounce"
                style={{
                  border: `1.5px solid ${r.color}30`,
                  animationDelay: `${i * 0.15}s`,
                }}
              >
                <img src={`/avatars/${r.id}.svg`} alt={r.name} loading="lazy" className="w-full h-full object-cover" />
              </div>
            ))}
          </div>
          <div className="mt-4 w-48 h-1 bg-dark-700 rounded-full mx-auto overflow-hidden">
            <div className="h-full bg-brand-500/50 rounded-full animate-pulse" style={{ width: "60%" }} />
          </div>
        </div>
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-dark-900 bg-mesh bg-grid">
        <div className="text-center max-w-md">
          <div className="text-red-400 text-lg font-semibold mb-2">加载失败</div>
          <div className="text-zinc-400 text-sm mb-4">{loadError}</div>
          <a href="/" className="text-brand-300 hover:text-brand-400 text-sm underline">
            返回首页
          </a>
        </div>
      </div>
    );
  }

  return (
    <div className="h-screen flex flex-col bg-dark-900 bg-mesh">
      <Header />
      <HistoryDrawer isOpen={isHistoryOpen} onClose={toggleHistory} />

      {/* M3 审计入口：v2 会话进入 brainstorming 之后才出现（legacy 流程没有审计） */}
      {(() => {
        if (legacyMode || !sessionView) return null;
        const v2Phase = sessionView.phase;
        if (
          v2Phase !== "brainstorming" &&
          v2Phase !== "auditing" &&
          v2Phase !== "audit_completed"
        ) {
          return null;
        }
        const targetId = (params?.id as string) ?? sessionId;
        if (!targetId) return null;
        const auditing = v2Phase === "auditing";
        return (
          <div className="flex shrink-0 items-center gap-3 border-b border-zinc-800/50 bg-dark-800/40 px-4 py-1.5">
            <span className="text-[11px] text-zinc-500">
              {v2Phase === "audit_completed"
                ? "审计已完成，可回看报告"
                : auditing
                  ? "审计进行中"
                  : "方案已收敛，可进入六维审计"}
            </span>
            <Link
              href={`/session/${targetId}/audit`}
              className="ml-auto inline-flex min-h-[32px] items-center rounded-lg border border-brand-500/40 bg-brand-600/15 px-3 text-[11px] font-medium text-brand-300 transition-colors hover:bg-brand-600/25"
            >
              {auditing ? "继续审计" : v2Phase === "audit_completed" ? "查看审计报告" : "进入审计"}
            </Link>
          </div>
        );
      })()}

      {/* Loop 工作室入口：brainstorming / audit_completed 且非 legacy（契约 §7） */}
      {(() => {
        if (!sessionView) return null;
        if (!shouldShowLoopEntry({ v2Phase: sessionView.phase, legacyMode })) return null;
        const targetId = (params?.id as string) ?? sessionId;
        if (!targetId) return null;
        const audited = sessionView.phase === "audit_completed";
        return (
          <div className="flex shrink-0 items-center gap-3 border-b border-zinc-800/50 bg-dark-800/40 px-4 py-1.5">
            <span className="text-[11px] text-zinc-500">
              {audited
                ? "审计已完成，可进入多轮闭环打磨方案"
                : "进入闭环工作室，五阶段打磨最小可用方案"}
            </span>
            <Link
              href={`/session/${targetId}/loop`}
              className="ml-auto inline-flex min-h-[32px] items-center rounded-lg border border-brand-500/40 bg-brand-600/15 px-3 text-[11px] font-medium text-brand-300 transition-colors hover:bg-brand-600/25"
            >
              进入 Loop 工作室
            </Link>
          </div>
        );
      })()}

      {/* Mobile view switcher (< md) */}
      <div
        className="flex shrink-0 border-b border-zinc-800/50 bg-dark-900/60 md:hidden"
        role="tablist"
        aria-label="工作台视图切换"
      >
        <button
          role="tab"
          aria-selected={mobileTab === "canvas"}
          onClick={() => setMobileTab("canvas")}
          className={`flex-1 min-h-[44px] text-xs font-medium transition-colors ${
            mobileTab === "canvas"
              ? "text-brand-300 border-b-2 border-brand-300 bg-brand-500/5"
              : "text-zinc-500 hover:text-zinc-300"
          }`}
        >
          画布
        </button>
        <button
          role="tab"
          aria-selected={mobileTab === "chat"}
          onClick={() => setMobileTab("chat")}
          className={`flex-1 min-h-[44px] text-xs font-medium transition-colors ${
            mobileTab === "chat"
              ? "text-brand-300 border-b-2 border-brand-300 bg-brand-500/5"
              : "text-zinc-500 hover:text-zinc-300"
          }`}
        >
          聊天
        </button>
      </div>

      <div className="flex-1 flex flex-col overflow-hidden min-h-0 md:flex-row">
        {/* LEFT: Visual Canvas — real-time whiteboard */}
        <div
          className={`flex-1 flex-col bg-dark-900/50 min-w-0 min-h-0 ${
            mobileTab === "canvas" ? "flex" : "hidden"
          } md:flex`}
        >
          <CanvasPanel />
        </div>
        {/* RIGHT: WeChat-style Group Chat */}
        <div
          className={`flex-1 flex-col bg-dark-900/70 min-w-0 min-h-0 border-t border-zinc-800/50 md:flex-none md:w-[440px] md:border-t-0 md:border-l md:border-zinc-800/50 ${
            mobileTab === "chat" ? "flex" : "hidden"
          } md:flex`}
        >
          <BriefCard />
          <ChatPanel />
        </div>
      </div>
    </div>
  );
}

export default function WorkbenchPage() {
  return (
    <Suspense fallback={
      <div className="min-h-screen flex items-center justify-center bg-dark-900 bg-mesh bg-grid">
        <div className="w-10 h-10 border-2 border-brand-500/30 border-t-brand-500 rounded-full animate-spin" />
      </div>
    }>
      <WorkbenchContent />
    </Suspense>
  );
}
