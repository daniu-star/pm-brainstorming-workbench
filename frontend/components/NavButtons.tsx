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
        className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs text-zinc-400 hover:text-zinc-200 hover:bg-zinc-800/60 rounded-lg transition-all duration-200 min-h-[44px] min-w-[44px] justify-center whitespace-nowrap"
        aria-label="历史会话"
      >
        <HistoryIcon size={16} />
        <span className="hidden sm:inline">历史会话</span>
      </button>

      <button
        onClick={() => router.push("/")}
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
          className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs rounded-lg transition-all duration-200 min-h-[44px] min-w-[44px] justify-center whitespace-nowrap disabled:opacity-40 disabled:cursor-not-allowed text-red-400 hover:text-red-300 hover:bg-red-900/20 disabled:hover:bg-transparent"
          aria-label="进入面试环节"
          title={hasSession ? "进入面试环节" : "请先创建会话"}
        >
          <InterviewIcon size={16} />
          <span className="hidden sm:inline">进入面试环节</span>
        </button>
      )}
    </div>
  );
}
