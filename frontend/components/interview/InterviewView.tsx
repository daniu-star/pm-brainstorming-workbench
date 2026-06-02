"use client";

import { useEffect, useRef, useMemo, useState } from "react";
import { useSessionStore } from "@/store/sessionStore";
import { InterviewHeader } from "./InterviewHeader";
import { InterviewInput } from "./InterviewInput";
import { playTTSStream } from "@/lib/audio";
import ReactMarkdown from "react-markdown";

export function InterviewView() {
  const messages = useSessionStore((s) => s.messages);
  const interviewMode = useSessionStore((s) => s.interviewMode);
  const isStreaming = useSessionStore((s) => s.isStreaming);
  const streamingContent = useSessionStore((s) => s.streamingContent);
  const streamingRole = useSessionStore((s) => s.streamingRole);
  const setPlayingAudio = useSessionStore((s) => s.setPlayingAudio);
  const isPlayingAudio = useSessionStore((s) => s.isPlayingAudio);
  const lastMessageRef = useRef<string | null>(null);
  const ttsAbortRef = useRef<AbortController | null>(null);
  const ttsCleanupRef = useRef<(() => void) | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const [phoneMode, setPhoneMode] = useState(false);

  const togglePhoneMode = () => setPhoneMode((p) => !p);

  useEffect(() => {
    if (interviewMode !== "voice" || isStreaming) return;

    const lastAiMsg = [...messages].reverse().find((m) => m.role === "assistant");
    if (!lastAiMsg) return;

    const key = lastAiMsg.content.slice(0, 100);
    if (key === lastMessageRef.current) return;
    lastMessageRef.current = key;

    if (isPlayingAudio) return;

    ttsAbortRef.current?.abort();
    ttsCleanupRef.current?.();
    ttsCleanupRef.current = null;

    const controller = new AbortController();
    ttsAbortRef.current = controller;

    setPlayingAudio(true);
    playTTSStream(
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
    <div className="h-full flex flex-col">
      <InterviewHeader phoneMode={phoneMode} onTogglePhoneMode={togglePhoneMode} />

      <div className={`flex-1 overflow-y-auto bg-warm-50 px-4 py-4 ${phoneMode ? "hidden" : ""}`}>
        {messages.length === 0 && !isStreaming && (
          <div className="flex items-center justify-center h-full">
            <div className="text-center">
              <div className="mb-4 flex justify-center">
                <div className="w-16 h-16 rounded-full overflow-hidden border-2 border-slate-700 shadow-lg">
                  <img
                    src="/avatars/interviewer-business.svg"
                    alt="AI 面试官"
                    className="w-full h-full object-cover"
                  />
                </div>
              </div>
              <p className="text-lg font-semibold text-slate-800 mb-1">准备开始面试</p>
              <p className="text-sm text-warm-500">AI 压力面试官将对你进行深度提问</p>
            </div>
          </div>
        )}

        {messages.length > 0 && (
          <div className="text-center mb-4 pt-1">
            <span className="text-xs text-warm-500 bg-warm-100 px-3 py-1 rounded-full border border-warm-200">
              面试开始
            </span>
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
            <div className="shrink-0">
              <div className="w-10 h-10 rounded-full overflow-hidden border border-slate-600">
                <img
                  src="/avatars/interviewer-business.svg"
                  alt="AI 面试官"
                  className="w-full h-full object-cover"
                />
              </div>
            </div>
            <div className="flex items-center gap-2">
              <span className="interview-streaming-dot w-2 h-2 rounded-full bg-red-500" />
              <span className="text-sm text-slate-500">面试官正在思考...</span>
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      <InterviewInput phoneMode={phoneMode} onTogglePhoneMode={togglePhoneMode} />
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
      <div className="msg-enter flex justify-end mb-4">
        <div className="max-w-[75%]">
          <div
            className={`relative px-4 py-3 shadow-sm rounded-2xl rounded-br-[4px] ${
              isStreaming ? "streaming-cursor" : ""
            }`}
            style={{
              background: "linear-gradient(135deg, #f59e0b, #d97706)",
            }}
          >
            <p className="text-white text-sm leading-relaxed whitespace-pre-wrap">
              {message.content}
            </p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="msg-enter flex gap-3 mb-4">
      <div className="shrink-0 mt-0.5">
        <div className="w-10 h-10 rounded-full overflow-hidden border border-slate-600 shadow-sm">
          <img
            src="/avatars/interviewer-business.svg"
            alt="AI 面试官"
            className="w-full h-full object-cover"
          />
        </div>
      </div>
      <div className="flex-1 min-w-0 max-w-[85%]">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-xs font-semibold text-slate-700">AI 压力面试官</span>
          <span className="flex items-center gap-1 text-[10px] text-red-500">
            <span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" />
            压力测试中
          </span>
        </div>
        <div
          className={`relative bg-white border border-slate-200/80 px-4 py-3 shadow-sm rounded-2xl rounded-tl-[4px] ${
            isStreaming ? "streaming-cursor" : ""
          }`}
        >
          <div className="absolute left-0 top-2 bottom-2 w-[3px] rounded-full bg-red-500" />
          <div className="pl-2">
            <div className="prose prose-sm max-w-none text-slate-700 leading-relaxed">
              {isStreaming ? (
                <p className="whitespace-pre-wrap">{message.content}</p>
              ) : (
                <ReactMarkdown>{message.content}</ReactMarkdown>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
