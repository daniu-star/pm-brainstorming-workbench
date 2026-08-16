"use client";

import { useCallback, useState } from "react";
import { MicIcon, SendIcon } from "@/components/icons";
import { useSpeechRecognition } from "@/hooks/useSpeechRecognition";
import { useSessionStore } from "@/store/sessionStore";

export function InterviewInput({
  // B045/67/68：TTS 播放期间禁用麦克风，避免回声/自激
  isPlayingAudio = false,
}: {
  isPlayingAudio?: boolean;
}) {
  const [input, setInput] = useState("");
  const { answerInterview, interviewMode, isStreaming } = useSessionStore();
  const {
    isRecording,
    transcript,
    interim,
    remainingSeconds,
    start,
    stop,
    reset,
    isSupported,
    status,
    error,
    engine,
  } = useSpeechRecognition();

  const handleTextSend = () => {
    if (!input.trim() || isStreaming) return;
    answerInterview(input.trim());
    setInput("");
  };

  const handleVoiceSend = useCallback(() => {
    const text = `${transcript}${interim}`.trim();
    if (!text || isStreaming) return;
    answerInterview(text);
    reset();
  }, [transcript, interim, isStreaming, answerInterview, reset]);

  return (
    <div className="shrink-0 border-t border-white/10 bg-[#070b10] px-4 py-3">
      {interviewMode === "text" ? (
        <div className="flex items-end gap-2">
          <textarea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder="回答审计官的问题…"
            rows={3}
            aria-label="回答审计官问题"
            className="min-h-[74px] flex-1 resize-none rounded-xl border border-white/10 bg-black/20 px-3.5 py-2.5 text-xs leading-5 text-zinc-200 outline-none transition-colors duration-150 placeholder:text-zinc-600 focus:border-cyan-300/40 focus:ring-2 focus:ring-cyan-300/10"
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                handleTextSend();
              }
            }}
          />
          <button
            type="button"
            onClick={handleTextSend}
            disabled={!input.trim() || isStreaming}
            aria-label="发送回答"
            className="flex size-11 shrink-0 items-center justify-center rounded-lg bg-cyan-300 text-[#031014] transition-colors duration-150 hover:bg-cyan-200 disabled:cursor-not-allowed disabled:bg-zinc-800 disabled:text-zinc-600"
          >
            <SendIcon size={17} />
          </button>
        </div>
      ) : (
        <div>
          <div className="mb-3 min-h-14 rounded-lg border border-white/10 bg-black/20 px-3 py-2.5">
            {engine && (
              <span className="mb-1 block text-[10px] uppercase text-cyan-200/50">
                {engine === "server" ? "AI 高精度转写" : "浏览器实时识别"}
              </span>
            )}
            {transcript || interim ? (
              <p className="text-xs leading-5 text-zinc-300">
                {transcript}
                <span className="text-zinc-500">{interim}</span>
              </p>
            ) : (
              <p className="text-xs leading-5 text-zinc-600">
                {status === "requesting"
                  ? "正在请求麦克风权限…"
                  : isPlayingAudio
                    ? "AI 正在发言，请稍候…"
                    : isRecording
                      ? "正在聆听，请清晰表达你的判断…"
                      : "点击麦克风开始回答"}
              </p>
            )}
          </div>

          {error && (
            <p role="alert" className="mb-3 rounded-lg border border-red-400/20 bg-red-400/5 px-3 py-2 text-[10px] leading-4 text-red-300">
              {error}
            </p>
          )}

          {!isSupported && !error && (
            <p className="mb-3 text-[10px] leading-4 text-amber-300">
              当前浏览器不支持语音识别，请切换到最新版 Chrome 或 Edge，或使用文字模式。
            </p>
          )}

          {/* B083：接近录音上限时倒计时提示 */}
          {isRecording && remainingSeconds !== null && remainingSeconds <= 10 && (
            <p aria-live="polite" className="mb-3 text-center text-[10px] leading-4 text-amber-300">
              还剩 {remainingSeconds}s 自动结束
            </p>
          )}

          <div className="flex items-center justify-center gap-3">
            <button
              type="button"
              onClick={isRecording ? stop : start}
              disabled={!isSupported || status === "requesting" || isPlayingAudio}
              aria-label={isRecording ? "停止语音识别" : "开始语音识别"}
              title={isPlayingAudio ? "AI 正在发言，暂时无法录音" : undefined}
              className={`flex size-12 items-center justify-center rounded-full border transition-colors duration-150 ${
                isRecording
                  ? "border-red-300/40 bg-red-400/15 text-red-200"
                  : "border-cyan-300/30 bg-cyan-300/10 text-cyan-200 hover:bg-cyan-300/15"
              } disabled:cursor-not-allowed disabled:opacity-40`}
            >
              <MicIcon size={20} />
            </button>

            {(transcript || interim) && !isRecording && (
              <button
                type="button"
                onClick={handleVoiceSend}
                disabled={isStreaming || status === "processing"}
                aria-label="发送语音转写结果"
                className="flex size-11 items-center justify-center rounded-full bg-cyan-300 text-[#031014] hover:bg-cyan-200 disabled:cursor-not-allowed disabled:bg-zinc-800 disabled:text-zinc-600"
              >
                <SendIcon size={17} />
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
