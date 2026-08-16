"use client";

import { useSessionStore } from "@/store/sessionStore";
import { CanvasToolbar } from "./CanvasToolbar";
import { PipelineView } from "./PipelineView";
import { BrainIcon } from "@/components/icons";

export function CanvasPanel() {
  const { canvasTree, messages, isStreaming, isGeneratingCanvas, generateCanvas } = useSessionStore();
  const isEmpty = !canvasTree || !canvasTree.branches?.length;

  if (isEmpty) {
    return (
      <div className="flex-1 flex flex-col min-h-0">
        <CanvasToolbar />
        <div className="flex-1 flex items-center justify-center">
          <div className="text-center px-8 max-w-sm">
            <div className="mb-4 flex justify-center">
              <div className={`transition-all duration-500 ${isStreaming ? "scale-110" : ""}`}>
                <BrainIcon size={48} className={isStreaming ? "text-brand-400" : "text-zinc-500"} />
              </div>
            </div>
            {isStreaming ? (
              <>
                <p className="text-zinc-300 text-base font-medium mb-2">正在分析对话内容...</p>
                <p className="text-zinc-500 text-sm mb-4">
                  专家们的观点正在被实时整理为结构化功能树
                </p>
                <div className="flex items-center justify-center gap-1.5">
                  <span className="w-2 h-2 bg-brand-500 rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
                  <span className="w-2 h-2 bg-brand-500 rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
                  <span className="w-2 h-2 bg-brand-500 rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
                </div>
              </>
            ) : messages.length > 0 ? (
              <>
                <p className="text-zinc-400 text-base font-medium mb-2">讨论进行中。</p>
                <p className="text-zinc-500 text-sm mb-5">
                  点击下方按钮，基于当前讨论生成画布
                </p>
                <button
                  onClick={() => void generateCanvas()}
                  disabled={isGeneratingCanvas}
                  className="inline-flex min-h-[44px] items-center gap-2 rounded-lg border border-brand-500/40 bg-brand-600/20 px-4 py-2 text-xs font-medium text-brand-300 transition-colors hover:bg-brand-600/30 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {isGeneratingCanvas ? (
                    <>
                      <span className="inline-block size-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />
                      生成中...
                    </>
                  ) : (
                    <>
                      <BrainIcon size={14} />
                      生成画布
                    </>
                  )}
                </button>
              </>
            ) : (
              <>
                <p className="text-zinc-500 text-lg mb-2">实时画布</p>
                <p className="text-zinc-500 text-sm leading-relaxed">
                  开始对话后，画布将自动分析对话内容<br />
                  构建可视化功能树，标注核心观点与风险
                </p>
              </>
            )}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <CanvasToolbar />
      <PipelineView tree={canvasTree} />
    </div>
  );
}
