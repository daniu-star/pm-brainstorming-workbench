"use client";

import { useRouter } from "next/navigation";
import { HistoryIcon, PlusIcon, UserIcon, VideoIcon } from "@/components/icons";
import { api } from "@/lib/api";
import { useSessionStore } from "@/store/sessionStore";
import { resetAuthVerification } from "@/components/auth/AuthGate";

interface NavButtonsProps {
  currentPage: "landing" | "workbench" | "interview";
  sessionId?: string | null;
  onToggleHistory: () => void;
}

export function NavButtons({ currentPage, sessionId, onToggleHistory }: NavButtonsProps) {
  const router = useRouter();
  const isStreaming = useSessionStore((s) => s.isStreaming);
  const hasSession = !!sessionId;

  const handleNewChat = () => {
    if (isStreaming) {
      const confirmed = window.confirm("当前回复尚未完成，离开将丢弃，确定吗？");
      if (!confirmed) return;
    }
    router.push("/");
  };

  const logout = async () => {
    try {
      await api("/api/auth/logout", { method: "POST" });
    } finally {
      resetAuthVerification();
      router.replace("/login");
      router.refresh();
    }
  };

  return (
    <div className="flex items-center gap-1.5">
      <button
        onClick={onToggleHistory}
        className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/60 rounded-lg transition-all duration-200 min-h-[44px] min-w-[44px] justify-center whitespace-nowrap"
        aria-label="历史会话"
      >
        <HistoryIcon size={16} />
        <span className="hidden sm:inline">历史会话</span>
      </button>

      <button
        onClick={handleNewChat}
        className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/60 rounded-lg transition-all duration-200 min-h-[44px] min-w-[44px] justify-center whitespace-nowrap"
        aria-label="添加新对话"
      >
        <PlusIcon size={16} />
        <span className="hidden sm:inline">添加新对话</span>
      </button>

      {currentPage !== "interview" && (
        <button
          onClick={() => hasSession && router.push(`/session/${sessionId}/interview`)}
          disabled={!hasSession}
          className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs rounded-lg transition-colors duration-150 min-h-[44px] min-w-[44px] justify-center whitespace-nowrap disabled:opacity-40 disabled:cursor-not-allowed text-cyan-300 hover:text-cyan-200 hover:bg-cyan-300/5 disabled:hover:bg-transparent"
          aria-label="进入 AI 审计专业通话"
          title={hasSession ? "进入 AI 审计专业通话" : "请先创建会话"}
        >
          <VideoIcon size={16} />
          <span className="hidden sm:inline">AI 审计通话</span>
        </button>
      )}

      <button
        type="button"
        onClick={() => void logout()}
        className="flex min-h-[44px] min-w-[44px] items-center justify-center gap-1.5 whitespace-nowrap rounded-lg px-2.5 py-1.5 text-xs text-zinc-500 transition-colors hover:bg-zinc-800/60 hover:text-zinc-200"
        aria-label="退出登录"
      >
        <UserIcon size={16} />
        <span className="hidden sm:inline">退出</span>
      </button>
    </div>
  );
}
