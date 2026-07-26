"use client";

import { useState, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { useSessionStore } from "@/store/sessionStore";
import { useSpeechRecognition } from "@/hooks/useSpeechRecognition";
import { ROLES, Attachment } from "@/lib/types";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { Send, Mic, Square, Loader2, Brain, ArrowRight, Target, CheckCircle, Paperclip, X, File as FileIcon } from "lucide-react";
import { PrdViewer } from "@/components/pipeline/PrdViewer";
import { apiUrl } from "@/lib/api";
import { getUserHeaders } from "@/lib/user";
import { toast } from "@/components/Toast";

export function InputBox() {
  const router = useRouter();
  const [input, setInput] = useState("");
  const [showPrd, setShowPrd] = useState(false);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const sendMessage = useSessionStore((s) => s.sendMessage);
  const sendToCoach = useSessionStore((s) => s.sendToCoach);
  const answerInterview = useSessionStore((s) => s.answerInterview);
  const phase = useSessionStore((s) => s.phase);
  const isStreaming = useSessionStore((s) => s.isStreaming);
  const generateCanvas = useSessionStore((s) => s.generateCanvas);
  const startInterview = useSessionStore((s) => s.startInterview);
  const sessionId = useSessionStore((s) => s.sessionId);
  const targetRole = useSessionStore((s) => s.targetRole);
  const setTargetRole = useSessionStore((s) => s.setTargetRole);
  const runPipeline = useSessionStore((s) => s.runPipeline);
  const isPipelineRunning = useSessionStore((s) => s.isPipelineRunning);
  const pipelineResult = useSessionStore((s) => s.pipelineResult);

  const handleFileSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files || files.length === 0) return;

    if (attachments.length + files.length > 10) {
      toast("error", "最多上传 10 个附件");
      return;
    }

    setUploading(true);
    try {
      for (const file of Array.from(files)) {
        const formData = new FormData();
        formData.append("file", file);
        formData.append("session_id", sessionId || "");

        const res = await fetch(apiUrl("/api/attachments/upload"), {
          method: "POST",
          body: formData,
          headers: getUserHeaders(),
        });

        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.detail || "上传失败");
        }

        const data = await res.json();
        setAttachments((prev) => [...prev, data as Attachment]);
      }
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "附件上传失败");
    } finally {
      setUploading(false);
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    }
  };

  const handleRemoveAttachment = async (attachmentId: string) => {
    try {
      await fetch(apiUrl(`/api/attachments/${attachmentId}`), {
        method: "DELETE",
        headers: getUserHeaders(),
      });
      setAttachments((prev) => prev.filter((a) => a.id !== attachmentId));
    } catch {
      toast("error", "删除附件失败");
    }
  };

  const handleSend = () => {
    if ((!input.trim() && attachments.length === 0) || isStreaming) return;

    let messageContent = input.trim();
    if (attachments.length > 0) {
      const fileList = attachments.map((a) => a.filename).join(", ");
      messageContent = messageContent
        ? `${messageContent}\n\n[附件: ${fileList}]`
        : `[附件: ${fileList}]`;
    }

    if (phase === "interview") {
      answerInterview(messageContent);
    } else if (phase === "coach") {
      sendToCoach(messageContent);
    } else {
      sendMessage(messageContent, targetRole);
      setTargetRole("all");
    }
    setInput("");
    setAttachments([]);
  };

  const handleEndBrainstorm = () => {
    if (!sessionId) return;
    runPipeline();
  };

  const { isRecording, isTranscribing, transcript, errorMessage, start, stop, reset, status } = useSpeechRecognition();

  useEffect(() => {
    if (transcript) {
      setInput((prev) => prev + transcript);
      reset();
    }
  }, [transcript, reset]);

  useEffect(() => {
    if (pipelineResult?.prd && !isPipelineRunning) {
      setShowPrd(true);
    }
  }, [pipelineResult, isPipelineRunning]);

  return (
    <div className="px-4 py-3 border-t border-border bg-background/95 backdrop-blur-sm">
      <div className="flex items-center gap-3 mb-2">
        {phase === "coach" && (
          <>
            <span className="text-xs text-primary flex items-center gap-1">
              <span className="w-1.5 h-1.5 bg-primary rounded-full animate-pulse" />
              产品教练正在帮你理清思路
            </span>
            <Button
              onClick={() => {
                if (!isStreaming) {
                  useSessionStore.getState().setPhase("brainstorm");
                }
              }}
              disabled={isStreaming}
              aria-label="跳过引导直接脑暴"
              variant="ghost"
              size="sm"
              className="text-xs text-primary hover:text-primary/80 ml-auto"
            >
              跳过引导，直接脑暴 <ArrowRight size={14} />
            </Button>
          </>
        )}
        {phase === "brainstorm" && (
          <>
            <Button
              onClick={handleEndBrainstorm}
              disabled={isPipelineRunning}
              aria-label="结束脑暴并生成 PRD"
              variant="default"
              size="sm"
              className="text-xs"
            >
              {isPipelineRunning ? (
                <>
                  <Loader2 size={14} className="animate-spin" /> 生成PRD中...
                </>
              ) : (
                <>
                  <CheckCircle size={14} /> 结束脑暴
                </>
              )}
            </Button>
            <span className="text-muted-foreground/40">|</span>
            <Button
              onClick={generateCanvas}
              disabled={isStreaming}
              aria-label="生成讨论画布"
              variant="ghost"
              size="sm"
              className="text-xs text-muted-foreground hover:text-emerald-600"
            >
              <Brain size={14} /> 生成画布
            </Button>
            <span className="text-muted-foreground/40">|</span>
            <Button
              onClick={() => sessionId && router.push(`/session/${sessionId}/interview`)}
              disabled={isStreaming || !sessionId}
              aria-label="进入 AI 审计间"
              variant="ghost"
              size="sm"
              className="interview-entry-button text-xs"
            >
              <Target size={14} /> 进入 AI 审计间
            </Button>
          </>
        )}
        {phase === "interview" && (
          <span className="text-xs text-destructive flex items-center gap-1">
            <span className="w-1.5 h-1.5 bg-destructive rounded-full animate-pulse" />
            面试模式中 — 回答每个问题以继续
          </span>
        )}
      </div>

      {status === "recording" && (
        <div className="voice-transcript-bar mb-2 px-3 py-1.5 bg-destructive/10 border border-destructive/20 rounded-lg text-xs text-destructive flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-destructive animate-pulse" />
          <span>正在聆听...</span>
          <span className="flex items-center gap-0.5 ml-1">
            {Array.from({ length: 5 }).map((_, i) => (
              <span key={i} className="inline-block w-0.5 bg-destructive/60 rounded-full animate-pulse" style={{ height: `${8 + Math.random() * 8}px`, animationDelay: `${i * 0.15}s` }} />
            ))}
          </span>
        </div>
      )}
      {status === "transcribing" && (
        <div className="voice-transcript-bar mb-2 px-3 py-1.5 bg-primary/10 border border-primary/20 rounded-lg text-xs text-primary flex items-center gap-2">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          <span>正在识别...</span>
        </div>
      )}
      {status === "success" && (
        <div className="voice-transcript-bar mb-2 px-3 py-1.5 bg-emerald-50 border border-emerald-200 rounded-lg text-xs text-emerald-600 flex items-center gap-2">
          <svg className="h-3.5 w-3.5 text-emerald-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
          </svg>
          <span>识别成功</span>
        </div>
      )}
      {status === "error" && (
        <div className="voice-transcript-bar mb-2 px-3 py-1.5 bg-destructive/10 border border-destructive/20 rounded-lg text-xs text-destructive flex items-center gap-2">
          <svg className="h-3.5 w-3.5 text-destructive" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          <span className="flex-1">{errorMessage}</span>
          <Button onClick={start} variant="outline" size="sm" className="h-6 px-2 text-xs">
            重试
          </Button>
        </div>
      )}

      {attachments.length > 0 && (
        <div className="flex flex-wrap gap-2 mb-2">
          {attachments.map((att) => (
            <div
              key={att.id}
              className="flex items-center gap-1 px-2 py-1 bg-muted rounded text-xs"
            >
              <FileIcon size={12} />
              <span className="max-w-32 truncate">{att.filename}</span>
              <button
                onClick={() => handleRemoveAttachment(att.id)}
                className="text-muted-foreground hover:text-destructive"
                aria-label={`删除附件 ${att.filename}`}
              >
                <X size={12} />
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="flex gap-2 items-end">
        <div className="flex-1 relative">
          <Textarea
            id="workbench-message"
            name="workbench-message"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={
              phase === "interview"
                ? "回答面试官的问题..."
                : phase === "coach"
                  ? "回答产品教练的问题，帮助理清你的想法..."
                  : targetRole === "all"
                    ? "插话、追问或提出你的想法..."
                    : `向 ${ROLES.find(r => r.id === targetRole)?.name || targetRole} 提问...`
            }
            rows={2}
            aria-label={
              phase === "interview"
                ? "回答面试官问题"
                : phase === "coach"
                  ? "回答产品教练的问题"
                  : "输入你的想法或追问"
            }
            className="rounded-2xl px-4 py-2.5 text-sm resize-none input-inner-shadow border-border bg-background"
            onKeyDown={(e) => {
              if (e.key === "Enter" && (!e.shiftKey || e.ctrlKey || e.metaKey)) {
                e.preventDefault();
                handleSend();
              }
            }}
          />
        </div>
        <div className="flex flex-col items-center shrink-0">
          <Button
            onClick={isRecording ? stop : start}
            disabled={isStreaming || isTranscribing}
            aria-label={isRecording ? "停止录音" : "语音输入"}
            title="点击语音输入"
            variant="outline"
            size="icon"
            className={cn(
              "rounded-full min-w-[44px] min-h-[44px]",
              status === "recording" && "mic-ripple bg-destructive hover:bg-destructive/90 text-destructive-foreground border-destructive shadow-md",
              status === "transcribing" && "bg-primary hover:bg-primary/90 text-primary-foreground border-primary animate-pulse shadow-md",
              status === "success" && "bg-emerald-500 hover:bg-emerald-500 text-white border-emerald-500 shadow-md"
            )}
          >
            {status === "recording" ? (
              <Square size={20} />
            ) : status === "transcribing" ? (
              <Loader2 size={20} className="animate-spin" />
            ) : (
              <Mic size={20} />
            )}
          </Button>
          {status === "recording" && (
            <Badge variant="destructive" className="text-[11px] mt-0.5 whitespace-nowrap">正在聆听...</Badge>
          )}
          {status === "transcribing" && (
            <Badge variant="secondary" className="text-[11px] mt-0.5 whitespace-nowrap">正在识别...</Badge>
          )}
          {status === "success" && (
            <Badge className="text-[11px] mt-0.5 whitespace-nowrap bg-emerald-500 text-white border-transparent">识别成功</Badge>
          )}
        </div>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={handleFileSelect}
          accept="image/*,.pdf,.txt,.md,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.json,.zip"
        />
        <Button
          onClick={() => fileInputRef.current?.click()}
          disabled={uploading || isStreaming}
          variant="ghost"
          size="sm"
          className="text-xs text-muted-foreground shrink-0"
          aria-label="上传附件"
        >
          {uploading ? <Loader2 size={16} className="animate-spin" /> : <Paperclip size={16} />}
        </Button>
        <Button
          onClick={handleSend}
          disabled={(!input.trim() && attachments.length === 0) || isStreaming}
          aria-label="发送消息"
          size="icon"
          className="shrink-0 rounded-full min-w-[44px] min-h-[44px]"
        >
          <Send size={18} />
        </Button>
      </div>

      {showPrd && pipelineResult?.prd && (
        <PrdViewer
          open={showPrd}
          onOpenChange={setShowPrd}
          prd={pipelineResult.prd}
          acceptanceResult={pipelineResult.acceptanceResult}
        />
      )}
    </div>
  );
}
