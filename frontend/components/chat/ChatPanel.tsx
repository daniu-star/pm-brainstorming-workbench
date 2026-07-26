"use client";

import { useSessionStore } from "@/store/sessionStore";
import { ROLE_MAP } from "@/lib/types";
import { exportSessionAsMarkdown } from "@/lib/export";
import { MessageList } from "./MessageList";
import { RoleSelector } from "./RoleSelector";
import { InputBox } from "./InputBox";
import { InterviewBanner } from "./InterviewBanner";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { Settings, Wallet, Download, Image as ImageIcon, Loader2 } from "lucide-react";

const PHASE_ACCENT: Record<string, string> = {
  brainstorm: "bg-amber-500",
  coach: "bg-amber-500",
  interview: "bg-destructive",
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
      <div className="h-14 bg-background/75 backdrop-blur-xl border-b border-border flex items-center px-4 shrink-0 relative">
        <div className={cn("absolute left-0 top-0 bottom-0 w-[3px]", accentColor)} />
        <span className="text-sm font-semibold text-foreground pl-1">
          {phase === "interview" ? "AI 面试官" : phase === "coach" ? "产品教练 · 思路梳理" : "产品脑暴群聊"}
        </span>
        <Badge variant="secondary" className="ml-auto">
          {messages.length} 条消息
        </Badge>
        <Button
          onClick={generateProductPortrait}
          disabled={!canGeneratePortrait || isGeneratingPortrait}
          aria-label="生成产品画像"
          variant="ghost"
          size="sm"
          className={cn(
            "ml-2 text-xs h-8",
            canGeneratePortrait && !isGeneratingPortrait
              ? "text-primary hover:bg-primary/10"
              : "text-muted-foreground"
          )}
        >
          {isGeneratingPortrait ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <ImageIcon className="h-3 w-3" />
          )}
          画像
        </Button>
        <Button
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
          variant="ghost"
          size="icon"
          className="text-muted-foreground hover:text-foreground"
        >
          <Download className="h-3.5 w-3.5" />
        </Button>
        <Button
          onClick={() => setRechargeOpen(true)}
          aria-label="充值"
          variant="ghost"
          size="icon"
          className="text-muted-foreground hover:text-primary"
        >
          <Wallet className="h-3.5 w-3.5" />
        </Button>
        <Button
          onClick={() => setSettingsOpen(true)}
          aria-label="设置"
          variant="ghost"
          size="icon"
          className="text-muted-foreground hover:text-foreground"
        >
          <Settings className="h-3.5 w-3.5" />
        </Button>
      </div>

      {isStreaming && streamingRoleColor && (
        <div
          className="role-glow-bar w-full"
          style={{ background: `linear-gradient(to right, ${streamingRoleColor}, transparent)` }}
        />
      )}

      {error && (
        <div
          role="alert"
          className="mx-3 mt-2 px-3 py-2 bg-destructive/10 border border-destructive/20 rounded-xl text-destructive text-sm flex justify-between items-center"
        >
          <span>{error}</span>
          <Button
            onClick={clearError}
            aria-label="关闭错误提示"
            variant="ghost"
            size="icon"
            className="text-destructive/60 hover:text-destructive ml-2 shrink-0"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </Button>
        </div>
      )}

      {connectionStatus === "reconnecting" && (
        <div className="mx-3 mt-2">
          <Badge
            variant="secondary"
            className="px-3 py-2 rounded-xl text-sm flex items-center gap-2 w-full justify-start"
          >
            <span className="w-1.5 h-1.5 bg-muted-foreground rounded-full animate-pulse" />
            重新连接中...
          </Badge>
        </div>
      )}
      {connectionStatus === "disconnected" && (
        <div className="mx-3 mt-2">
          <Badge
            variant="destructive"
            className="px-3 py-2 rounded-xl text-sm w-full justify-start flex"
          >
            连接已断开，请检查网络
          </Badge>
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
