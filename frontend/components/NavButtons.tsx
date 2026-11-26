"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { CodeIcon, HistoryIcon, LayersIcon, PlusIcon, ShieldIcon, UserIcon, VideoIcon } from "@/components/icons";
import { QuotaBadge } from "@/components/quota/QuotaBadge";
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
  const v2Phase = useSessionStore((s) => s.v2Phase);
  const hasSession = !!sessionId;
  // clarifying 阶段后端不允许 start_audit（state_machine.py:43），入口直接置灰；
  // auditing 仍可进入页面查看正在生成的报告，不能一起拦掉
  const auditBlocked = v2Phase === "clarifying";

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
          onClick={() => hasSession && !auditBlocked && router.push(`/session/${sessionId}/audit`)}
          disabled={!hasSession || auditBlocked}
          className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs rounded-lg transition-colors duration-150 min-h-[44px] min-w-[44px] justify-center whitespace-nowrap disabled:opacity-40 disabled:cursor-not-allowed text-cyan-300 hover:text-cyan-200 hover:bg-cyan-300/5 disabled:hover:bg-transparent"
          aria-label="进入专家审计"
          title={
            !hasSession
              ? "请先创建会话"
              : auditBlocked
                ? "请先完成澄清或跳过引导，再进入专家审计"
                : "进入专家审计"
          }
        >
          <VideoIcon size={16} />
          <span className="hidden sm:inline">专家审计</span>
        </button>
      )}

      <QuotaBadge />

      <Link
        href="/settings/llm"
        className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/60 rounded-lg transition-all duration-200 min-h-[44px] min-w-[44px] justify-center whitespace-nowrap"
        aria-label="模型 API 设置"
      >
        <CodeIcon size={16} />
        <span className="hidden sm:inline">模型 API</span>
      </Link>

      <Link
        href="/settings/team"
        className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/60 rounded-lg transition-all duration-200 min-h-[44px] min-w-[44px] justify-center whitespace-nowrap"
        aria-label="团队工作区"
      >
        <LayersIcon size={16} />
        <span className="hidden sm:inline">团队</span>
      </Link>

      <Link
        href="/settings/access"
        className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/60 rounded-lg transition-all duration-200 min-h-[44px] min-w-[44px] justify-center whitespace-nowrap"
        aria-label="权限总览"
      >
        <ShieldIcon size={16} />
        <span className="hidden sm:inline">权限</span>
      </Link>

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
