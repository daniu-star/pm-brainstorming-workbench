"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronDown, Loader2 } from "lucide-react";
import { useSessionStore } from "@/store/sessionStore";
import { InterviewHeader } from "./InterviewHeader";
import { InterviewInput } from "./InterviewInput";
import { InterviewCamera } from "./InterviewCamera";
import { PrdViewer } from "@/components/pipeline/PrdViewer";
import { playInterviewerTTS } from "@/lib/audio";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Card } from "@/components/ui/card";
import { Avatar, AvatarImage, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import ReactMarkdown from "react-markdown";

const INTERVIEWER_AVATAR = "/avatars/interviewer-business.svg";
const USER_AVATAR = "/avatars/user.svg";
const DIMENSION_NAMES: Record<string, string> = {
  problem_validity: "问题有效性",
  solution_effectiveness: "方案有效性",
  technical_risk: "技术风险",
  business_viability: "商业可行性",
  user_adoption: "用户采纳",
  execution_risk: "执行风险",
};

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
  const currentAuditDimension = useSessionStore((s) => s.currentAuditDimension);
  const auditStatus = useSessionStore((s) => s.auditStatus);
  const lastMessageRef = useRef<string | null>(null);
  const ttsAbortRef = useRef<AbortController | null>(null);
  const ttsCleanupRef = useRef<(() => void) | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const scrollHostRef = useRef<HTMLDivElement>(null);
  const [isNearBottom, setIsNearBottom] = useState(true);
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
    const viewport = scrollHostRef.current?.querySelector<HTMLElement>(
      "[data-radix-scroll-area-viewport]"
    );
    if (!viewport) return;
    const updatePosition = () => {
      const distance = viewport.scrollHeight - viewport.scrollTop - viewport.clientHeight;
      setIsNearBottom(distance <= 120);
    };
    updatePosition();
    viewport.addEventListener("scroll", updatePosition, { passive: true });
    return () => viewport.removeEventListener("scroll", updatePosition);
  }, []);

  useEffect(() => {
    if (!isNearBottom) return;
    const raf = requestAnimationFrame(() => {
      bottomRef.current?.scrollIntoView({ behavior: "auto" });
    });
    return () => cancelAnimationFrame(raf);
  }, [messages, streamingContent, isNearBottom]);

  return (
    <div className="interview-dark-container relative h-full flex flex-col">
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
      <InterviewCamera />

      <div ref={scrollHostRef} className={cn("relative min-h-0 flex-1", phoneMode && "hidden")}>
        <ScrollArea className="interview-stage h-full bg-background">
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
                <p className="text-xs tracking-[0.16em] text-cyan-100/70 mb-2">AUDIT CHANNEL READY</p>
                <p className="text-lg font-semibold text-slate-100 mb-1">准备进入专业审计</p>
                <p className="text-sm text-slate-400">六维审计框架将连续质询你的产品方案</p>
              </div>
            </div>
          )}

          {messages.length > 0 && (
            <div className="text-center mb-4 pt-1">
              <Badge variant="secondary">
                {auditStatus === "completed" ? "审计完成" : "审计进行中"}
                {questionCount > 0 ? ` · 第 ${questionCount} 题` : ""}
                {currentAuditDimension ? ` · ${DIMENSION_NAMES[currentAuditDimension] || currentAuditDimension}` : ""}
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
        {!isNearBottom && (
          <Button
            type="button"
            onClick={() => {
              bottomRef.current?.scrollIntoView({ behavior: "smooth" });
              setIsNearBottom(true);
            }}
            className="absolute bottom-4 left-1/2 z-20 -translate-x-1/2 rounded-full"
            size="sm"
          >
            <ChevronDown className="h-3.5 w-3.5" />
            回到最新
          </Button>
        )}
      </div>

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
          <span className="text-xs font-semibold text-slate-200">
            {message.role_name === "audit_report" || message.role_name === "AI审计报告" ? "AI 审计报告" : "AI 专业审计官"}
          </span>
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
