"use client";

import { useSessionStore } from "@/store/sessionStore";
import { ROLE_MAP } from "@/lib/types";
import { exportSessionAsMarkdown } from "@/lib/export";
import { MessageList } from "./MessageList";
import { RoleSelector } from "./RoleSelector";
import { InputBox } from "./InputBox";
import { InterviewBanner } from "./InterviewBanner";
import { GearIcon, WalletIcon } from "@/components/icons";

const PHASE_ACCENT: Record<string, string> = {
  brainstorm: "bg-amber-500",
  coach: "bg-amber-500",
  interview: "bg-red-500",
};

export function ChatPanel() {
  const phase = useSessionStore((s) => s.phase);
  const error = useSessionStore((s) => s.error);
  const clearError = useSessionStore((s) => s.clearError);
  const connectionStatus = useSessionStore((s) => s.connectionStatus);
  const messages = useSessionStore((s) => s.messages);
  const sessionId = useSessionStore((s) => s.sessionId);
  const isStreaming = useSessionStore((s) => s.isStreaming);
  const streamingRole = useSessionStore((s) => s.streamingRole);
  const discussionMap = useSessionStore((s) => s.discussionMap);
  const isGeneratingPortrait = useSessionStore((s) => s.isGeneratingPortrait);
  const generateProductPortrait = useSessionStore((s) => s.generateProductPortrait);
  const setSettingsOpen = useSessionStore((s) => s.setSettingsOpen);
  const setRechargeOpen = useSessionStore((s) => s.setRechargeOpen);

  const accentColor = PHASE_ACCENT[phase] || PHASE_ACCENT.brainstorm;
  const streamingRoleColor = streamingRole ? ROLE_MAP[streamingRole]?.color : null;
  const canGeneratePortrait = messages.length >= 6 && phase === "brainstorm";

  return (
    <div className="flex flex-col h-full">
      <div className="h-11 bg-white border-b border-warm-200 flex items-center px-4 shrink-0 relative">
        <div className={`absolute left-0 top-0 bottom-0 w-[3px] ${accentColor}`} />
        <span className="text-sm font-semibold text-warm-600 pl-1">
          {phase === "interview" ? "AI 面试官" : phase === "coach" ? "产品教练 · 思路梳理" : "产品脑暴群聊"}
        </span>
        <span className="ml-auto bg-warm-100 text-warm-500 text-[10px] rounded-full px-2 py-0.5">
          {messages.length} 条消息
        </span>
        <button
          onClick={generateProductPortrait}
          disabled={!canGeneratePortrait || isGeneratingPortrait}
          aria-label="生成产品画像"
          className={`ml-2 text-xs px-2 py-1 rounded-md border transition-all duration-200 min-h-[32px] flex items-center gap-1 focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none ${
            canGeneratePortrait && !isGeneratingPortrait
              ? "bg-amber-50 text-amber-600 border-amber-300 hover:bg-amber-100 active:bg-amber-200"
              : "bg-warm-50 text-warm-300 border-warm-200 cursor-not-allowed"
          }`}
        >
          {isGeneratingPortrait ? (
            <svg className="animate-spin h-3 w-3" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
          ) : (
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
              <line x1="3" y1="9" x2="21" y2="9"/>
              <line x1="9" y1="21" x2="9" y2="9"/>
            </svg>
          )}
          画像
        </button>
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
          aria-label="导出会话"
          className="text-warm-400 hover:text-warm-600 active:text-warm-700 transition-all duration-200 min-h-[32px] min-w-[32px] flex items-center justify-center focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none rounded"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
            <polyline points="7 10 12 15 17 10"/>
            <line x1="12" y1="15" x2="12" y2="3"/>
          </svg>
        </button>
        <button
          onClick={() => setRechargeOpen(true)}
          aria-label="充值"
          className="text-warm-400 hover:text-amber-600 active:text-amber-700 transition-all duration-200 min-h-[32px] min-w-[32px] flex items-center justify-center focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none rounded"
        >
          <WalletIcon size={14} />
        </button>
        <button
          onClick={() => setSettingsOpen(true)}
          aria-label="设置"
          className="text-warm-400 hover:text-warm-600 active:text-warm-700 transition-all duration-200 min-h-[32px] min-w-[32px] flex items-center justify-center focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none rounded"
        >
          <GearIcon size={14} />
        </button>
      </div>

      {isStreaming && streamingRoleColor && (
        <div
          className="role-glow-bar w-full"
          style={{ background: `linear-gradient(to right, ${streamingRoleColor}, transparent)` }}
        />
      )}

      {error && (
        <div role="alert" className="mx-3 mt-2 px-3 py-2 bg-red-50 border border-red-200 rounded-xl text-red-600 text-sm flex justify-between items-center">
          <span>{error}</span>
          <button onClick={clearError} aria-label="关闭错误提示" className="text-red-400 hover:text-red-500 active:text-red-600 ml-2 shrink-0 min-h-[44px] min-w-[44px] flex items-center justify-center transition-all duration-200 focus-visible:ring-2 focus-visible:ring-red-500/50 focus-visible:outline-none rounded">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 6L6 18M6 6l12 12"/></svg>
          </button>
        </div>
      )}

      {connectionStatus === "reconnecting" && (
        <div className="mx-3 mt-2 px-3 py-2 bg-amber-50 border border-amber-200 rounded-xl text-amber-600 text-sm flex items-center gap-2">
          <span className="w-1.5 h-1.5 bg-amber-500 rounded-full animate-pulse" />
          重新连接中...
        </div>
      )}
      {connectionStatus === "disconnected" && (
        <div className="mx-3 mt-2 px-3 py-2 bg-red-50 border border-red-200 rounded-xl text-red-600 text-sm">
          连接已断开，请检查网络
        </div>
      )}

      {phase === "interview" && <InterviewBanner />}

      <div className="flex-1 overflow-y-auto px-2 py-3 chat-area-bg chat-noise relative">
        <MessageList />
      </div>

      {phase === "brainstorm" && messages.length > 0 && <RoleSelector />}

      <InputBox />
    </div>
  );
}
