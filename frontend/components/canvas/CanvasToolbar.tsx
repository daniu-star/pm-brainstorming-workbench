"use client";

import { useSessionStore } from "@/store/sessionStore";

export function CanvasToolbar() {
  const { canvasTree, generateCanvas, isStreaming, isGeneratingCanvas } = useSessionStore();

  return (
    <div className="h-10 bg-dark-800/90 backdrop-blur border-b border-zinc-800/50 flex items-center justify-between px-4 shrink-0">
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-zinc-300">实时画布</span>
          {isStreaming && (
            <span className="flex items-center gap-1 text-[10px] text-brand-300">
              <span className="w-1.5 h-1.5 bg-brand-500 rounded-full animate-pulse" />
              分析中
            </span>
          )}
        </div>
        {canvasTree?.root && (
          <>
            <span className="text-zinc-500">|</span>
            <span className="text-xs text-zinc-500 truncate max-w-[300px]">
              {canvasTree.root}
            </span>
          </>
        )}
      </div>
      <div className="flex items-center gap-2">
        {canvasTree?.branches && (
          <span className="text-[10px] text-zinc-500">
            {canvasTree.branches.length} 分支 · {canvasTree.branches.reduce((s, b) => s + (b.children?.length || 0), 0)} 节点
          </span>
        )}
        <button
          onClick={() => void generateCanvas()}
          disabled={isStreaming || isGeneratingCanvas}
          className="flex items-center gap-1.5 text-xs px-2.5 py-1 min-h-[44px] min-w-[44px] bg-brand-600/20 text-brand-300 border border-brand-500/30 rounded-md hover:bg-brand-600/30 transition-colors duration-200 disabled:opacity-50 disabled:cursor-not-allowed"
          aria-label="重新生成画布"
        >
          {isGeneratingCanvas ? (
            <>
              <span className="inline-block size-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
              重新生成中
            </>
          ) : (
            "重新生成"
          )}
        </button>
      </div>
    </div>
  );
}
