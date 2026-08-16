"use client";

import { useEffect, useState, Suspense } from "react";
import { useParams, useSearchParams } from "next/navigation";
import { useSessionStore } from "@/store/sessionStore";
import { Header } from "@/components/Header";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { CanvasPanel } from "@/components/canvas/CanvasPanel";
import { HistoryDrawer } from "@/components/HistoryDrawer";
import { ROLES } from "@/lib/types";
import { BrainIcon } from "@/components/icons";

type MobileTab = "canvas" | "chat";

function WorkbenchContent() {
  const params = useParams();
  const searchParams = useSearchParams();
  const {
    sessionId,
    loadSession,
    abortStream,
    isHistoryOpen,
    toggleHistory,
  } = useSessionStore();
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [mobileTab, setMobileTab] = useState<MobileTab>("chat");

  useEffect(() => {
    const id = params?.id as string;
    if (!id) return;
    let cancelled = false;
    loadSession(id)
      .then(() => {
        if (cancelled) return;
        setLoading(false);
        // Auto-trigger coach clarification first (not direct brainstorm).
        // Guarded by store state so React StrictMode's double-invoke
        // cannot fire the coach message twice (or abort it mid-flight).
        const problem = searchParams.get("problem");
        if (problem) {
          const store = useSessionStore.getState();
          if (store.coachTriggeredFor !== id) {
            store.setCoachTriggered(id);
            store.sendToCoach(problem);
          }
        }
      })
      .catch((err) => {
        if (cancelled) return;
        setLoadError(err instanceof Error ? err.message : "加载会话失败");
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [params?.id, loadSession, searchParams]);

  // Cleanup SSE on unmount
  useEffect(() => {
    return () => {
      abortStream();
    };
  }, [abortStream]);

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
