"use client";

import { InterviewIcon } from "@/components/icons";

export function InterviewBanner() {
  return (
    <div className="mx-4 my-2 px-4 py-2.5 rounded-xl bg-red-50 border border-red-200">
      <div className="flex items-center gap-2">
        <span className="w-1.5 h-1.5 bg-red-500 rounded-full animate-pulse shrink-0" />
        <InterviewIcon size={14} className="text-red-400" />
        <span className="text-red-400 text-sm font-medium">AI 面试官正在对你的方案进行压力测试</span>
      </div>
    </div>
  );
}
