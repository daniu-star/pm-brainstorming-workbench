"use client";

import { useEffect, useState, useRef, Suspense } from "react";
import { useParams, useSearchParams, useRouter } from "next/navigation";
import { useSessionStore } from "@/store/sessionStore";
import { Header } from "@/components/Header";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { CanvasPanel } from "@/components/canvas/CanvasPanel";
import { HistoryDrawer } from "@/components/HistoryDrawer";
import { ROLES } from "@/lib/types";
import { BrainIcon } from "@/components/icons";
import { exportSessionAsMarkdown } from "@/lib/export";

function WorkbenchContent() {
  const params = useParams();
  const searchParams = useSearchParams();
  const {
    sessionId,
    loadSession,
    sendToCoach,
    abortStream,
    messages,
    isHistoryOpen,
    toggleHistory,
  } = useSessionStore();
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [mobileTab, setMobileTab] = useState<"canvas" | "chat">("chat");
  const triggeredRef = useRef(false);
  const router = useRouter();

  useEffect(() => {
    const id = Array.isArray(params?.id) ? params.id[0] : params?.id;
    if (!id) return;
    loadSession(id)
      .then(() => setLoading(false))
      .catch((err) => {
        const msg = err instanceof Error ? err.message : "加载会话失败";
        if (msg.includes("会话未找到") || msg.includes("404")) {
          const problem = searchParams.get("problem") || "";
          useSessionStore.getState().createSession(problem || "新的脑暴会话").then(() => {
            const newId = useSessionStore.getState().sessionId;
            if (newId && newId !== id) {
              router.replace(`/session/${newId}?problem=${encodeURIComponent(problem)}`);
            } else {
              setLoading(false);
            }
          }).catch(() => {
            setLoadError("会话已过期，请返回首页重新开始");
            setLoading(false);
          });
        } else {
          setLoadError(msg);
          setLoading(false);
        }
      });
  }, [params?.id, loadSession, searchParams, router]);

  useEffect(() => {
    return () => {
      abortStream();
    };
  }, [abortStream]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === "E") {
        e.preventDefault();
        const state = useSessionStore.getState();
        if (state.sessionId && state.messages.length > 0) {
          exportSessionAsMarkdown(
            state.messages[0]?.content?.slice(0, 50) || "脑暴会话",
            state.messages,
            state.discussionMap
          );
        }
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  useEffect(() => {
    if (!loading && sessionId && !triggeredRef.current) {
      const problem = searchParams.get("problem");
      if (problem) {
        const hasCoachMessages = messages.some((m) => m.role_name === "coach" || m.role === "assistant");
        if (!hasCoachMessages) {
          triggeredRef.current = true;
          sendToCoach(problem);
        }
      }
    }
  }, [loading, sessionId, searchParams, sendToCoach, messages]);

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-warm-50 bg-warm-workbench bg-grid-warm">
        <div className="text-center">
          <div className="mb-6 flex justify-center">
            <BrainIcon size={48} className="text-amber-500 animate-pulse" />
          </div>
          <div className="text-warm-600 text-lg font-medium mb-2">正在召集专家团队...</div>
          <div className="text-warm-500 text-sm mb-6">技术负责人 · 设计师 · 运营负责人 · 目标用户</div>
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
                <img src={`/avatars/${r.id}.svg`} alt={r.name} className="w-full h-full object-cover" />
              </div>
            ))}
          </div>
          <div className="mt-4 w-48 h-1 bg-warm-100 rounded-full mx-auto overflow-hidden">
            <div className="h-full bg-amber-500/50 rounded-full animate-pulse" style={{ width: "60%" }} />
          </div>
        </div>
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-orange-50 via-amber-50 to-rose-50">
        <div className="text-center max-w-md bg-white border border-amber-200 rounded-2xl p-8 shadow-lg shadow-amber-100">
          <div className="w-14 h-14 rounded-full bg-amber-50 border border-amber-200 flex items-center justify-center mx-auto mb-4">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#d97706" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="10" /><line x1="12" y1="8" x2="12" y2="12" /><line x1="12" y1="16" x2="12.01" y2="16" />
            </svg>
          </div>
          <div className="text-amber-900 text-lg font-semibold mb-2">会话加载失败</div>
          <div className="text-amber-700 text-sm mb-5">{loadError}</div>
          <a
            href="/"
            aria-label="返回首页"
            className="inline-flex items-center gap-2 px-5 py-2.5 bg-gradient-to-r from-amber-500 to-orange-500 hover:from-amber-600 hover:to-orange-600 text-white text-sm font-semibold rounded-xl shadow-md shadow-amber-200 transition-all"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M19 12H5M12 19l-7-7 7-7" /></svg>
            返回首页重新开始
          </a>
        </div>
      </div>
    );
  }

  return (
    <div className="h-screen flex flex-col bg-warm-50 bg-warm-workbench">
      <Header />
      <HistoryDrawer isOpen={isHistoryOpen} onClose={toggleHistory} />
      <div className="flex-1 flex overflow-hidden relative">
        <div className="md:hidden flex border-b border-warm-200 bg-white shrink-0">
          <button
            onClick={() => setMobileTab("canvas")}
            aria-label="切换到画布视图"
            className={`flex-1 py-2.5 text-sm font-medium text-center transition-all duration-200 min-h-[44px] focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none ${
              mobileTab === "canvas" ? "text-amber-600 border-b-2 border-amber-500" : "text-warm-500 hover:text-warm-600"
            }`}
          >
            画布
          </button>
          <button
            onClick={() => setMobileTab("chat")}
            aria-label="切换到聊天视图"
            className={`flex-1 py-2.5 text-sm font-medium text-center transition-all duration-200 min-h-[44px] focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none ${
              mobileTab === "chat" ? "text-amber-600 border-b-2 border-amber-500" : "text-warm-500 hover:text-warm-600"
            }`}
          >
            聊天
          </button>
        </div>

        <div className={`flex-1 flex flex-col bg-warm-50 min-w-0 ${
          mobileTab === "canvas" ? "flex" : "hidden md:flex"
        }`}>
          <CanvasPanel />
        </div>

        <div className={`border-l border-warm-200 flex flex-col bg-warm-50 ${
          mobileTab === "chat" ? "flex w-full" : "hidden md:flex"
        } md:w-[440px] md:min-w-[380px]`}>
          <ChatPanel />
        </div>
      </div>
    </div>
  );
}

export default function WorkbenchClientPage() {
  return (
    <Suspense fallback={
      <div className="min-h-screen flex items-center justify-center bg-warm-50 bg-warm-workbench bg-grid-warm">
        <div className="w-10 h-10 border-2 border-amber-500/30 border-t-amber-500 rounded-full animate-spin" />
      </div>
    }>
      <WorkbenchContent />
    </Suspense>
  );
}
