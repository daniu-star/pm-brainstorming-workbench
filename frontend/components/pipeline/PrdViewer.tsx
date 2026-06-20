"use client";

import { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { FileText, AlertCircle, CheckCircle2 } from "lucide-react";
import ReactMarkdown from "react-markdown";
import type { AcceptanceResult } from "@/lib/types";

interface PrdViewerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  prd: string;
  acceptanceResult?: AcceptanceResult | null;
}

export function PrdViewer({ open, onOpenChange, prd, acceptanceResult }: PrdViewerProps) {
  const passed = acceptanceResult?.passed ?? true;
  const gaps = acceptanceResult?.gaps ?? [];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl h-[85vh] flex flex-col">
        <DialogHeader>
          <div className="flex items-center justify-between">
            <DialogTitle className="flex items-center gap-2">
              <FileText className="h-5 w-5" />
              PRD 文档
            </DialogTitle>
            {acceptanceResult && (
              <Badge variant={passed ? "default" : "destructive"}>
                {passed ? (
                  <CheckCircle2 className="h-3 w-3 mr-1" />
                ) : (
                  <AlertCircle className="h-3 w-3 mr-1" />
                )}
                {passed ? "验收通过" : "验收未通过"}
              </Badge>
            )}
          </div>
        </DialogHeader>

        <ScrollArea className="flex-1">
          {prd ? (
            <div className="prose prose-sm dark:prose-invert max-w-none">
              <ReactMarkdown>{prd}</ReactMarkdown>
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
              <FileText className="h-12 w-12 mb-3 opacity-50" />
              <p className="text-sm">PRD 尚未生成</p>
              <p className="text-xs mt-1">启动 Pipeline 后，产品经理将撰写 PRD 文档</p>
            </div>
          )}
        </ScrollArea>

        {!passed && gaps.length > 0 && (
          <div className="mt-4 rounded-md border border-destructive/50 bg-destructive/10 p-4">
            <div className="flex items-center gap-2 mb-2">
              <AlertCircle className="h-4 w-4 text-destructive" />
              <span className="text-sm font-medium text-destructive">验收缺口</span>
            </div>
            <ul className="space-y-1">
              {gaps.map((gap, idx) => (
                <li key={idx} className="text-sm text-muted-foreground">
                  • {gap}
                </li>
              ))}
            </ul>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
