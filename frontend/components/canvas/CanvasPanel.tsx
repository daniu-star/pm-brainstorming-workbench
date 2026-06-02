"use client";

import { useState } from "react";
import { useSessionStore } from "@/store/sessionStore";
import { CanvasToolbar } from "./CanvasToolbar";
import { TimelineView } from "./TimelineView";
import { ProductPortrait } from "./ProductPortrait";
import { BrainIcon } from "@/components/icons";

type CanvasTab = "map" | "portrait";

export function CanvasPanel() {
  const discussionMap = useSessionStore((s) => s.discussionMap);
  const messages = useSessionStore((s) => s.messages);
  const isStreaming = useSessionStore((s) => s.isStreaming);
  const productPortrait = useSessionStore((s) => s.productPortrait);
  const isGeneratingPortrait = useSessionStore((s) => s.isGeneratingPortrait);
  const [activeTab, setActiveTab] = useState<CanvasTab>("map");

  const isEmpty = !discussionMap || !discussionMap.timeline?.length;
  const showTabs = productPortrait || (!isEmpty);

  return (
    <div className="flex-1 flex flex-col">
      <CanvasToolbar />
      {showTabs && (
        <div className="flex border-b border-warm-200 bg-warm-50/80 px-4">
          <button
            onClick={() => setActiveTab("map")}
            aria-label="讨论地图"
            className={`px-3 py-1.5 text-xs font-medium transition-all duration-200 border-b-2 min-h-[32px] focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none ${
              activeTab === "map"
                ? "text-amber-600 border-amber-500"
                : "text-warm-400 border-transparent hover:text-warm-600"
            }`}
          >
            讨论地图
          </button>
          <button
            onClick={() => setActiveTab("portrait")}
            aria-label="产品画像"
            className={`px-3 py-1.5 text-xs font-medium transition-all duration-200 border-b-2 min-h-[32px] focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none ${
              activeTab === "portrait"
                ? "text-amber-600 border-amber-500"
                : "text-warm-400 border-transparent hover:text-warm-600"
            }`}
          >
            产品画像
            {isGeneratingPortrait && (
              <svg className="animate-spin h-3 w-3 ml-1 inline" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
            )}
          </button>
        </div>
      )}

      {activeTab === "portrait" && productPortrait ? (
        <div className="flex-1 overflow-y-auto bg-warm-50">
          <ProductPortrait portrait={productPortrait} />
        </div>
      ) : activeTab === "portrait" && isGeneratingPortrait ? (
        <div className="flex-1 flex items-center justify-center bg-warm-50">
          <div className="text-center">
            <div className="mb-4 flex justify-center">
              <BrainIcon size={40} className="text-amber-500 animate-pulse" />
            </div>
            <p className="text-warm-600 text-sm font-medium mb-1">正在生成产品画像...</p>
            <p className="text-warm-400 text-xs">分析讨论内容，提炼产品核心特征</p>
          </div>
        </div>
      ) : activeTab === "portrait" && !productPortrait ? (
        <div className="flex-1 flex items-center justify-center bg-warm-50">
          <div className="text-center px-8 max-w-sm">
            <div className="mb-4 flex justify-center">
              <BrainIcon size={40} className="text-amber-500/40" />
            </div>
            <p className="text-warm-600 text-sm font-medium mb-1">暂无产品画像</p>
            <p className="text-warm-400 text-xs">在聊天面板点击「画像」按钮生成</p>
          </div>
        </div>
      ) : isEmpty ? (
        <div className="flex-1 flex items-center justify-center relative overflow-hidden">
          <div className="canvas-empty-glow absolute inset-0" />
          <div className="text-center px-8 max-w-sm relative z-10">
            <div className="mb-4 flex justify-center relative">
              <div className="canvas-orbit absolute inset-0 flex items-center justify-center">
                <div className="w-20 h-20 rounded-full border border-amber-500/20" />
              </div>
              <div
                className={`transition-all duration-500 ${
                  isStreaming ? "scale-125 animate-pulse" : ""
                }`}
              >
                <BrainIcon
                  size={48}
                  className={isStreaming ? "text-amber-600" : "text-amber-500/60"}
                />
              </div>
            </div>
            {isStreaming ? (
              <>
                <p className="text-warm-600 text-base font-medium mb-2">正在分析对话...</p>
                <p className="text-warm-500 text-sm mb-4">
                  提取共识、分歧和阶段性成果
                </p>
                <div className="flex items-center justify-center gap-1.5">
                  <span className="w-2 h-2 bg-amber-500 rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
                  <span className="w-2 h-2 bg-amber-500 rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
                  <span className="w-2 h-2 bg-amber-500 rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
                </div>
              </>
            ) : messages.length > 0 ? (
              <>
                <p className="text-warm-600 text-base font-medium mb-2">讨论地图</p>
                <p className="text-warm-500 text-sm">
                  每次讨论结束会自动更新，也可手动刷新
                </p>
              </>
            ) : (
              <>
                <p className="text-warm-600 text-lg font-medium mb-2">讨论地图</p>
                <p className="text-warm-500 text-sm leading-relaxed">
                  开始对话后，将自动提取共识、分歧和阶段性成果
                </p>
              </>
            )}
            <div className="flex justify-center gap-4 mt-6">
              <span className="canvas-particle w-1.5 h-1.5 bg-amber-400/50 rounded-full" style={{ animationDelay: "0s" }} />
              <span className="canvas-particle w-1 h-1 bg-orange-400/40 rounded-full" style={{ animationDelay: "1.5s" }} />
              <span className="canvas-particle w-1.5 h-1.5 bg-amber-400/30 rounded-full" style={{ animationDelay: "3s" }} />
              <span className="canvas-particle w-1 h-1 bg-orange-400/50 rounded-full" style={{ animationDelay: "4.5s" }} />
              <span className="canvas-particle w-1.5 h-1.5 bg-amber-400/40 rounded-full" style={{ animationDelay: "2s" }} />
            </div>
          </div>
        </div>
      ) : (
        <TimelineView map={discussionMap} />
      )}
    </div>
  );
}
