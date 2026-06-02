"use client";

import { useSessionStore } from "@/store/sessionStore";

export function CanvasToolbar() {
  const { discussionMap, generateCanvas, isStreaming } = useSessionStore();

  const timeline = discussionMap?.timeline || [];
  const consensusCount = timeline.filter((n) => n.type === "consensus").length;
  const disagreementCount = timeline.filter((n) => n.type === "disagreement").length;
  const summaryCount = timeline.filter((n) => n.type === "summary").length;

  return (
    <div className="h-10 bg-warm-50 backdrop-blur border-b border-warm-200 flex items-center justify-between px-4 shrink-0">
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-warm-600">讨论地图</span>
          {isStreaming && (
            <span className="flex items-center gap-1 text-[10px] text-amber-600">
              <span className="w-1.5 h-1.5 bg-amber-500 rounded-full animate-pulse" />
              分析中
            </span>
          )}
        </div>
        {discussionMap?.topic && (
          <>
            <span className="text-warm-300">|</span>
            <span className="text-xs text-warm-500 truncate max-w-[300px]">
              {discussionMap.topic}
            </span>
          </>
        )}
      </div>
      <div className="flex items-center gap-2">
        {timeline.length > 0 && (
          <span className="text-[10px] text-warm-500">
            {consensusCount} 共识 · {disagreementCount} 分歧 · {summaryCount} 总结
          </span>
        )}
        <button
          onClick={generateCanvas}
          disabled={isStreaming}
          aria-label="刷新讨论地图"
          className="text-xs px-2.5 py-1 min-h-[44px] min-w-[44px] bg-amber-600/20 text-amber-600 border border-amber-500/30 rounded-md hover:bg-amber-600/30 active:bg-amber-600/40 transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-1.5 focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none"
        >
          {isStreaming && (
            <svg className="animate-spin h-3.5 w-3.5" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
          )}
          刷新
        </button>
      </div>
    </div>
  );
}
