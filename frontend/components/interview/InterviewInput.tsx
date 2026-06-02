"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import { useSessionStore } from "@/store/sessionStore";
import { useSpeechRecognition } from "@/hooks/useSpeechRecognition";
import { SendIcon, MicIcon } from "@/components/icons";

interface InterviewInputProps {
  phoneMode?: boolean;
  onTogglePhoneMode?: () => void;
}

export function InterviewInput({ phoneMode, onTogglePhoneMode }: InterviewInputProps) {
  const [input, setInput] = useState("");
  const { answerInterview, interviewMode, isStreaming, isPlayingAudio, setInterviewMode } = useSessionStore();
  const {
    isRecording,
    isTranscribing,
    transcript,
    errorMessage,
    start,
    stop,
    reset,
    isSupported,
    status,
  } = useSpeechRecognition();

  useEffect(() => {
    if (isPlayingAudio && isRecording) {
      stop();
    }
  }, [isPlayingAudio, isRecording, stop]);

  useEffect(() => {
    if (phoneMode && transcript.trim() && !isRecording && !isTranscribing && !isStreaming) {
      answerInterview(transcript.trim());
      reset();
    }
  }, [phoneMode, transcript, isRecording, isTranscribing, isStreaming, answerInterview, reset]);

  useEffect(() => {
    if (phoneMode && interviewMode !== "voice") {
      setInterviewMode("voice");
    }
  }, [phoneMode, interviewMode, setInterviewMode]);

  const handleTextSend = () => {
    if (!input.trim() || isStreaming) return;
    answerInterview(input.trim());
    setInput("");
  };

  const handleVoiceSend = useCallback(() => {
    if (!transcript.trim() || isStreaming) return;
    answerInterview(transcript.trim());
    reset();
  }, [transcript, isStreaming, answerInterview, reset]);

  if (phoneMode) {
    return (
      <PhoneModeView
        isRecording={isRecording}
        isTranscribing={isTranscribing}
        isSupported={isSupported}
        errorMessage={errorMessage}
        start={start}
        stop={stop}
        onHangUp={onTogglePhoneMode}
        status={status}
      />
    );
  }

  if (interviewMode === "text") {
    return (
      <div className="px-4 py-3 border-t border-slate-200 bg-white">
        <div className="flex gap-2 items-end">
          <div className="flex-1 relative">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="回答面试官的问题..."
              rows={3}
              aria-label="回答面试官问题"
              className="w-full bg-warm-50 border border-slate-200 rounded-xl px-4 py-2.5 text-sm text-slate-700 placeholder-slate-400 resize-none focus:outline-none focus:border-amber-400 focus:ring-2 focus:ring-amber-400/30 transition-all"
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  handleTextSend();
                }
              }}
            />
          </div>
          <button
            onClick={handleTextSend}
            disabled={!input.trim() || isStreaming}
            aria-label="发送回答"
            className="shrink-0 w-12 h-12 min-w-[48px] min-h-[48px] bg-gradient-to-r from-amber-500 to-amber-600 hover:from-amber-600 hover:to-amber-700 active:from-amber-700 active:to-amber-800 disabled:bg-slate-200 disabled:text-slate-400 text-white rounded-xl transition-all duration-200 flex items-center justify-center focus:outline-none focus:ring-2 focus:ring-amber-500/50 focus:ring-offset-2 focus:ring-offset-white"
          >
            <SendIcon size={18} />
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="px-4 py-3 border-t border-slate-200 bg-white">
      <VoiceInputView
        isRecording={isRecording}
        isTranscribing={isTranscribing}
        transcript={transcript}
        errorMessage={errorMessage}
        isSupported={isSupported}
        isStreaming={isStreaming}
        start={start}
        stop={stop}
        reset={reset}
        onSend={handleVoiceSend}
        status={status}
      />
    </div>
  );
}

function VoiceInputView({
  isRecording,
  isTranscribing,
  transcript,
  errorMessage,
  isSupported,
  isStreaming,
  start,
  stop,
  reset,
  onSend,
  status,
}: {
  isRecording: boolean;
  isTranscribing: boolean;
  transcript: string;
  errorMessage: string;
  isSupported: boolean;
  isStreaming: boolean;
  start: () => void;
  stop: () => void;
  reset: () => void;
  onSend: () => void;
  status: "idle" | "recording" | "transcribing" | "success" | "error";
}) {
  const isActive = isRecording || isTranscribing || !!transcript || !!errorMessage;

  if (!isSupported) {
    return (
      <div className="flex flex-col items-center gap-3 py-4">
        <div className="w-16 h-16 rounded-full bg-slate-100 flex items-center justify-center opacity-40">
          <MicIcon size={28} />
        </div>
        <p className="text-sm text-slate-500">您的浏览器不支持语音输入</p>
      </div>
    );
  }

  if (!isActive) {
    return (
      <div className="flex flex-col items-center gap-4 py-4">
        <div className="mic-pulse relative">
          <button
            onClick={start}
            aria-label="开始录音"
            className="w-16 h-16 min-w-[44px] min-h-[44px] rounded-full bg-slate-100 hover:bg-slate-200 active:bg-slate-200 text-slate-500 hover:text-slate-600 transition-all duration-200 flex items-center justify-center focus:outline-none focus:ring-2 focus:ring-amber-500/50 focus:ring-offset-2 focus:ring-offset-white"
          >
            <MicIcon size={28} />
          </button>
        </div>
        <div className="flex flex-col items-center gap-1.5">
          <p className="text-sm text-slate-600">点击麦克风开始语音对话</p>
          <p className="text-xs text-slate-500">语音识别将自动转为文字</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-center gap-3">
      {status === "recording" && (
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
          <p className="text-sm text-red-600">正在聆听...</p>
          <span className="flex items-center gap-0.5 ml-1">
            {Array.from({ length: 5 }).map((_, i) => (
              <span key={i} className="inline-block w-0.5 bg-red-400 rounded-full animate-pulse" style={{ height: `${8 + Math.random() * 8}px`, animationDelay: `${i * 0.15}s` }} />
            ))}
          </span>
        </div>
      )}
      {status === "transcribing" && (
        <div className="flex items-center gap-2">
          <svg className="animate-spin h-4 w-4 text-amber-500" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          <p className="text-sm text-amber-600">正在识别...</p>
        </div>
      )}
      {status === "success" && (
        <div className="flex items-center gap-2">
          <svg className="h-4 w-4 text-emerald-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
          </svg>
          <p className="text-sm text-emerald-600">识别成功</p>
        </div>
      )}
      {status === "error" && (
        <div className="flex flex-col items-center gap-2 w-full">
          <div className="flex items-center gap-2">
            <svg className="h-4 w-4 text-red-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <p className="text-sm text-red-600">{errorMessage}</p>
          </div>
          <div className="flex items-center gap-3">
            <button onClick={start} className="px-3 py-1.5 bg-red-100 hover:bg-red-200 active:bg-red-300 rounded-lg text-xs text-red-700 transition-colors min-h-[44px]">
              重试
            </button>
            <button onClick={reset} className="text-xs text-slate-500 hover:text-slate-700 transition-colors underline">
              切换到文字模式
            </button>
          </div>
        </div>
      )}
      {transcript && status !== "error" && (
        <div className="w-full bg-warm-50 border border-slate-200 rounded-xl px-4 py-2.5 text-sm text-slate-700 min-h-[40px]">
          {transcript}
        </div>
      )}

      <div className="flex items-center gap-4">
        <div className={status === "recording" ? "mic-ripple relative" : ""}>
          <button
            onClick={isRecording ? stop : start}
            disabled={isTranscribing}
            aria-label={isRecording ? "停止录音" : "开始录音"}
            className={`w-16 h-16 min-w-[44px] min-h-[44px] rounded-full transition-all duration-200 flex items-center justify-center focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-offset-white ${
              status === "recording"
                ? "bg-red-600 hover:bg-red-500 active:bg-red-700 text-white focus:ring-red-500/50"
                : status === "transcribing"
                  ? "bg-amber-500 hover:bg-amber-400 active:bg-amber-600 text-white focus:ring-amber-500/50 animate-pulse"
                  : status === "success"
                    ? "bg-emerald-500 text-white focus:ring-emerald-500/50"
                    : "mic-pulse bg-slate-100 hover:bg-slate-200 active:bg-slate-200 text-slate-500 hover:text-slate-600 focus:ring-amber-500/50"
            }`}
          >
            <MicIcon size={28} />
          </button>
        </div>

        {transcript && status !== "error" && (
          <button
            onClick={onSend}
            disabled={isStreaming}
            aria-label="发送语音转录"
            className="w-12 h-12 min-w-[48px] min-h-[48px] bg-gradient-to-r from-amber-500 to-amber-600 hover:from-amber-600 hover:to-amber-700 active:from-amber-700 active:to-amber-800 disabled:bg-slate-200 disabled:text-slate-400 text-white rounded-xl transition-all duration-200 flex items-center justify-center focus:outline-none focus:ring-2 focus:ring-amber-500/50 focus:ring-offset-2 focus:ring-offset-white"
          >
            <SendIcon size={18} />
          </button>
        )}
      </div>
    </div>
  );
}

function PhoneModeView({
  isRecording,
  isTranscribing,
  isSupported,
  errorMessage,
  start,
  stop,
  onHangUp,
  status,
}: {
  isRecording: boolean;
  isTranscribing: boolean;
  isSupported: boolean;
  errorMessage: string;
  start: () => void;
  stop: () => void;
  onHangUp?: () => void;
  status: "idle" | "recording" | "transcribing" | "success" | "error";
}) {
  const autoStartRef = useRef(false);

  useEffect(() => {
    if (isRecording) {
      autoStartRef.current = false;
    }
  }, [isRecording]);

  useEffect(() => {
    if (errorMessage) {
      autoStartRef.current = false;
    }
  }, [errorMessage]);

  useEffect(() => {
    if (isSupported && !autoStartRef.current && !isRecording && !isTranscribing && !errorMessage) {
      autoStartRef.current = true;
      const timer = setTimeout(() => {
        start();
      }, 500);
      return () => clearTimeout(timer);
    }
  }, [isRecording, isTranscribing, errorMessage, isSupported, start]);

  return (
    <div className="px-4 py-8 bg-gradient-to-b from-slate-800 to-slate-900 flex flex-col items-center gap-6">
      <div className="flex flex-col items-center gap-2">
        <div className="w-16 h-16 rounded-full overflow-hidden border-2 border-slate-600 shadow-lg">
          <img
            src="/avatars/interviewer-business.svg"
            alt="AI 面试官"
            className="w-full h-full object-cover"
          />
        </div>
        <span className="text-sm font-medium text-white">AI 压力面试官</span>
      </div>

      <div className="h-8 flex items-center justify-center">
        {status === "recording" && (
          <div className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
            <span className="text-sm text-red-400">正在聆听...</span>
          </div>
        )}
        {status === "transcribing" && (
          <div className="flex items-center gap-2">
            <svg className="animate-spin h-4 w-4 text-amber-400" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
            <span className="text-sm text-amber-400">正在识别...</span>
          </div>
        )}
        {status === "success" && (
          <div className="flex items-center gap-2">
            <svg className="h-4 w-4 text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
            </svg>
            <span className="text-sm text-emerald-400">识别成功</span>
          </div>
        )}
        {status === "error" && !isRecording && (
          <div className="flex flex-col items-center gap-2">
            <span className="text-xs text-red-400">{errorMessage}</span>
            <button onClick={start} className="px-3 py-1 bg-red-600 hover:bg-red-500 active:bg-red-700 text-white rounded-full text-xs font-medium transition-all duration-200 min-h-[44px]">
              重试
            </button>
          </div>
        )}
      </div>

      {isRecording && (
        <div className="phone-waveform flex items-center gap-1 h-8">
          {Array.from({ length: 12 }).map((_, i) => (
            <div
              key={i}
              className="phone-waveform-bar w-1 bg-amber-400 rounded-full"
              style={{
                animationDelay: `${i * 0.08}s`,
              }}
            />
          ))}
        </div>
      )}

      <div className="relative">
        {isRecording && (
          <>
            <div className="phone-ring absolute inset-0 rounded-full" />
            <div className="phone-ring absolute inset-0 rounded-full" style={{ animationDelay: "0.6s" }} />
            <div className="phone-ring absolute inset-0 rounded-full" style={{ animationDelay: "1.2s" }} />
          </>
        )}
        <button
          onClick={isRecording ? stop : start}
          disabled={!isSupported || isTranscribing}
          aria-label={isRecording ? "停止录音" : "开始录音"}
          className={`relative w-20 h-20 rounded-full transition-all duration-200 flex items-center justify-center focus:outline-none ${
            status === "recording"
              ? "bg-red-600 hover:bg-red-500 text-white shadow-lg shadow-red-500/30"
              : status === "transcribing"
                ? "bg-amber-500 hover:bg-amber-400 text-white shadow-lg shadow-amber-500/30 animate-pulse"
                : status === "success"
                  ? "bg-emerald-500 text-white shadow-lg shadow-emerald-500/30"
                  : "bg-amber-500 hover:bg-amber-400 text-white shadow-lg shadow-amber-500/30"
          }`}
        >
          <MicIcon size={32} />
        </button>
      </div>

      <button
        onClick={onHangUp}
        className="flex items-center gap-2 px-6 py-3 bg-red-600 hover:bg-red-500 active:bg-red-700 text-white rounded-full text-sm font-medium transition-all duration-200 shadow-lg shadow-red-600/30"
        aria-label="挂断电话模式"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z" />
        </svg>
        挂断
      </button>
    </div>
  );
}
