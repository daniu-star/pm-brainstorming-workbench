"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import { Send, Mic, Square, PhoneOff, AlertCircle } from "lucide-react";
import { useSessionStore } from "@/store/sessionStore";
import { useSpeechRecognition } from "@/hooks/useSpeechRecognition";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const INTERVIEWER_AVATAR = "/avatars/interviewer-business.svg";

function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

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
    recordingDuration,
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
        recordingDuration={recordingDuration}
        start={start}
        stop={stop}
        onHangUp={onTogglePhoneMode}
        status={status}
      />
    );
  }

  if (interviewMode === "text") {
    return (
      <div className="px-4 py-3 border-t border-border bg-card">
        <div className="flex gap-2 items-end">
          <div className="flex-1 relative">
            <Textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="回答面试官的问题..."
              rows={3}
              aria-label="回答面试官问题"
              className="resize-none"
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  handleTextSend();
                }
              }}
            />
          </div>
          <Button
            onClick={handleTextSend}
            disabled={!input.trim() || isStreaming}
            aria-label="发送回答"
            size="icon"
            className="h-12 w-12 shrink-0"
          >
            <Send className="h-4 w-4" />
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="px-4 py-3 border-t border-border bg-card">
      <VoiceInputView
        isRecording={isRecording}
        isTranscribing={isTranscribing}
        transcript={transcript}
        errorMessage={errorMessage}
        isSupported={isSupported}
        isStreaming={isStreaming}
        recordingDuration={recordingDuration}
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
  recordingDuration,
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
  recordingDuration: number;
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
        <div className="h-16 w-16 rounded-full bg-muted flex items-center justify-center opacity-40">
          <Mic className="h-7 w-7" />
        </div>
        <p className="text-sm text-muted-foreground">您的浏览器不支持语音输入</p>
      </div>
    );
  }

  if (!isActive) {
    return (
      <div className="flex flex-col items-center gap-4 py-4">
        <div className="mic-pulse relative">
          <Button
            onClick={start}
            aria-label="开始录音"
            variant="secondary"
            size="icon"
            className="h-16 w-16 rounded-full"
          >
            <Mic className="h-7 w-7" />
          </Button>
        </div>
        <div className="flex flex-col items-center gap-1.5">
          <p className="text-sm text-foreground">点击麦克风开始语音对话</p>
          <p className="text-xs text-muted-foreground">语音识别将自动转为文字</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-center gap-3">
      {status === "recording" && (
        <div className="flex items-center gap-2">
          <Badge variant="destructive" className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-destructive-foreground animate-pulse" />
            录音中 {formatDuration(recordingDuration)}
          </Badge>
          <span className="flex items-center gap-0.5 ml-1">
            {Array.from({ length: 5 }).map((_, i) => (
              <span
                key={i}
                className="phone-waveform-bar inline-block w-0.5 bg-primary rounded-full"
                style={{ animationDelay: `${i * 0.15}s` }}
              />
            ))}
          </span>
        </div>
      )}
      {status === "transcribing" && (
        <Badge variant="secondary" className="flex items-center gap-1.5">
          <Square className="h-3 w-3 animate-pulse" />
          转写中...
        </Badge>
      )}
      {status === "success" && (
        <Badge variant="default" className="flex items-center gap-1.5">
          识别成功
        </Badge>
      )}
      {status === "error" && (
        <div className="flex flex-col items-center gap-2 w-full">
          <Badge variant="destructive" className="flex items-center gap-1.5">
            <AlertCircle className="h-3 w-3" />
            {errorMessage}
          </Badge>
          <div className="flex items-center gap-3">
            <Button onClick={start} variant="outline" size="sm">
              重试
            </Button>
            <button onClick={reset} className="text-xs text-muted-foreground hover:text-foreground transition-colors underline">
              切换到文字模式
            </button>
          </div>
        </div>
      )}
      {transcript && status !== "error" && (
        <div className="w-full bg-muted border border-border rounded-xl px-4 py-2.5 text-sm text-foreground min-h-[40px]">
          {transcript}
        </div>
      )}

      <div className="flex items-center gap-4">
        <div className={status === "recording" ? "mic-ripple relative" : ""}>
          <Button
            onClick={isRecording ? stop : start}
            disabled={isTranscribing}
            aria-label={isRecording ? "停止录音" : "开始录音"}
            size="icon"
            className={cn(
              "h-16 w-16 rounded-full",
              status === "recording" && "bg-destructive hover:bg-destructive/90",
              status === "transcribing" && "bg-primary animate-pulse",
              status === "success" && "bg-primary",
              status === "idle" && "mic-pulse"
            )}
          >
            {isRecording ? <Square className="h-6 w-6" /> : <Mic className="h-7 w-7" />}
          </Button>
        </div>

        {transcript && status !== "error" && (
          <Button
            onClick={onSend}
            disabled={isStreaming}
            aria-label="发送语音转录"
            size="icon"
            className="h-12 w-12 shrink-0"
          >
            <Send className="h-4 w-4" />
          </Button>
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
  recordingDuration,
  start,
  stop,
  onHangUp,
  status,
}: {
  isRecording: boolean;
  isTranscribing: boolean;
  isSupported: boolean;
  errorMessage: string;
  recordingDuration: number;
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
    <div className="px-4 py-8 bg-muted/30 flex flex-col items-center gap-6">
      <div className="flex flex-col items-center gap-2">
        <div className="relative">
          {isRecording && (
            <span className="phone-ring absolute inset-0 rounded-full" />
          )}
          <div className="h-16 w-16 rounded-full overflow-hidden border-2 border-primary/30 shadow-md">
            <img
              src={INTERVIEWER_AVATAR}
              alt="AI 面试官"
              className="w-full h-full object-cover"
            />
          </div>
        </div>
        <span className="text-sm font-medium text-foreground">AI 压力面试官</span>
      </div>

      <div className="h-8 flex items-center justify-center">
        {status === "recording" && (
          <Badge variant="destructive" className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-destructive-foreground animate-pulse" />
            正在聆听 {formatDuration(recordingDuration)}
          </Badge>
        )}
        {status === "transcribing" && (
          <Badge variant="secondary" className="flex items-center gap-1.5">
            <Square className="h-3 w-3 animate-pulse" />
            正在识别...
          </Badge>
        )}
        {status === "success" && (
          <Badge variant="default">识别成功</Badge>
        )}
        {status === "error" && !isRecording && (
          <div className="flex flex-col items-center gap-2">
            <Badge variant="destructive" className="flex items-center gap-1.5">
              <AlertCircle className="h-3 w-3" />
              {errorMessage}
            </Badge>
            <Button onClick={start} variant="destructive" size="sm" className="rounded-full">
              重试
            </Button>
          </div>
        )}
      </div>

      {isRecording && (
        <div className="phone-waveform flex items-center gap-1 h-8">
          {Array.from({ length: 12 }).map((_, i) => (
            <div
              key={i}
              className="phone-waveform-bar w-1 bg-primary rounded-full"
              style={{ animationDelay: `${i * 0.08}s` }}
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
        <Button
          onClick={isRecording ? stop : start}
          disabled={!isSupported || isTranscribing}
          aria-label={isRecording ? "停止录音" : "开始录音"}
          size="icon"
          className={cn(
            "relative h-20 w-20 rounded-full",
            status === "recording" && "bg-destructive hover:bg-destructive/90 shadow-md shadow-destructive/30",
            status === "transcribing" && "bg-primary animate-pulse shadow-md shadow-primary/30",
            status === "success" && "bg-primary shadow-md shadow-primary/30",
            status === "idle" && "bg-primary hover:bg-primary/90 shadow-md shadow-primary/30"
          )}
        >
          {isRecording ? <Square className="h-8 w-8" /> : <Mic className="h-8 w-8" />}
        </Button>
      </div>

      <Button
        onClick={onHangUp}
        variant="destructive"
        className="flex items-center gap-2 rounded-full"
        aria-label="挂断电话模式"
      >
        <PhoneOff className="h-4 w-4" />
        挂断
      </Button>
    </div>
  );
}
