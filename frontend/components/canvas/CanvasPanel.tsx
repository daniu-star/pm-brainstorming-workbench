"use client";

import { useState } from "react";
import { Brain, Loader2 } from "lucide-react";
import { useSessionStore } from "@/store/sessionStore";
import { CanvasToolbar } from "./CanvasToolbar";
import { TimelineView } from "./TimelineView";
import { ProductPortrait } from "./ProductPortrait";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

type CanvasTab = "map" | "portrait";

export function CanvasPanel() {
  const discussionMap = useSessionStore((s) => s.discussionMap);
  const messages = useSessionStore((s) => s.messages);
  const isStreaming = useSessionStore((s) => s.isStreaming);
  const productPortrait = useSessionStore((s) => s.productPortrait);
  const isGeneratingPortrait = useSessionStore((s) => s.isGeneratingPortrait);
  const canvasStatus = useSessionStore((s) => s.canvasStatus);
  const [activeTab, setActiveTab] = useState<CanvasTab>("map");

  const isEmpty = !discussionMap || !discussionMap.timeline?.length;
  const showTabs = productPortrait || (!isEmpty);

  return (
    <div className="workbench-canvas-panel flex-1 flex flex-col">
      <CanvasToolbar />
      {showTabs ? (
        <Tabs
          value={activeTab}
          onValueChange={(v) => setActiveTab(v as CanvasTab)}
          className="flex-1 flex flex-col"
        >
          <TabsList className="grid w-full grid-cols-2 rounded-none border-b border-border bg-muted/50 h-auto py-1">
            <TabsTrigger value="map" className="text-xs gap-2">
              决策图谱
              <Badge
                variant={canvasStatus === "error" ? "destructive" : "secondary"}
                className="h-5 px-1.5 text-xs"
              >
                {canvasStatus === "syncing"
                  ? "同步中"
                  : canvasStatus === "ready"
                    ? "已同步"
                    : canvasStatus === "stale"
                      ? "待更新"
                      : canvasStatus === "error"
                        ? "同步失败"
                        : "待生成"}
              </Badge>
            </TabsTrigger>
            <TabsTrigger value="portrait" className="text-xs">
              产品画像
              {isGeneratingPortrait && (
                <Loader2 className="animate-spin h-3 w-3 ml-1" />
              )}
            </TabsTrigger>
          </TabsList>

          <TabsContent value="map" className="flex-1 flex flex-col mt-0">
            {isEmpty ? (
              <MapEmptyState
                isStreaming={isStreaming}
                hasMessages={messages.length > 0}
              />
            ) : (
              <TimelineView map={discussionMap} />
            )}
          </TabsContent>

          <TabsContent value="portrait" className="flex-1 flex flex-col mt-0">
            {productPortrait ? (
              <div className="flex-1 overflow-y-auto bg-muted/50">
                <ProductPortrait portrait={productPortrait} />
              </div>
            ) : isGeneratingPortrait ? (
              <div className="flex-1 flex items-center justify-center bg-muted/50">
                <Card className="text-center px-8 py-6 max-w-sm border-border">
                  <div className="mb-4 flex justify-center">
                    <Brain size={40} className="text-primary animate-pulse" />
                  </div>
                  <p className="text-foreground text-sm font-medium mb-1">
                    正在生成产品画像...
                  </p>
                  <p className="text-muted-foreground text-xs">
                    分析讨论内容，提炼产品核心特征
                  </p>
                </Card>
              </div>
            ) : (
              <div className="flex-1 flex items-center justify-center bg-muted/50">
                <Card className="text-center px-8 py-6 max-w-sm border-border">
                  <div className="mb-4 flex justify-center">
                    <Brain size={40} className="text-primary/40" />
                  </div>
                  <p className="text-foreground text-sm font-medium mb-1">
                    暂无产品画像
                  </p>
                  <p className="text-muted-foreground text-xs">
                    在聊天面板点击「画像」按钮生成
                  </p>
                </Card>
              </div>
            )}
          </TabsContent>
        </Tabs>
      ) : (
        <MapEmptyState
          isStreaming={isStreaming}
          hasMessages={messages.length > 0}
        />
      )}
    </div>
  );
}

function MapEmptyState({
  isStreaming,
  hasMessages,
}: {
  isStreaming: boolean;
  hasMessages: boolean;
}) {
  return (
    <div className="flex-1 flex items-center justify-center relative overflow-hidden">
      <div className="canvas-empty-glow absolute inset-0" />
      <div className="text-center px-8 max-w-sm relative z-10">
        <div className="mb-4 flex justify-center relative">
          <div className="canvas-orbit absolute inset-0 flex items-center justify-center">
            <div className="w-20 h-20 rounded-full border border-primary/20" />
          </div>
          <div
            className={`transition-all duration-500 ${
              isStreaming ? "scale-110 animate-pulse" : ""
            }`}
          >
            <Brain
              size={48}
              className={isStreaming ? "text-primary" : "text-primary/60"}
            />
          </div>
        </div>
        {isStreaming ? (
          <>
            <p className="text-foreground text-base font-medium mb-2">
              正在分析对话...
            </p>
            <p className="text-muted-foreground text-sm mb-4">
              提取共识、分歧和阶段性成果
            </p>
            <div className="flex items-center justify-center gap-1.5">
              <span
                className="w-2 h-2 bg-primary rounded-full animate-bounce"
                style={{ animationDelay: "0ms" }}
              />
              <span
                className="w-2 h-2 bg-primary rounded-full animate-bounce"
                style={{ animationDelay: "150ms" }}
              />
              <span
                className="w-2 h-2 bg-primary rounded-full animate-bounce"
                style={{ animationDelay: "300ms" }}
              />
            </div>
          </>
        ) : hasMessages ? (
          <>
            <p className="text-foreground text-base font-medium mb-2">
              决策图谱
            </p>
            <p className="text-muted-foreground text-sm">
              每次讨论结束会自动更新，也可手动刷新
            </p>
          </>
        ) : (
          <>
            <p className="text-foreground text-lg font-medium mb-2">
              决策图谱
            </p>
            <p className="text-muted-foreground text-sm leading-relaxed">
              开始对话后，将自动提取共识、分歧和阶段性成果
            </p>
          </>
        )}
      </div>
    </div>
  );
}
