"use client";

import { useRouter } from "next/navigation";
import { HistoryIcon, PlusIcon, InterviewIcon } from "@/components/icons";

interface NavButtonsProps {
  currentPage: "landing" | "workbench" | "interview";
  sessionId?: string | null;
  onToggleHistory: () => void;
}

export function NavButtons({ currentPage, sessionId, onToggleHistory }: NavButtonsProps) {
  const router = useRouter();
  const hasSession = !!sessionId;
  const isLanding = currentPage === "landing";

  return (
    <div className="flex items-center gap-1.5">
      <button
        onClick={onToggleHistory}
        className="nav-frost-control flex min-h-[44px] min-w-[44px] items-center justify-center gap-1.5 whitespace-nowrap rounded-xl px-2.5 py-1.5 text-xs transition-all duration-200"
        aria-label="历史会话"
      >
        <HistoryIcon size={16} />
        <span className="hidden sm:inline">历史会话</span>
      </button>

      <button
        onClick={() => router.push("/")}
        className="nav-frost-control flex min-h-[44px] min-w-[44px] items-center justify-center gap-1.5 whitespace-nowrap rounded-xl px-2.5 py-1.5 text-xs transition-all duration-200"
        aria-label="添加新对话"
      >
        <PlusIcon size={16} />
        <span className="hidden sm:inline">添加新对话</span>
      </button>

      {currentPage !== "interview" && (
        <button
          onClick={() => hasSession && router.push(`/session/${sessionId}/interview`)}
          disabled={!hasSession}
          className="nav-frost-control nav-audit-control flex min-h-[44px] min-w-[44px] items-center justify-center gap-1.5 whitespace-nowrap rounded-xl px-2.5 py-1.5 text-xs transition-all duration-200 disabled:cursor-not-allowed disabled:opacity-40"
          aria-label="进入 AI 审计专业通话"
          title={hasSession ? "进入 AI 审计专业通话" : "请先创建会话"}
        >
          <InterviewIcon size={16} />
          <span className="hidden sm:inline">AI 审计通话</span>
        </button>
      )}
    </div>
  );
}
