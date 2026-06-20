"use client";

import { useState, useRef, useCallback, useEffect } from "react";
import { apiUrl } from "@/lib/api";
import { getUserHeaders } from "@/lib/user";
import { toast } from "@/components/Toast";

type STTErrorType = "permission-denied" | "network" | "unsupported" | "transcribe-failed";

interface SpeechRecognitionResult {
  isRecording: boolean;
  isTranscribing: boolean;
  transcript: string;
  errorMessage: string;
  recordingDuration: number;
  start: () => void;
  stop: () => void;
  reset: () => void;
  isSupported: boolean;
  status: "idle" | "recording" | "transcribing" | "success" | "error";
}

const MAX_DURATION = 60;

function getSupportedMimeType(): string | null {
  if (typeof window === "undefined") return null;
  const types = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/mp4",
    "audio/wav",
    "audio/ogg;codecs=opus",
  ];
  for (const type of types) {
    if (MediaRecorder.isTypeSupported(type)) return type;
  }
  return null;
}

function notifyError(type: STTErrorType, detail?: string): string {
  const messages: Record<STTErrorType, { title: string; description: string }> = {
    "permission-denied": {
      title: "麦克风权限被拒绝",
      description: "请在浏览器设置中允许麦克风访问后重试",
    },
    network: {
      title: "网络连接失败",
      description: "无法连接到语音识别服务，请检查网络连接",
    },
    unsupported: {
      title: "不支持语音输入",
      description: "请使用 Chrome 或 Edge 浏览器以获得最佳体验",
    },
    "transcribe-failed": {
      title: "语音识别失败",
      description: detail || "未识别到语音内容，请重试",
    },
  };
  const msg = messages[type];
  toast({ title: msg.title, description: msg.description, variant: "destructive" });
  return msg.description;
}

export function useSpeechRecognition(): SpeechRecognitionResult {
  const [isRecording, setIsRecording] = useState(false);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const [transcript, setTranscript] = useState("");
  const [errorMessage, setErrorMessage] = useState("");
  const [recordingDuration, setRecordingDuration] = useState(0);
  const [status, setStatus] = useState<"idle" | "recording" | "transcribing" | "success" | "error">("idle");

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const durationTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const maxDurationTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const isSupported =
    typeof window !== "undefined" &&
    !!navigator.mediaDevices?.getUserMedia &&
    typeof MediaRecorder !== "undefined";

  const clearTimers = useCallback(() => {
    if (durationTimerRef.current) {
      clearInterval(durationTimerRef.current);
      durationTimerRef.current = null;
    }
    if (maxDurationTimerRef.current) {
      clearTimeout(maxDurationTimerRef.current);
      maxDurationTimerRef.current = null;
    }
  }, []);

  useEffect(() => {
    return () => clearTimers();
  }, [clearTimers]);

  const start = useCallback(() => {
    if (!isSupported) {
      const msg = notifyError("unsupported");
      setErrorMessage(msg);
      setStatus("error");
      return;
    }
    setTranscript("");
    setErrorMessage("");
    setRecordingDuration(0);
    chunksRef.current = [];

    const mimeType = getSupportedMimeType();

    navigator.mediaDevices
      .getUserMedia({ audio: true })
      .then((stream) => {
        streamRef.current = stream;
        const options: MediaRecorderOptions = {};
        if (mimeType) options.mimeType = mimeType;
        const recorder = new MediaRecorder(stream, options);

        recorder.ondataavailable = (e) => {
          if (e.data.size > 0) {
            chunksRef.current.push(e.data);
          }
        };

        recorder.onstop = async () => {
          setIsRecording(false);
          setStatus("transcribing");
          clearTimers();
          setRecordingDuration(0);

          const blob = new Blob(chunksRef.current, {
            type: mimeType || "audio/webm",
          });
          chunksRef.current = [];

          stream.getTracks().forEach((t) => t.stop());
          streamRef.current = null;

          setIsTranscribing(true);
          try {
            const formData = new FormData();
            const ext = mimeType?.includes("wav") ? "wav" : mimeType?.includes("mp4") ? "mp4" : mimeType?.includes("ogg") ? "ogg" : "webm";
            formData.append("file", blob, `recording.${ext}`);

            const res = await fetch(apiUrl("/api/voice/stt"), {
              method: "POST",
              headers: getUserHeaders(),
              body: formData,
            });

            if (!res.ok) {
              const errorData = await res.json().catch(() => ({}));
              throw new Error(errorData.detail || `语音识别请求失败 (${res.status})`);
            }

            const data = await res.json();
            if (data.text) {
              setTranscript(data.text);
              setStatus("success");
              setTimeout(() => setStatus("idle"), 2000);
            } else {
              const msg = notifyError("transcribe-failed", "未识别到语音内容，请重试");
              setErrorMessage(msg);
              setStatus("error");
            }
          } catch (err) {
            const isNetwork =
              err instanceof TypeError &&
              (err.message.includes("Failed to fetch") ||
                err.message.includes("NetworkError"));
            const detail = err instanceof Error ? err.message : "语音识别失败，请重试";
            const msg = isNetwork
              ? notifyError("network")
              : notifyError("transcribe-failed", detail);
            setErrorMessage(msg);
            setStatus("error");
          } finally {
            setIsTranscribing(false);
          }
        };

        recorder.onerror = () => {
          setIsRecording(false);
          clearTimers();
          setRecordingDuration(0);
          const msg = notifyError("transcribe-failed", "录音过程中发生错误");
          setErrorMessage(msg);
          setStatus("error");
          stream.getTracks().forEach((t) => t.stop());
          streamRef.current = null;
        };

        mediaRecorderRef.current = recorder;
        recorder.start();
        setIsRecording(true);
        setStatus("recording");

        durationTimerRef.current = setInterval(() => {
          setRecordingDuration((d) => d + 1);
        }, 1000);

        maxDurationTimerRef.current = setTimeout(() => {
          if (
            mediaRecorderRef.current &&
            mediaRecorderRef.current.state === "recording"
          ) {
            mediaRecorderRef.current.stop();
          }
        }, MAX_DURATION * 1000);
      })
      .catch((err) => {
        if (
          err instanceof DOMException &&
          (err.name === "NotAllowedError" || err.name === "PermissionDeniedError")
        ) {
          const msg = notifyError("permission-denied");
          setErrorMessage(msg);
          setStatus("error");
        } else {
          const msg = notifyError("transcribe-failed", "无法访问麦克风，请检查设备");
          setErrorMessage(msg);
          setStatus("error");
        }
      });
  }, [isSupported, clearTimers]);

  const stop = useCallback(() => {
    clearTimers();
    setRecordingDuration(0);
    if (
      mediaRecorderRef.current &&
      mediaRecorderRef.current.state === "recording"
    ) {
      mediaRecorderRef.current.stop();
    }
  }, [clearTimers]);

  const reset = useCallback(() => {
    setTranscript("");
    setErrorMessage("");
    setStatus("idle");
  }, []);

  return {
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
  };
}
