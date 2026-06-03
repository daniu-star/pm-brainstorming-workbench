"use client";

import { useState, useRef, useCallback } from "react";
import { apiUrl } from "@/lib/api";
import { getUserHeaders } from "@/lib/user";

interface SpeechRecognitionResult {
  isRecording: boolean;
  isTranscribing: boolean;
  transcript: string;
  errorMessage: string;
  start: () => void;
  stop: () => void;
  reset: () => void;
  isSupported: boolean;
  status: "idle" | "recording" | "transcribing" | "success" | "error";
}

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

export function useSpeechRecognition(): SpeechRecognitionResult {
  const [isRecording, setIsRecording] = useState(false);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const [transcript, setTranscript] = useState("");
  const [errorMessage, setErrorMessage] = useState("");
  const [status, setStatus] = useState<"idle" | "recording" | "transcribing" | "success" | "error">("idle");

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);

  const isSupported = typeof window !== "undefined" && !!window.MediaRecorder;

  const start = useCallback(() => {
    if (!isSupported) {
      alert("您的浏览器不支持语音输入，请使用 Chrome 或 Edge 浏览器。");
      return;
    }
    setTranscript("");
    setErrorMessage("");
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
              setErrorMessage("未识别到语音内容，请重试");
              setStatus("error");
            }
          } catch (err) {
            const msg =
              err instanceof TypeError &&
              (err.message.includes("Failed to fetch") ||
                err.message.includes("NetworkError"))
                ? "无法连接到服务器，请检查网络连接"
                : err instanceof Error
                  ? err.message
                  : "语音识别失败，请重试";
            setErrorMessage(msg);
            setStatus("error");
          } finally {
            setIsTranscribing(false);
          }
        };

        recorder.onerror = () => {
          setIsRecording(false);
          setErrorMessage("录音过程中发生错误");
          setStatus("error");
          stream.getTracks().forEach((t) => t.stop());
          streamRef.current = null;
        };

        mediaRecorderRef.current = recorder;
        recorder.start();
        setIsRecording(true);
        setStatus("recording");
      })
      .catch((err) => {
        if (
          err instanceof DOMException &&
          (err.name === "NotAllowedError" || err.name === "PermissionDeniedError")
        ) {
          setErrorMessage("麦克风权限被拒绝，请在浏览器设置中允许");
          setStatus("error");
        } else {
          setErrorMessage("无法访问麦克风，请检查设备");
          setStatus("error");
        }
      });
  }, [isSupported]);

  const stop = useCallback(() => {
    if (
      mediaRecorderRef.current &&
      mediaRecorderRef.current.state === "recording"
    ) {
      mediaRecorderRef.current.stop();
    }
  }, []);

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
    start,
    stop,
    reset,
    isSupported,
    status,
  };
}
