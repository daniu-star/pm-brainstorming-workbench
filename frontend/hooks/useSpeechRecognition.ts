"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { apiUrl } from "@/lib/api";

export type SpeechStatus = "idle" | "requesting" | "listening" | "processing" | "error";
export type SpeechEngine = "server" | "browser";

interface VoiceCapabilities {
  stt_enabled: boolean;
  stt_model: string | null;
  max_audio_bytes: number;
  max_recording_seconds: number;
}

interface STTResponse {
  text: string;
  engine: "server";
}

interface SpeechRecognitionResult {
  isRecording: boolean;
  transcript: string;
  interim: string;
  /** B083：距自动结束剩余秒数（仅服务端录音且剩余 ≤ 录音上限时有效，其余为 null） */
  remainingSeconds: number | null;
  start: () => Promise<void>;
  stop: () => void;
  reset: () => void;
  isSupported: boolean;
  status: SpeechStatus;
  error: string | null;
  engine: SpeechEngine | null;
}

const ERROR_MESSAGES: Record<string, string> = {
  "not-allowed": "麦克风权限被拒绝。请在浏览器地址栏中允许麦克风访问后重试。",
  "service-not-allowed": "浏览器语音识别服务不可用，请确认使用 Chrome 或 Edge 并保持网络连接。",
  "audio-capture": "未检测到可用麦克风，请检查系统输入设备。",
  network: "语音识别网络连接中断，请检查网络后重试。",
  "no-speech": "没有检测到清晰语音，请靠近麦克风后重试。",
  aborted: "语音识别已停止。",
};

let capabilitiesPromise: Promise<VoiceCapabilities | null> | null = null;

async function loadVoiceCapabilities(): Promise<VoiceCapabilities | null> {
  if (capabilitiesPromise) return capabilitiesPromise;

  capabilitiesPromise = (async () => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 3000);

    try {
      const response = await fetch(apiUrl("/api/voice/capabilities"), {
        credentials: "include",
        signal: controller.signal,
      });
      if (!response.ok) return null;
      return (await response.json()) as VoiceCapabilities;
    } catch {
      return null;
    } finally {
      clearTimeout(timeout);
    }
  })();

  return capabilitiesPromise;
}

function selectRecordingMimeType(): string | undefined {
  if (typeof MediaRecorder === "undefined") return undefined;

  return [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/mp4",
    "audio/ogg;codecs=opus",
  ].find((mimeType) => MediaRecorder.isTypeSupported(mimeType));
}

/** B083：capabilities 读不到 max_recording_seconds 时的默认单次录音上限。 */
const DEFAULT_MAX_RECORDING_SECONDS = 180;

export function useSpeechRecognition(): SpeechRecognitionResult {
  const [isRecording, setIsRecording] = useState(false);
  const [transcript, setTranscript] = useState("");
  const [interim, setInterim] = useState("");
  const [remainingSeconds, setRemainingSeconds] = useState<number | null>(null);
  const [status, setStatus] = useState<SpeechStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  const [engine, setEngine] = useState<SpeechEngine | null>(null);

  const recognitionRef = useRef<SpeechRecognition | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const recordingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const countdownTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const shouldListenRef = useRef(false);
  const restartCountRef = useRef(0);
  const restartTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const hasBrowserRecognition =
    typeof window !== "undefined" && Boolean(window.SpeechRecognition || window.webkitSpeechRecognition);
  const hasMediaRecorder =
    typeof window !== "undefined" &&
    typeof window.MediaRecorder !== "undefined" &&
    Boolean(navigator.mediaDevices?.getUserMedia);
  const isSupported = hasBrowserRecognition || hasMediaRecorder;

  const stopMediaTracks = useCallback(() => {
    mediaStreamRef.current?.getTracks().forEach((track) => track.stop());
    mediaStreamRef.current = null;
  }, []);

  const clearRecordingTimers = useCallback(() => {
    if (recordingTimerRef.current) {
      clearTimeout(recordingTimerRef.current);
      recordingTimerRef.current = null;
    }
    if (countdownTimerRef.current) {
      clearInterval(countdownTimerRef.current);
      countdownTimerRef.current = null;
    }
    setRemainingSeconds(null);
  }, []);

  const uploadRecording = useCallback(async (blob: Blob) => {
    if (!blob.size) {
      setStatus("error");
      setError("没有采集到录音内容，请检查麦克风后重试。");
      return;
    }

    setStatus("processing");
    const extension = blob.type.includes("mp4") ? "mp4" : blob.type.includes("ogg") ? "ogg" : "webm";
    const formData = new FormData();
    formData.append("file", blob, `recording.${extension}`);

    try {
      const response = await fetch(apiUrl("/api/voice/stt"), {
        method: "POST",
        credentials: "include",
        body: formData,
      });
      const payload = (await response.json().catch(() => null)) as
        | STTResponse
        | { detail?: string }
        | null;

      if (!response.ok) {
        const detail = payload && "detail" in payload ? payload.detail : null;
        throw new Error(detail || "服务端语音识别失败");
      }

      const text = payload && "text" in payload ? payload.text.trim() : "";
      if (!text) throw new Error("没有识别到清晰语音");

      setTranscript(text);
      setError(null);
      setStatus("idle");
    } catch (uploadError) {
      setStatus("error");
      setError(uploadError instanceof Error ? uploadError.message : "服务端语音识别失败");
    }
  }, []);

  const startServerRecording = useCallback(
    (stream: MediaStream, capabilities: VoiceCapabilities) => {
      const mimeType = selectRecordingMimeType();

      try {
        const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
        mediaRecorderRef.current = recorder;
        mediaStreamRef.current = stream;
        audioChunksRef.current = [];
        setEngine("server");

        recorder.ondataavailable = (event) => {
          if (event.data.size > 0) audioChunksRef.current.push(event.data);
        };

        recorder.onerror = () => {
          setIsRecording(false);
          setStatus("error");
          setError("录音采集失败，请检查麦克风设备。");
          clearRecordingTimers();
          stopMediaTracks();
        };

        recorder.onstop = () => {
          clearRecordingTimers();

          const recordedType = recorder.mimeType || mimeType || "audio/webm";
          const blob = new Blob(audioChunksRef.current, { type: recordedType });
          audioChunksRef.current = [];
          mediaRecorderRef.current = null;
          setIsRecording(false);
          stopMediaTracks();
          void uploadRecording(blob);
        };

        recorder.start(250);
        setIsRecording(true);
        setStatus("listening");
        setError(null);

        // B083：上限从 capabilities 读取，读不到时默认 180s
        const maxSeconds =
          capabilities.max_recording_seconds > 0
            ? capabilities.max_recording_seconds
            : DEFAULT_MAX_RECORDING_SECONDS;
        const startedAt = Date.now();
        setRemainingSeconds(maxSeconds);

        countdownTimerRef.current = setInterval(() => {
          const elapsed = (Date.now() - startedAt) / 1000;
          const left = Math.max(0, Math.ceil(maxSeconds - elapsed));
          setRemainingSeconds(left);
          if (left <= 0 && recorder.state === "recording") recorder.stop();
        }, 1000);

        recordingTimerRef.current = setTimeout(() => {
          if (recorder.state === "recording") recorder.stop();
        }, maxSeconds * 1000);
      } catch {
        stopMediaTracks();
        throw new Error("当前浏览器无法创建录音，请改用 Chrome 或 Edge。");
      }
    },
    [clearRecordingTimers, stopMediaTracks, uploadRecording]
  );

  const createAndStartBrowserRecognition = useCallback(() => {
    const SpeechRecognitionConstructor = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognitionConstructor) {
      setStatus("error");
      setError("当前浏览器不支持浏览器语音识别，且服务端 STT 尚未配置。");
      return;
    }

    const recognition = new SpeechRecognitionConstructor();
    recognition.lang = "zh-CN";
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;

    recognition.onstart = () => {
      restartCountRef.current = 0;
      setEngine("browser");
      setIsRecording(true);
      setStatus("listening");
      setError(null);
    };

    recognition.onresult = (event) => {
      let finalText = "";
      let interimText = "";

      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const item = event.results[index];
        if (item.isFinal) finalText += item[0].transcript;
        else interimText += item[0].transcript;
      }

      if (finalText) setTranscript((current) => current + finalText);
      setInterim(interimText);
    };

    recognition.onerror = (event) => {
      const isRecoverable = event.error === "no-speech" || event.error === "aborted";
      setIsRecording(false);

      if (!isRecoverable) {
        shouldListenRef.current = false;
        setStatus("error");
        setError(ERROR_MESSAGES[event.error] || `语音识别失败：${event.error}`);
      }
    };

    recognition.onend = () => {
      recognitionRef.current = null;
      setIsRecording(false);
      setInterim("");

      if (shouldListenRef.current && restartCountRef.current < 2) {
        restartCountRef.current += 1;
        restartTimerRef.current = setTimeout(createAndStartBrowserRecognition, 250);
        return;
      }

      shouldListenRef.current = false;
      setStatus((current) => (current === "error" ? current : "idle"));
    };

    recognitionRef.current = recognition;

    try {
      recognition.start();
    } catch {
      shouldListenRef.current = false;
      setStatus("error");
      setError("浏览器语音识别启动失败，请稍候重试。");
    }
  }, []);

  const start = useCallback(async () => {
    if (!isSupported || isRecording || status === "requesting" || status === "processing") return;

    setError(null);
    setTranscript("");
    setInterim("");
    setEngine(null);
    setStatus("requesting");

    try {
      if (!navigator.mediaDevices?.getUserMedia) {
        if (!hasBrowserRecognition) {
          throw new Error("当前环境无法访问麦克风，请使用 HTTPS 或 localhost。");
        }
        shouldListenRef.current = true;
        restartCountRef.current = 0;
        createAndStartBrowserRecognition();
        return;
      }

      // B045/67/68：开启回声消除/噪声抑制/自动增益，避免 TTS 回声进入 STT
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      const capabilities = await loadVoiceCapabilities();

      if (capabilities?.stt_enabled && hasMediaRecorder) {
        startServerRecording(stream, capabilities);
        return;
      }

      stream.getTracks().forEach((track) => track.stop());
      shouldListenRef.current = true;
      restartCountRef.current = 0;
      createAndStartBrowserRecognition();
    } catch (startError) {
      shouldListenRef.current = false;
      stopMediaTracks();
      setStatus("error");
      setError(
        startError instanceof DOMException && startError.name === "NotAllowedError"
          ? ERROR_MESSAGES["not-allowed"]
          : startError instanceof Error
            ? startError.message
            : "无法访问麦克风，请检查浏览器权限和系统输入设备。"
      );
    }
  }, [
    createAndStartBrowserRecognition,
    hasBrowserRecognition,
    hasMediaRecorder,
    isRecording,
    isSupported,
    startServerRecording,
    status,
    stopMediaTracks,
  ]);

  const stop = useCallback(() => {
    if (engine === "server") {
      if (mediaRecorderRef.current?.state === "recording") {
        mediaRecorderRef.current.stop();
      }
      return;
    }

    shouldListenRef.current = false;
    if (restartTimerRef.current) {
      clearTimeout(restartTimerRef.current);
      restartTimerRef.current = null;
    }
    setStatus("processing");
    recognitionRef.current?.stop();
    setIsRecording(false);
  }, [engine]);

  const reset = useCallback(() => {
    setTranscript("");
    setInterim("");
    setError(null);
    setEngine(null);
    setStatus("idle");
  }, []);

  useEffect(() => {
    return () => {
      shouldListenRef.current = false;
      if (restartTimerRef.current) clearTimeout(restartTimerRef.current);
      if (recordingTimerRef.current) clearTimeout(recordingTimerRef.current);
      if (countdownTimerRef.current) clearInterval(countdownTimerRef.current);
      recognitionRef.current?.abort();
      if (mediaRecorderRef.current?.state === "recording") {
        mediaRecorderRef.current.onstop = null;
        mediaRecorderRef.current.stop();
      }
      mediaStreamRef.current?.getTracks().forEach((track) => track.stop());
    };
  }, []);

  return {
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
  };
}
