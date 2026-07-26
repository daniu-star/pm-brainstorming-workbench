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

const SpeechRecognitionClass =
  (typeof window !== "undefined" &&
    ((window as any).SpeechRecognition || (window as any).webkitSpeechRecognition)) ||
  null;
const supportsWebSpeech = !!SpeechRecognitionClass;

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
  const [engine, setEngine] = useState<"web-speech" | "media-recorder" | null>(null);
  const [serverSTTAvailable, setServerSTTAvailable] = useState(true);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const durationTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const maxDurationTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const recognitionRef = useRef<any>(null);

  const supportsMediaRecording =
    typeof navigator !== "undefined" &&
    !!navigator.mediaDevices?.getUserMedia &&
    typeof MediaRecorder !== "undefined";
  const isSupported = supportsWebSpeech || supportsMediaRecording;

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
    return () => {
      clearTimers();
      if (recognitionRef.current) {
        try {
          recognitionRef.current.stop();
        } catch {
          // ignore
        }
        recognitionRef.current = null;
      }
    };
  }, [clearTimers]);

  useEffect(() => {
    const controller = new AbortController();
    fetch(apiUrl("/api/voice/capabilities"), {
      headers: getUserHeaders(),
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) throw new Error("capabilities unavailable");
        const data = await response.json();
        setServerSTTAvailable(Boolean(data.server_stt_available));
      })
      .catch((error) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setServerSTTAvailable(false);
      });
    return () => controller.abort();
  }, []);

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

    if (
      supportsWebSpeech &&
      SpeechRecognitionClass &&
      (!serverSTTAvailable || !supportsMediaRecording)
    ) {
      setEngine("web-speech");
      try {
        const recognition = new SpeechRecognitionClass();
        recognition.lang = "zh-CN";
        recognition.continuous = true;
        recognition.interimResults = true;

        recognition.onresult = (event: any) => {
          let final = "";
          for (let i = event.resultIndex; i < event.results.length; i++) {
            const transcript_part = event.results[i][0].transcript;
            if (event.results[i].isFinal) {
              final += transcript_part;
            }
          }
          if (final) {
            setTranscript((prev) => prev + final);
          }
        };

        recognition.onerror = (event: any) => {
          setIsRecording(false);
          setStatus("error");
          const errMsg = event?.error || "语音识别失败";
          if (errMsg === "not-allowed" || errMsg === "service-not-allowed") {
            const msg = notifyError("permission-denied");
            setErrorMessage(msg);
          } else if (errMsg === "network") {
            const msg = notifyError("network");
            setErrorMessage(msg);
          } else {
            const msg = notifyError("transcribe-failed", `语音识别错误: ${errMsg}`);
            setErrorMessage(msg);
          }
          recognitionRef.current = null;
        };

        recognition.onend = () => {
          setIsRecording(false);
          setStatus("idle");
          recognitionRef.current = null;
        };

        recognition.start();
        recognitionRef.current = recognition;
        setIsRecording(true);
        setStatus("recording");
      } catch (err) {
        const detail = err instanceof Error ? err.message : "无法启动语音识别";
        const msg = notifyError("transcribe-failed", detail);
        setErrorMessage(msg);
        setStatus("error");
      }
      return;
    }

    setEngine("media-recorder");
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
  }, [isSupported, clearTimers, serverSTTAvailable, supportsMediaRecording]);

  const stop = useCallback(() => {
    if (engine === "web-speech" && recognitionRef.current) {
      try {
        recognitionRef.current.stop();
      } catch {
        // ignore
      }
      setIsRecording(false);
      setStatus("success");
      setTimeout(() => setStatus("idle"), 2000);
      recognitionRef.current = null;
      return;
    }
    clearTimers();
    setRecordingDuration(0);
    if (
      mediaRecorderRef.current &&
      mediaRecorderRef.current.state === "recording"
    ) {
      mediaRecorderRef.current.stop();
    }
  }, [clearTimers, engine]);

  const reset = useCallback(() => {
    if (recognitionRef.current) {
      try {
        recognitionRef.current.stop();
      } catch {
        // ignore
      }
      recognitionRef.current = null;
    }
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
