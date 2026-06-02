"use client";

import { useState, useEffect } from "react";
import { useSessionStore } from "@/store/sessionStore";
import { useSpeechRecognition } from "@/hooks/useSpeechRecognition";
import { BrainIcon, InterviewIcon, SendIcon, MicIcon, ArrowRightIcon } from "@/components/icons";
import { ROLES } from "@/lib/types";

export function InputBox() {
  const [input, setInput] = useState("");
  const sendMessage = useSessionStore((s) => s.sendMessage);
  const sendToCoach = useSessionStore((s) => s.sendToCoach);
  const answerInterview = useSessionStore((s) => s.answerInterview);
  const phase = useSessionStore((s) => s.phase);
  const isStreaming = useSessionStore((s) => s.isStreaming);
  const generateCanvas = useSessionStore((s) => s.generateCanvas);
  const startInterview = useSessionStore((s) => s.startInterview);
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
    <div className="px-4 py-3 border-t border-warm-200/80 bg-white/95 backdrop-blur-sm">
      <div className="flex items-center gap-3 mb-2">
        {phase === "coach" && (
          <>
            <span className="text-xs text-amber-600 flex items-center gap-1">
              <span className="w-1.5 h-1.5 bg-amber-500 rounded-full animate-pulse" />
              产品教练正在帮你理清思路
            </span>
            <button
              onClick={() => {
                if (!isStreaming) {
                  useSessionStore.getState().setPhase("brainstorm");
                }
              }}
              disabled={isStreaming}
              aria-label="跳过引导直接脑暴"
              className="text-xs text-amber-600 hover:text-amber-700 active:text-amber-800 transition-all duration-200 disabled:opacity-40 ml-auto flex items-center gap-1 min-h-[44px] min-w-[44px] justify-center focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none"
            >
              跳过引导，直接脑暴 <ArrowRightIcon size={14} />
            </button>
          </>
        )}
        {phase === "brainstorm" && (
          <>
            <button
              onClick={generateCanvas}
              disabled={isStreaming}
              aria-label="生成讨论画布"
              className="text-xs text-warm-500 hover:text-emerald-600 active:text-emerald-700 transition-all duration-200 disabled:opacity-40 flex items-center gap-1.5 min-h-[44px] min-w-[44px] justify-center focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none"
            >
              <BrainIcon size={14} /> 生成画布
            </button>
            <span className="text-warm-300">|</span>
            <button
              onClick={startInterview}
              disabled={isStreaming}
              aria-label="进入面试模式"
              className="text-xs text-warm-500 hover:text-red-600 active:text-red-700 transition-all duration-200 disabled:opacity-40 flex items-center gap-1.5 min-h-[44px] min-w-[44px] justify-center focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none"
            >
              <InterviewIcon size={14} /> 进入面试
            </button>
          </>
        )}
        {phase === "interview" && (
          <span className="text-xs text-red-600 flex items-center gap-1">
            <span className="w-1.5 h-1.5 bg-red-500 rounded-full animate-pulse" />
            面试模式中 — 回答每个问题以继续
          </span>
        )}
      </div>

      {status === "recording" && (
        <div className="voice-transcript-bar mb-2 px-3 py-1.5 bg-red-50 border border-red-200 rounded-lg text-xs text-red-600 flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
          <span>正在聆听...</span>
          <span className="flex items-center gap-0.5 ml-1">
            {Array.from({ length: 5 }).map((_, i) => (
              <span key={i} className="inline-block w-0.5 bg-red-400 rounded-full animate-pulse" style={{ height: `${8 + Math.random() * 8}px`, animationDelay: `${i * 0.15}s` }} />
            ))}
          </span>
        </div>
      )}
      {status === "transcribing" && (
        <div className="voice-transcript-bar mb-2 px-3 py-1.5 bg-amber-50 border border-amber-200 rounded-lg text-xs text-amber-700 flex items-center gap-2">
          <svg className="animate-spin h-3.5 w-3.5 text-amber-500" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
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
        <div className="voice-transcript-bar mb-2 px-3 py-1.5 bg-red-50 border border-red-200 rounded-lg text-xs text-red-600 flex items-center gap-2">
          <svg className="h-3.5 w-3.5 text-red-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          <span className="flex-1">{errorMessage}</span>
          <button onClick={start} className="px-2 py-0.5 bg-red-100 hover:bg-red-200 active:bg-red-300 rounded text-red-700 transition-colors">
            重试
          </button>
        </div>
      )}

      <div className="flex gap-2 items-end">
        <div className="flex-1 relative">
          <textarea
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
            className="w-full bg-warm-50 border border-warm-200 rounded-2xl px-4 py-2.5 text-sm text-warm-600 placeholder-warm-400 resize-none focus:outline-none focus:border-amber-400 focus:ring-2 focus:ring-amber-400/30 transition-all" style={{ boxShadow: "inset 0 1px 3px rgba(139,111,71,0.06)" }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (!e.shiftKey || e.ctrlKey || e.metaKey)) {
                e.preventDefault();
                handleSend();
              }
            }}
          />
        </div>
        <div className="flex flex-col items-center shrink-0">
          <button
            onClick={isRecording ? stop : start}
            disabled={isStreaming || isTranscribing}
            aria-label={isRecording ? "停止录音" : "语音输入"}
            title="点击语音输入"
            className={`w-10 h-10 min-w-[44px] min-h-[44px] rounded-full transition-all duration-200 flex items-center justify-center focus:outline-none focus:ring-2 focus:ring-amber-500/50 focus:ring-offset-2 focus:ring-offset-white ${
              status === "recording"
                ? "mic-ripple bg-red-600 hover:bg-red-500 active:bg-red-700 text-white"
                : status === "transcribing"
                  ? "bg-amber-500 hover:bg-amber-400 active:bg-amber-600 text-white animate-pulse"
                  : status === "success"
                    ? "bg-emerald-500 text-white"
                    : "mic-pulse bg-warm-100 hover:bg-warm-200 active:bg-warm-300 text-warm-500 hover:text-warm-600 disabled:text-warm-400"
            }`}
          >
            <MicIcon size={18} />
          </button>
          {status === "recording" && (
            <span className="text-[10px] text-red-600 mt-0.5 whitespace-nowrap">正在聆听...</span>
          )}
          {status === "transcribing" && (
            <span className="text-[10px] text-amber-600 mt-0.5 whitespace-nowrap">正在识别...</span>
          )}
          {status === "success" && (
            <span className="text-[10px] text-emerald-600 mt-0.5 whitespace-nowrap">识别成功</span>
          )}
        </div>
        <button
          onClick={handleSend}
          disabled={!input.trim() || isStreaming}
          aria-label="发送消息"
          className="shrink-0 w-10 h-10 min-w-[44px] min-h-[44px] bg-gradient-to-r from-amber-500 to-orange-500 hover:from-amber-600 hover:to-orange-600 active:from-amber-700 active:to-orange-700 disabled:from-warm-300 disabled:to-warm-300 disabled:text-warm-400 text-white rounded-full transition-all duration-200 flex items-center justify-center focus:outline-none focus:ring-2 focus:ring-amber-500/50 focus:ring-offset-2 focus:ring-offset-white"
        >
          <SendIcon size={18} />
        </button>
      </div>
    </div>
  );
}
