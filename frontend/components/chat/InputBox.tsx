"use client";

import { useState, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import { useSessionStore } from "@/store/sessionStore";
import { isAuditAnswerPhase } from "@/lib/phaseRouter";
import { useSpeechRecognition } from "@/hooks/useSpeechRecognition";
import { ROLES } from "@/lib/types";
import { BrainIcon, VideoIcon, SendIcon, MicIcon, ArrowRightIcon } from "@/components/icons";

export function InputBox() {
  const router = useRouter();
  const [input, setInput] = useState("");
  const appliedTranscriptRef = useRef("");
  const {
    sendMessage,
    sendToCoach,
    answerInterview,
    skipCoach,
    phase,
    v2Phase,
    isStreaming,
    generateCanvas,
    sessionId,
    targetRole,
    setTargetRole,
  } = useSessionStore();

  // v2 阶段以原值为准：旧 phase 会把 auditing 与 audit_completed 混成 interview，
  // 只有 auditing 才允许回答审计问题（audit_completed 下发送走 brainstorm）
  const auditing = v2Phase ? isAuditAnswerPhase(v2Phase) : phase === "interview";

  const handleSend = () => {
    if (!input.trim() || isStreaming) return;
    void (async () => {
      if (auditing) {
        // 提交失败（无待回答的问题 / 网络错误）时保留输入，便于直接重发
        const submitted = await answerInterview(input.trim());
        if (submitted) setInput("");
        return;
      }
      if (!v2Phase && phase === "coach") {
        sendToCoach(input.trim());
      } else {
        sendMessage(input.trim(), useSessionStore.getState().targetRole);
      }
      setInput("");
    })();
  };

  const { isRecording, transcript, start, stop, status, error: speechError, isSupported } = useSpeechRecognition();

  // Append voice transcript to input
  useEffect(() => {
    if (!transcript) {
      appliedTranscriptRef.current = "";
      return;
    }

    const addition = transcript.startsWith(appliedTranscriptRef.current)
      ? transcript.slice(appliedTranscriptRef.current.length)
      : transcript;
    if (addition) setInput((current) => current + addition);
    appliedTranscriptRef.current = transcript;
  }, [transcript]);

  const targetRoleInfo = ROLES.find((r) => r.id === targetRole);

  return (
    <div className="px-4 py-3 border-t border-zinc-800/50 bg-dark-900/50">
      {/* Action bar */}
      <div className="flex items-center gap-3 mb-2">
        {phase === "coach" && (
          <>
            <span className="text-xs text-amber-400/70 flex items-center gap-1">
              <span className="w-1.5 h-1.5 bg-amber-500 rounded-full animate-pulse" />
              产品教练正在帮你理清思路
            </span>
            <button
              onClick={skipCoach}
              disabled={isStreaming}
              className="text-xs text-brand-300 hover:text-brand-400 transition-colors duration-200 disabled:opacity-40 ml-auto flex items-center gap-1 min-h-[44px] min-w-[44px] justify-center"
            >
              跳过引导，直接脑暴 <ArrowRightIcon size={14} />
            </button>
          </>
        )}
        {(v2Phase
          ? v2Phase === "brainstorming" || v2Phase === "audit_completed"
          : phase === "brainstorm") && (
          <>
            <button
              onClick={() => void generateCanvas()}
              disabled={isStreaming}
              className="text-xs text-zinc-400 hover:text-emerald-400 transition-colors duration-200 disabled:opacity-40 flex items-center gap-1.5 min-h-[44px] min-w-[44px] justify-center"
            >
              <BrainIcon size={14} /> 生成画布
            </button>
            <span className="text-zinc-500">|</span>
            <button
              onClick={() => sessionId && router.push(`/session/${sessionId}/audit`)}
              disabled={isStreaming || !sessionId}
              className="text-xs text-zinc-400 hover:text-cyan-300 transition-colors duration-150 disabled:opacity-40 flex items-center gap-1.5 min-h-[44px] min-w-[44px] justify-center"
            >
              <VideoIcon size={14} /> 进入 AI 审计
            </button>
          </>
        )}
        {auditing && (
          <span className="text-xs text-red-400/70 flex items-center gap-1">
            <span className="w-1.5 h-1.5 bg-red-500 rounded-full animate-pulse" />
            审计进行中 — 回答每个问题以继续
          </span>
        )}
        {v2Phase === "audit_completed" && (
          <span className="text-xs text-emerald-400/70 flex items-center gap-1">
            <span className="w-1.5 h-1.5 bg-emerald-500 rounded-full" />
            审计已完成 — 可继续讨论或重新审计
          </span>
        )}
      </div>

      {/* Current @ target chip — click to clear back to "all" */}
      {phase === "brainstorm" && targetRole !== "all" && targetRoleInfo && (
        <div className="mb-2 flex items-center gap-2">
          <button
            type="button"
            onClick={() => setTargetRole("all")}
            className="inline-flex min-h-[40px] items-center gap-1.5 rounded-full border border-brand-400/40 bg-brand-500/10 px-3 py-1 text-xs font-medium text-brand-300 hover:bg-brand-500/20 transition-colors"
            aria-label={`当前定向 ${targetRoleInfo.name}，点击恢复全员讨论`}
          >
            @ {targetRoleInfo.name}
            <span aria-hidden="true" className="text-zinc-400">
              ✕
            </span>
          </button>
          <span className="text-[10px] text-zinc-500">已定向该角色，点击取消恢复群发</span>
        </div>
      )}

      {speechError && (
        <p role="alert" className="mb-2 rounded-lg border border-red-400/20 bg-red-400/5 px-3 py-2 text-[10px] text-red-300">
          {speechError}
        </p>
      )}

      {/* Input row */}
      <div className="flex gap-2 items-end">
        <div className="flex-1 relative">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={
              auditing
                ? "回答审计官的问题..."
                : v2Phase === "audit_completed"
                  ? "继续讨论、追问或提出新的想法..."
                  : phase === "coach"
                    ? "回答产品教练的问题，帮助理清你的想法..."
                    : targetRole !== "all" && targetRoleInfo
                      ? `向 ${targetRoleInfo.name} 提问...`
                      : "插话、追问或提出你的想法..."
            }
            rows={2}
            aria-label={
              auditing
                ? "回答审计官问题"
                : phase === "coach"
                  ? "回答产品教练的问题"
                  : "输入你的想法或追问"
            }
            className="w-full bg-dark-800 border border-zinc-700/50 rounded-xl px-4 py-2.5 text-sm text-zinc-200 placeholder-zinc-500 resize-none focus:outline-none focus:border-brand-500/50 focus:ring-2 focus:ring-brand-500/30 transition-all"
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                handleSend();
              }
            }}
          />
        </div>
        <button
          onClick={isRecording ? stop : start}
          disabled={isStreaming || !isSupported || status === "requesting"}
          aria-label={isRecording ? "停止录音" : "语音输入"}
          className={`shrink-0 w-12 h-12 min-w-[48px] min-h-[48px] rounded-xl transition-all duration-200 flex items-center justify-center focus:outline-none focus:ring-2 focus:ring-brand-500/50 focus:ring-offset-2 focus:ring-offset-dark-900 ${
            isRecording
              ? "bg-red-600 hover:bg-red-500 text-white"
              : "bg-zinc-800 hover:bg-zinc-700 text-zinc-400 hover:text-zinc-300 disabled:text-zinc-500"
          }`}
        >
          <MicIcon size={18} />
        </button>
        <button
          onClick={handleSend}
          disabled={!input.trim() || isStreaming}
          aria-label="发送消息"
          className="shrink-0 w-12 h-12 min-w-[48px] min-h-[48px] bg-brand-600 hover:bg-brand-500 disabled:bg-zinc-700 disabled:text-zinc-500 text-white rounded-xl transition-all duration-200 flex items-center justify-center focus:outline-none focus:ring-2 focus:ring-brand-500/50 focus:ring-offset-2 focus:ring-offset-dark-900"
        >
          <SendIcon size={18} />
        </button>
      </div>
    </div>
  );
}
