"use client";

import { SearchIcon } from "@/components/icons";

export function InterviewBanner() {
  return (
    <div className="mx-4 my-2 px-3 py-2 bg-red-900/20 border border-red-800/50 rounded-lg">
      <div className="flex items-center gap-2">
        <SearchIcon size={14} className="text-red-400" />
        <span className="text-red-400 text-sm font-medium">AI 面试官模式</span>
        <span className="text-zinc-400 text-xs">
          — AI 面试官正在对你的产品方案进行压力测试。回答每个问题以继续。
        </span>
      </div>
    </div>
  );
}
