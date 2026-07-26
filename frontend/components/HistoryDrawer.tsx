"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import type { SessionSummary } from "@/lib/types";
import { CloseIcon } from "@/components/icons";

interface HistoryDrawerProps {
  isOpen: boolean;
  onClose: () => void;
}

const PHASE_BADGES: Record<string, { label: string; color: string }> = {
  brainstorm: { label: "脑暴", color: "bg-amber-50 text-amber-600 border-amber-200" },
  audit: { label: "审计", color: "bg-red-50 text-red-600 border-red-200" },
  clarify: { label: "澄清", color: "bg-cyan-50 text-cyan-700 border-cyan-200" },
  draft: { label: "草稿", color: "bg-warm-100 text-warm-500 border-warm-300" },
  decision_ready: { label: "待决策", color: "bg-indigo-50 text-indigo-700 border-indigo-200" },
  completed: { label: "已完成", color: "bg-emerald-50 text-emerald-700 border-emerald-200" },
  archived: { label: "已归档", color: "bg-slate-100 text-slate-600 border-slate-200" },
};

function formatTime(iso: string): string {
  const d = new Date(iso);
  const now = new Date();
  const diff = now.getTime() - d.getTime();
  const mins = Math.floor(diff / 60000);
  const hours = Math.floor(diff / 3600000);
  const days = Math.floor(diff / 86400000);

  if (mins < 1) return "刚刚";
  if (mins < 60) return `${mins} 分钟前`;
  if (hours < 24) return `${hours} 小时前`;
  if (days < 30) return `${days} 天前`;
  return d.toLocaleDateString("zh-CN");
}

export function HistoryDrawer({ isOpen, onClose }: HistoryDrawerProps) {
  const router = useRouter();
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const drawerRef = useRef<HTMLDivElement>(null);

  const fetchSessions = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api<SessionSummary[]>("/api/session");
      setSessions(data);
    } catch {
      setError("加载历史会话失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isOpen) {
      fetchSessions();
    }
  }, [isOpen, fetchSessions]);

  useEffect(() => {
    if (!isOpen) return;
    const drawer = drawerRef.current;
    if (!drawer) return;
    const previouslyFocused = document.activeElement as HTMLElement;
    const firstFocusable = drawer.querySelector<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    );
    firstFocusable?.focus();
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key !== "Tab" || !drawer) return;
      const focusable = drawer.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
    drawer.addEventListener("keydown", handleKeyDown);
    return () => {
      drawer.removeEventListener("keydown", handleKeyDown);
      previouslyFocused?.focus();
    };
  }, [isOpen]);

  if (!isOpen) return null;

  return (
    <>
      <div
        className="fixed inset-0 bg-warm-600/30 backdrop-blur-sm z-40"
        onClick={onClose}
        aria-hidden="true"
      />

      <div
        ref={drawerRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="drawer-title"
        className="fixed top-0 right-0 h-full w-[320px] max-w-[85vw] bg-warm-50 border-l border-warm-200 z-50 flex flex-col shadow-xl animate-[slideInRight_0.2s_ease-out]"
      >
        <div className="h-12 border-b border-warm-200 flex items-center justify-between px-4 shrink-0">
          <span id="drawer-title" className="text-sm font-medium text-warm-600">历史会话</span>
          <button
            onClick={onClose}
            aria-label="关闭历史会话"
            className="min-h-[44px] min-w-[44px] flex items-center justify-center text-warm-500 hover:text-warm-600 active:text-warm-700 transition-all duration-200 focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none rounded"
          >
            <CloseIcon size={16} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto">
          {loading && (
            <div className="p-3 space-y-3">
              {[1, 2, 3, 4].map((i) => (
                <div key={i} className="bg-white rounded-xl p-4 animate-pulse space-y-3">
                  <div className="h-4 bg-warm-200 rounded w-3/4" />
                  <div className="h-3 bg-warm-200 rounded w-1/2" />
                  <div className="flex gap-2">
                    <div className="h-5 w-12 bg-warm-200 rounded-full" />
                    <div className="h-5 w-16 bg-warm-200 rounded-full" />
                  </div>
                </div>
              ))}
            </div>
          )}

          {error && (
            <div className="p-4 text-center">
              <p className="text-sm text-red-600 mb-3">{error}</p>
              <button
                onClick={fetchSessions}
                aria-label="重新加载历史会话"
                className="px-4 py-2 text-xs text-amber-600 hover:text-amber-700 active:text-amber-800 bg-amber-50 rounded-lg transition-all duration-200 min-h-[44px] focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none"
              >
                重试
              </button>
            </div>
          )}

          {!loading && !error && sessions.length === 0 && (
            <div className="p-8 text-center">
              <p className="text-sm text-warm-500 mb-4">暂无历史会话</p>
              <button
                onClick={() => { onClose(); router.push("/"); }}
                aria-label="创建新会话"
                className="px-4 py-2 text-xs text-amber-600 hover:text-amber-700 active:text-amber-800 bg-amber-50 rounded-lg transition-all duration-200 min-h-[44px] focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none"
              >
                创建新会话
              </button>
            </div>
          )}

          {!loading && !error && sessions.length > 0 && (
            <div className="p-3 space-y-2">
              {sessions.map((s) => {
                const badge = PHASE_BADGES[s.phase] || PHASE_BADGES.draft;
                return (
                  <button
                    key={s.id}
                    onClick={() => { onClose(); router.push(`/session/${s.id}`); }}
                    aria-label={`打开会话：${s.problem_statement || "未命名会话"}`}
                    className="w-full bg-white hover:bg-warm-100 active:scale-[0.98] border border-warm-200 rounded-xl p-4 text-left transition-all duration-200 focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none"
                  >
                    <p className="text-sm text-warm-600 truncate mb-2">
                      {s.problem_statement || "未命名会话"}
                    </p>
                    <div className="flex items-center gap-2">
                      <span className={`text-[11px] px-2 py-0.5 rounded-full border ${badge.color}`}>
                        {badge.label}
                      </span>
                      <span className="text-[11px] text-warm-500">
                        {s.message_count} 条消息
                      </span>
                      <span className="text-[11px] text-warm-500 ml-auto">
                        {formatTime(s.created_at)}
                      </span>
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </div>

    </>
  );
}
