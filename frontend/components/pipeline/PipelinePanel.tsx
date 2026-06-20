"use client";

import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  FileText,
  Brain,
  GraduationCap,
  Code,
  Palette,
  TrendingUp,
  User,
  Layers,
  Image,
  CheckCircle2,
  Loader2,
  AlertCircle,
  Check,
  RefreshCw,
  Play,
} from "lucide-react";
import { useSessionStore } from "@/store/sessionStore";
import {
  PIPELINE_NODE_LABELS,
  PIPELINE_NODE_ORDER,
  type PipelineNodeName,
  type PipelineNodeState,
} from "@/lib/types";
import { PrdViewer } from "./PrdViewer";

// 节点图标映射
const NODE_ICONS: Record<PipelineNodeName, typeof FileText> = {
  pm_prd: FileText,
  cot: Brain,
  coach: GraduationCap,
  cto: Code,
  designer: Palette,
  ops: TrendingUp,
  user_feedback: User,
  canvas_synthesis: Layers,
  portrait: Image,
  pm_acceptance: CheckCircle2,
};

function formatDuration(startedAt?: number, completedAt?: number): string {
  if (!startedAt || !completedAt) return "";
  const seconds = Math.round((completedAt - startedAt) / 1000);
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

function NodeStatusBadge({ status }: { status: PipelineNodeState["status"] }) {
  switch (status) {
    case "running":
      return (
        <Badge className="bg-primary text-primary-foreground">
          <Loader2 className="h-3 w-3 mr-1 animate-spin" />
          运行中
        </Badge>
      );
    case "completed":
      return (
        <Badge className="bg-green-500 text-white hover:bg-green-500">
          <Check className="h-3 w-3 mr-1" />
          完成
        </Badge>
      );
    case "error":
      return (
        <Badge variant="destructive">
          <AlertCircle className="h-3 w-3 mr-1" />
          错误
        </Badge>
      );
    default:
      return (
        <Badge variant="secondary" className="bg-muted text-muted-foreground">
          等待
        </Badge>
      );
  }
}

function PipelineNodeRow({ node }: { node: PipelineNodeState }) {
  const [expanded, setExpanded] = useState(false);
  const Icon = NODE_ICONS[node.name] || FileText;
  const label = PIPELINE_NODE_LABELS[node.name] || node.name;
  const duration = formatDuration(node.startedAt, node.completedAt);
  const hasOutput = node.output && node.output.trim().length > 0;

  return (
    <div
      className="flex items-start gap-3 p-3 rounded-lg border border-border hover:bg-accent/50 transition-colors cursor-pointer"
      onClick={() => hasOutput && setExpanded(!expanded)}
    >
      <div className="mt-0.5">
        <Icon className="h-4 w-4 text-muted-foreground" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center justify-between gap-2">
          <span className="text-sm font-medium">{label}</span>
          <NodeStatusBadge status={node.status} />
        </div>
        {duration && (
          <span className="text-xs text-muted-foreground mt-0.5 block">
            耗时 {duration}
            {node.tokens ? ` · ${node.tokens} tokens` : ""}
          </span>
        )}
        {hasOutput && expanded && (
          <div className="mt-2 p-2 rounded bg-muted/50 text-xs text-muted-foreground max-h-48 overflow-y-auto whitespace-pre-wrap">
            {node.output}
          </div>
        )}
      </div>
    </div>
  );
}

export function PipelinePanel() {
  const {
    pipelineNodes,
    isPipelineRunning,
    pipelineResult,
    pipelineRevisionCount,
    runPipeline,
    clearPipeline,
    sessionId,
  } = useSessionStore();

  const [prdViewerOpen, setPrdViewerOpen] = useState(false);

  const canStart = sessionId && !isPipelineRunning;
  const acceptanceResult = pipelineResult?.acceptanceResult;
  const hasNodes = pipelineNodes.length > 0;

  return (
    <Card className="flex flex-col h-full">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="flex items-center gap-2 text-base">
            <Brain className="h-4 w-4" />
            Pipeline 执行
            {isPipelineRunning && (
              <Badge className="bg-primary text-primary-foreground">
                <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                运行中
              </Badge>
            )}
            {pipelineResult && !isPipelineRunning && (
              <Badge variant="secondary">已完成</Badge>
            )}
          </CardTitle>
          <div className="flex gap-2">
            {pipelineResult?.prd && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPrdViewerOpen(true)}
              >
                <FileText className="h-4 w-4 mr-1" />
                查看 PRD
              </Button>
            )}
            {canStart ? (
              <Button size="sm" onClick={runPipeline}>
                <Play className="h-4 w-4 mr-1" />
                启动 Pipeline
              </Button>
            ) : isPipelineRunning ? (
              <Button variant="outline" size="sm" onClick={clearPipeline}>
                停止
              </Button>
            ) : null}
          </div>
        </div>
      </CardHeader>

      <CardContent className="flex-1 flex flex-col gap-3 overflow-hidden">
        {pipelineRevisionCount > 0 && (
          <div className="flex items-center gap-2 p-2 rounded-md bg-amber-500/10 border border-amber-500/30">
            <RefreshCw className="h-4 w-4 text-amber-600" />
            <span className="text-sm text-amber-700 dark:text-amber-400">
              第 {pipelineRevisionCount} 次修订循环（PM 验收未通过，正在重新推理）
            </span>
          </div>
        )}

        {acceptanceResult && !isPipelineRunning && (
          <div
            className={`p-3 rounded-md border ${
              acceptanceResult.passed
                ? "bg-green-500/10 border-green-500/30"
                : "bg-destructive/10 border-destructive/30"
            }`}
          >
            <div className="flex items-center gap-2 mb-1">
              {acceptanceResult.passed ? (
                <CheckCircle2 className="h-4 w-4 text-green-600" />
              ) : (
                <AlertCircle className="h-4 w-4 text-destructive" />
              )}
              <span className="text-sm font-medium">
                {acceptanceResult.passed ? "PM 验收通过" : "PM 验收未通过"}
              </span>
            </div>
            {acceptanceResult.summary && (
              <p className="text-xs text-muted-foreground">{acceptanceResult.summary}</p>
            )}
            {!acceptanceResult.passed && acceptanceResult.gaps.length > 0 && (
              <ul className="mt-2 space-y-1">
                {acceptanceResult.gaps.map((gap, idx) => (
                  <li key={idx} className="text-xs text-muted-foreground">
                    • {gap}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}

        {hasNodes ? (
          <ScrollArea className="flex-1">
            <div className="space-y-2 pr-2">
              {pipelineNodes.map((node) => (
                <PipelineNodeRow key={node.name} node={node} />
              ))}
            </div>
          </ScrollArea>
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center text-muted-foreground">
            <Brain className="h-12 w-12 mb-3 opacity-30" />
            <p className="text-sm font-medium">Pipeline 尚未启动</p>
            <p className="text-xs mt-1 text-center max-w-xs">
              点击"启动 Pipeline"按钮，将依次执行 PM 写 PRD → CoT 分析 →
              教练/CTO/设计师/运营/用户 → 画布综合 → 产品画像 → PM 验收
            </p>
          </div>
        )}
      </CardContent>

      <PrdViewer
        open={prdViewerOpen}
        onOpenChange={setPrdViewerOpen}
        prd={pipelineResult?.prd || ""}
        acceptanceResult={acceptanceResult}
      />
    </Card>
  );
}
