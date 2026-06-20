"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useSessionStore } from "@/store/sessionStore";
import { useSpeechRecognition } from "@/hooks/useSpeechRecognition";
import { ROLES } from "@/lib/types";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { Send, Mic, Square, Loader2, Brain, ArrowRight, Target } from "lucide-react";

export function InputBox() {
  const router = useRouter();
  const [input, setInput] = useState("");
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

  const handleSend = () => {
    if (!input.trim() || isStreaming) return;
    if (phase === "interview") {
      answerInterview(input.trim());
    } else if (phase === "coach") {
      sendToCoach(input.trim());
    } else {
      sendMessage(input.trim(), targetRole);
      setTargetRole("all");
    }
    setInput("");
  };

  const { isRecording, isTranscribing, transcript, errorMessage, start, stop, reset, status } = useSpeechRecognition();

  useEffect(() => {
    if (transcript) {
      setInput((prev) => prev + transcript);
      reset();
    }
  }, [transcript, reset]);

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
              aria-label="进入面试模式"
              variant="ghost"
              size="sm"
              className="text-xs text-muted-foreground hover:text-destructive"
            >
              <Target size={14} /> 进入面试
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

      <div className="flex gap-2 items-end">
        <div className="flex-1 relative">
          <Textarea
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
        <Button
          onClick={handleSend}
          disabled={!input.trim() || isStreaming}
          aria-label="发送消息"
          size="icon"
          className="shrink-0 rounded-full min-w-[44px] min-h-[44px]"
        >
          <Send size={18} />
        </Button>
      </div>
    </div>
  );
}
