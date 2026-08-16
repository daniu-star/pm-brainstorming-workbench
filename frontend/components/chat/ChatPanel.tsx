"use client";

import { useSessionStore } from "@/store/sessionStore";
import { MessageList } from "./MessageList";
import { RoleSelector } from "./RoleSelector";
import { InputBox } from "./InputBox";
import { InterviewBanner } from "./InterviewBanner";

export function ChatPanel() {
  const {
    phase,
    error,
    clearError,
    messages,
    lastFailedSend,
    clearLastFailedSend,
    sendMessage,
    isStreaming,
  } = useSessionStore();

  const retrySend = () => {
    if (!lastFailedSend || isStreaming) return;
    const { content, targetRole } = lastFailedSend;
    clearLastFailedSend();
    sendMessage(content, targetRole);
  };

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* Chat header — group name */}
      <div className="h-11 bg-dark-800/60 border-b border-zinc-800/40 flex items-center px-4 shrink-0">
        <span className="text-sm font-medium text-zinc-300">
          {phase === "interview" ? "AI 面试官" : phase === "coach" ? "产品教练 · 思路梳理" : "产品脑暴群聊"}
        </span>
        <span className="ml-auto text-[10px] text-zinc-500">
          {messages.length} 条消息
        </span>
      </div>

      {error && (
        <div
          role="alert"
          className="mx-3 mt-2 px-3 py-2 bg-red-900/20 border border-red-800/40 rounded-xl text-red-400 text-sm flex justify-between items-center"
        >
          <span>{error}</span>
          <button
            onClick={clearError}
            aria-label="关闭错误提示"
            className="text-red-300 hover:text-red-200 ml-2 shrink-0 min-h-[44px] min-w-[44px] flex items-center justify-center"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 6L6 18M6 6l12 12"/></svg>
          </button>
        </div>
      )}

      {lastFailedSend && !isStreaming && (
        <div className="mx-3 mt-2 px-3 py-2 bg-amber-900/20 border border-amber-800/40 rounded-xl text-amber-300 text-sm flex justify-between items-center gap-2">
          <span className="truncate">
            发送失败{lastFailedSend.content ? `：${lastFailedSend.content.slice(0, 24)}${lastFailedSend.content.length > 24 ? "…" : ""}` : ""}
          </span>
          <span className="flex items-center gap-1 shrink-0">
            <button
              onClick={retrySend}
              className="min-h-[40px] px-3 text-xs font-medium text-amber-200 hover:text-amber-100 underline underline-offset-2"
            >
              重试
            </button>
            <button
              onClick={clearLastFailedSend}
              aria-label="放弃重试"
              className="min-h-[40px] min-w-[40px] flex items-center justify-center text-amber-300/70 hover:text-amber-200"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 6L6 18M6 6l12 12"/></svg>
            </button>
          </span>
        </div>
      )}

      {phase === "interview" && <InterviewBanner />}

      <MessageList />

      {phase === "brainstorm" && messages.length > 0 && <RoleSelector />}

      <InputBox />
    </div>
  );
}
