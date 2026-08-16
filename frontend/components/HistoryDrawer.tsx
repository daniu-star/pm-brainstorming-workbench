"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useSessionStore } from "@/store/sessionStore";
import { api } from "@/lib/api";

interface HistoryDrawerProps {
  isOpen: boolean;
  onClose: () => void;
}

const PHASE_BADGES: Record<string, { label: string; color: string }> = {
  brainstorm: { label: "脑暴", color: "bg-brand-500/20 text-brand-300 border-brand-500/30" },
  interview: { label: "面试", color: "bg-red-500/20 text-red-400 border-red-500/30" },
  coach: { label: "梳理", color: "bg-amber-500/20 text-amber-400 border-amber-500/30" },
  define: { label: "定义", color: "bg-zinc-500/20 text-zinc-400 border-zinc-500/30" },
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
  const { historySessions, historyLoading, historyError, fetchHistory } = useSessionStore();
  const drawerRef = useRef<HTMLDivElement>(null);
  const lastActiveElementRef = useRef<HTMLElement | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  // 删除会话（带确认），完成后刷新列表（B115）。
  const handleDelete = async (id: string) => {
    if (!window.confirm("确定删除该会话吗？删除后不可恢复。")) return;
    setDeletingId(id);
    try {
      await api(`/api/session/${id}`, { method: "DELETE" });
      await fetchHistory();
    } catch {
      // 删除失败时静默保留条目，下次打开重试
    } finally {
      setDeletingId(null);
    }
  };

  useEffect(() => {
    if (!isOpen) return;

    // Remember the trigger element so focus can be restored on close
    lastActiveElementRef.current = document.activeElement as HTMLElement | null;

    fetchHistory();

    // Move focus into the drawer
    requestAnimationFrame(() => drawerRef.current?.focus());

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);

    return () => {
      window.removeEventListener("keydown", onKeyDown);
      // Return focus to the element that opened the drawer
      lastActiveElementRef.current?.focus?.();
    };
  }, [isOpen, fetchHistory, onClose]);

  if (!isOpen) return null;

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/50 z-40"
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Drawer */}
      <div
        ref={drawerRef}
        role="dialog"
        aria-modal="true"
        aria-label="历史会话"
        tabIndex={-1}
        className="fixed top-0 right-0 h-full w-[360px] max-w-[90vw] bg-dark-900 border-l border-zinc-800/50 z-50 flex flex-col shadow-2xl animate-[slideInRight_0.2s_ease-out] outline-none"
      >
        {/* Header */}
        <div className="h-12 border-b border-zinc-800/50 flex items-center justify-between px-4 shrink-0">
          <span className="text-sm font-medium text-zinc-300">历史会话</span>
          <button
            onClick={onClose}
            className="min-h-[44px] min-w-[44px] flex items-center justify-center text-zinc-500 hover:text-zinc-300 transition-colors"
            aria-label="关闭历史会话"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto">
          {historyLoading && (
            <div className="p-3 space-y-3">
              {[1, 2, 3, 4].map((i) => (
                <div key={i} className="bg-dark-800/60 rounded-xl p-4 animate-pulse space-y-3">
                  <div className="h-4 bg-zinc-700/50 rounded w-3/4" />
                  <div className="h-3 bg-zinc-700/30 rounded w-1/2" />
                  <div className="flex gap-2">
                    <div className="h-5 w-12 bg-zinc-700/30 rounded-full" />
                    <div className="h-5 w-16 bg-zinc-700/20 rounded-full" />
                  </div>
                </div>
              ))}
            </div>
          )}

          {historyError && (
            <div className="p-4 text-center">
              <p className="text-sm text-red-400 mb-3">{historyError}</p>
              <button
                onClick={() => void fetchHistory()}
                className="px-4 py-2 text-xs text-brand-300 hover:text-brand-400 bg-brand-500/10 rounded-lg transition-colors min-h-[44px] min-w-[44px]"
              >
                重试
              </button>
            </div>
          )}

          {!historyLoading && !historyError && historySessions.length === 0 && (
            <div className="p-8 text-center">
              <p className="text-sm text-zinc-500 mb-4">暂无历史会话</p>
              <button
                onClick={() => { onClose(); router.push("/"); }}
                className="px-4 py-2 text-xs text-brand-300 hover:text-brand-400 bg-brand-500/10 rounded-lg transition-colors min-h-[44px] min-w-[44px]"
              >
                创建新会话
              </button>
            </div>
          )}

          {!historyLoading && !historyError && historySessions.length > 0 && (
            <div className="p-3 space-y-2">
              {historySessions.map((s) => {
                const badge = PHASE_BADGES[s.phase] || PHASE_BADGES.define;
                return (
                  <div
                    key={s.id}
                    className="group relative bg-dark-800/60 hover:bg-dark-700/60 border border-zinc-800/40 rounded-xl transition-all duration-200"
                  >
                    <button
                      onClick={() => { onClose(); router.push(`/session/${s.id}`); }}
                      className="w-full p-4 text-left"
                    >
                      <p className="text-sm text-zinc-200 truncate mb-2">
                        {s.problem_statement || "未命名会话"}
                      </p>
                      <div className="flex items-center gap-2">
                        <span className={`text-[10px] px-2 py-0.5 rounded-full border ${badge.color}`}>
                          {badge.label}
                        </span>
                        <span className="text-[10px] text-zinc-500">
                          {s.message_count} 条消息
                        </span>
                        <span className="text-[10px] text-zinc-500 ml-auto">
                          {formatTime(s.created_at)}
                        </span>
                      </div>
                    </button>
                    <button
                      onClick={() => void handleDelete(s.id)}
                      disabled={deletingId === s.id}
                      aria-label={`删除会话：${s.problem_statement || "未命名会话"}`}
                      className="touch-reveal absolute top-2 right-2 min-h-[36px] min-w-[36px] rounded-lg text-zinc-600 hover:text-red-400 hover:bg-red-500/10 opacity-0 group-hover:opacity-100 focus-visible:opacity-100 focus-visible:pointer-events-auto pointer-events-none group-hover:pointer-events-auto transition-all flex items-center justify-center disabled:opacity-50"
                    >
                      {deletingId === s.id ? (
                        <span className="inline-block w-3 h-3 border-2 border-zinc-500 border-t-transparent rounded-full animate-spin" />
                      ) : (
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
                        </svg>
                      )}
                    </button>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      <style jsx>{`
        @keyframes slideInRight {
          from { transform: translateX(100%); }
          to { transform: translateX(0); }
        }
      `}</style>
    </>
  );
}
