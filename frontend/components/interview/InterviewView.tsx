"use client";

import { useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { useSessionStore } from "@/store/sessionStore";
import { InterviewHeader } from "./InterviewHeader";
import { InterviewInput } from "./InterviewInput";
import { PrdViewer } from "@/components/pipeline/PrdViewer";
import { playInterviewerTTS } from "@/lib/audio";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Card } from "@/components/ui/card";
import { Avatar, AvatarImage, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import ReactMarkdown from "react-markdown";

const INTERVIEWER_AVATAR = "/avatars/interviewer-business.svg";
const USER_AVATAR = "/avatars/user.svg";

interface InterviewViewProps {
  dimensionsCovered?: string[];
  questionCount?: number;
}

export function InterviewView({
  dimensionsCovered = [],
  questionCount = 0,
}: InterviewViewProps) {
  const messages = useSessionStore((s) => s.messages);
  const interviewMode = useSessionStore((s) => s.interviewMode);
  const isStreaming = useSessionStore((s) => s.isStreaming);
  const streamingContent = useSessionStore((s) => s.streamingContent);
  const streamingRole = useSessionStore((s) => s.streamingRole);
  const setPlayingAudio = useSessionStore((s) => s.setPlayingAudio);
  const isPlayingAudio = useSessionStore((s) => s.isPlayingAudio);
  const pipelineResult = useSessionStore((s) => s.pipelineResult);
  const lastAnswerQuality = useSessionStore((s) => s.lastAnswerQuality);
  const lastMessageRef = useRef<string | null>(null);
  const ttsAbortRef = useRef<AbortController | null>(null);
  const ttsCleanupRef = useRef<(() => void) | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const [phoneMode, setPhoneMode] = useState(false);
  const [showPrd, setShowPrd] = useState(false);

  const togglePhoneMode = () => setPhoneMode((p) => !p);

  useEffect(() => {
    if (interviewMode !== "voice" || isStreaming) return;

    const lastAiMsg = [...messages].reverse().find((m) => m.role === "assistant");
    if (!lastAiMsg) return;

    const key = lastAiMsg.content.slice(0, 100);
    if (key === lastMessageRef.current) return;

    if (isPlayingAudio) return;

    ttsAbortRef.current?.abort();
    ttsCleanupRef.current?.();
    ttsCleanupRef.current = null;

    const controller = new AbortController();
    ttsAbortRef.current = controller;

    setPlayingAudio(true);
    playInterviewerTTS(
      lastAiMsg.content,
      () => {},
      controller.signal
    )
      .then((cleanup) => {
        ttsCleanupRef.current = cleanup;
      })
      .catch((err) => {
        if (err.name !== "AbortError") {
          console.warn("TTS 播放失败:", err);
        }
      })
      .finally(() => setPlayingAudio(false));

    return () => {
      controller.abort();
      ttsCleanupRef.current?.();
      ttsCleanupRef.current = null;
    };
  }, [messages, interviewMode, isStreaming, isPlayingAudio, setPlayingAudio]);

  useEffect(() => {
    const raf = requestAnimationFrame(() => {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    });
    return () => cancelAnimationFrame(raf);
  }, [messages, streamingContent]);

  return (
    <div className="interview-dark-container h-full flex flex-col">
      <div className={cn(
        "interview-glow-border",
        lastAnswerQuality === "good" && "quality-good",
        lastAnswerQuality === "bad" && "quality-bad",
        lastAnswerQuality === "neutral" && "quality-neutral",
      )} />
      <InterviewHeader
        phoneMode={phoneMode}
        onTogglePhoneMode={togglePhoneMode}
        dimensionsCovered={dimensionsCovered}
        hasPrd={!!pipelineResult?.prd}
        onViewPrd={() => setShowPrd(true)}
      />

      <ScrollArea className={cn("interview-stage flex-1 bg-background", phoneMode && "hidden")}>
        <div className="interview-transcript">
          {messages.length === 0 && !isStreaming && (
            <div className="flex items-center justify-center h-full min-h-[300px]">
              <div className="text-center">
                <div className="mb-4 flex justify-center">
                  <Avatar className="h-16 w-16 border-2 border-primary/30 shadow-md">
                    <AvatarImage src={INTERVIEWER_AVATAR} alt="AI 面试官" />
                    <AvatarFallback>AI</AvatarFallback>
                  </Avatar>
                </div>
                <p className="text-[10px] tracking-[0.22em] text-cyan-200/45 mb-2">AUDIT CHANNEL READY</p>
                <p className="text-lg font-semibold text-slate-100 mb-1">准备进入专业审计</p>
                <p className="text-sm text-slate-400">六维审计框架将连续质询你的产品方案</p>
              </div>
            </div>
          )}

          {messages.length > 0 && (
            <div className="text-center mb-4 pt-1">
              <Badge variant="secondary">
                面试开始{questionCount > 0 ? ` · 第 ${questionCount} 题` : ""}
              </Badge>
            </div>
          )}

          {messages.map((msg, idx) => (
            <InterviewMessage key={msg.id || idx} message={msg} />
          ))}

          {isStreaming && streamingContent && (
            <InterviewMessage
              message={{
                role: "assistant",
                content: streamingContent,
                role_name: streamingRole || "interviewer",
              }}
              isStreaming
            />
          )}

          {isStreaming && !streamingContent && (
            <div className="flex items-center gap-3 py-3 px-2 mb-2">
              <Avatar className="h-10 w-10 shrink-0 border border-primary/30">
                <AvatarImage src={INTERVIEWER_AVATAR} alt="AI 面试官" />
                <AvatarFallback>AI</AvatarFallback>
              </Avatar>
              <div className="flex items-center gap-2">
                <Loader2 className="h-4 w-4 animate-spin text-primary" />
                <Badge variant="secondary">思考中</Badge>
              </div>
            </div>
          )}

          <div ref={bottomRef} />
        </div>
      </ScrollArea>

      <InterviewInput phoneMode={phoneMode} onTogglePhoneMode={togglePhoneMode} />

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

function InterviewMessage({
  message,
  isStreaming,
}: {
  message: { role: string; content: string; role_name?: string };
  isStreaming?: boolean;
}) {
  const isUser = message.role === "user";

  if (isUser) {
    return (
      <div className="msg-enter flex justify-end mb-4 gap-2">
        <div className="max-w-[75%]">
          <Card
            className={cn(
              "interview-dark-bubble-user relative px-4 py-3 shadow-sm rounded-2xl rounded-br-[4px] border-0",
              isStreaming && "streaming-cursor"
            )}
          >
            <p className="text-sm leading-relaxed whitespace-pre-wrap">
              {message.content}
            </p>
          </Card>
        </div>
        <Avatar className="h-10 w-10 shrink-0 mt-0.5 border border-border">
          <AvatarImage src={USER_AVATAR} alt="用户" />
          <AvatarFallback>我</AvatarFallback>
        </Avatar>
      </div>
    );
  }

  return (
    <div className="msg-enter flex gap-3 mb-4">
      <Avatar className="h-10 w-10 shrink-0 mt-0.5 border border-primary/30">
        <AvatarImage src={INTERVIEWER_AVATAR} alt="AI 面试官" />
        <AvatarFallback>AI</AvatarFallback>
      </Avatar>
      <div className="flex-1 min-w-0 max-w-[85%]">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-xs font-semibold text-slate-200">AI 压力面试官</span>
          <Badge variant="secondary" className="flex items-center gap-1 text-[11px] py-0 px-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-primary" />
            压力测试中
          </Badge>
        </div>
        <Card
          className={cn(
            "interview-dark-bubble-ai relative px-4 py-3 shadow-sm rounded-2xl rounded-tl-[4px]",
            isStreaming && "streaming-cursor"
          )}
        >
          <div className="absolute left-0 top-2 bottom-2 w-[3px] rounded-full bg-blue-500" />
          <div className="pl-2">
            <div className="prose prose-sm prose-invert max-w-none text-slate-200 leading-relaxed">
              {isStreaming ? (
                <p className="whitespace-pre-wrap">{message.content}</p>
              ) : (
                <ReactMarkdown>{message.content}</ReactMarkdown>
              )}
            </div>
          </div>
        </Card>
      </div>
    </div>
  );
}
