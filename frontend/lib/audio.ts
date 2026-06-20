"use client";

import { apiUrl } from "@/lib/api";
import { getUserHeaders } from "@/lib/user";

function splitSentences(text: string): string[] {
  const parts = text.split(/([。！？；.!?;])/);
  const sentences: string[] = [];
  let buffer = "";
  for (let i = 0; i < parts.length; i++) {
    buffer += parts[i];
    if (/^[。！？；.!?;]$/.test(parts[i])) {
      const trimmed = buffer.trim();
      if (trimmed) sentences.push(trimmed);
      buffer = "";
    }
  }
  const remaining = buffer.trim();
  if (remaining) sentences.push(remaining);
  return sentences;
}

async function playAudioBlob(blob: Blob, signal?: AbortSignal): Promise<void> {
  const url = URL.createObjectURL(blob);
  const audio = new Audio(url);

  return new Promise<void>((resolve, reject) => {
    const cleanup = () => {
      audio.pause();
      audio.src = "";
      URL.revokeObjectURL(url);
    };

    const onAbort = () => {
      cleanup();
      reject(new DOMException("Aborted", "AbortError"));
    };

    signal?.addEventListener("abort", onAbort, { once: true });

    audio.onended = () => {
      signal?.removeEventListener("abort", onAbort);
      cleanup();
      resolve();
    };

    audio.onerror = () => {
      signal?.removeEventListener("abort", onAbort);
      cleanup();
      reject(new Error("音频播放失败"));
    };

    audio.play().catch((err) => {
      signal?.removeEventListener("abort", onAbort);
      cleanup();
      reject(err);
    });
  });
}

export async function playTTS(text: string, signal?: AbortSignal): Promise<() => void> {
  const res = await fetch(apiUrl("/api/voice/tts"), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getUserHeaders() },
    body: JSON.stringify({ text }),
    signal,
  });

  if (!res.ok) {
    throw new Error("语音合成失败");
  }

  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const audio = new Audio(url);

  let rejectPlay: ((err: Error) => void) | null = null;
  const errorPromise = new Promise<never>((_, reject) => {
    rejectPlay = reject;
  });

  const cleanup = () => {
    audio.pause();
    audio.src = "";
    URL.revokeObjectURL(url);
  };

  audio.onended = cleanup;
  audio.onerror = () => {
    cleanup();
    rejectPlay?.(new Error("音频播放失败"));
  };

  const playPromise = audio.play();

  await Promise.race([playPromise, errorPromise]);

  return cleanup;
}

export async function playTTSStream(
  text: string,
  onSentenceStart?: (sentence: string) => void,
  signal?: AbortSignal
): Promise<() => void> {
  let stopped = false;
  const controller = new AbortController();
  const combinedSignal = signal
    ? AbortSignal.any([signal, controller.signal])
    : controller.signal;

  if (signal?.aborted) {
    controller.abort();
  }

  const sentences = splitSentences(text);
  const cleanupFns: (() => void)[] = [];

  const streamPromise = (async () => {
    for (const sentence of sentences) {
      if (stopped || combinedSignal.aborted) break;

      onSentenceStart?.(sentence);

      const res = await fetch(apiUrl("/api/voice/tts?voice=interviewer"), {
        method: "POST",
        headers: { "Content-Type": "application/json", ...getUserHeaders() },
        body: JSON.stringify({ text: sentence }),
        signal: combinedSignal,
      });

      if (!res.ok) {
        throw new Error("语音合成失败");
      }

      const blob = await res.blob();

      if (stopped || combinedSignal.aborted) break;

      await playAudioBlob(blob, combinedSignal);
    }
  })();

  const cleanup = () => {
    stopped = true;
    controller.abort();
  };

  streamPromise.catch(() => {});

  return cleanup;
}

// ===== 面试官专用 TTS（独立端点，语速 -5%、音调 -2%）=====

const activeAudioControllers = new Set<AbortController>();

export async function playInterviewerTTS(
  text: string,
  onSentenceStart?: (sentence: string) => void,
  signal?: AbortSignal
): Promise<() => void> {
  let stopped = false;
  const controller = new AbortController();
  activeAudioControllers.add(controller);

  const combinedSignal = signal
    ? AbortSignal.any([signal, controller.signal])
    : controller.signal;

  if (signal?.aborted) {
    controller.abort();
  }

  const sentences = splitSentences(text);

  const streamPromise = (async () => {
    for (const sentence of sentences) {
      if (stopped || combinedSignal.aborted) break;

      onSentenceStart?.(sentence);

      const res = await fetch(apiUrl("/api/voice/tts/interviewer"), {
        method: "POST",
        headers: { "Content-Type": "application/json", ...getUserHeaders() },
        body: JSON.stringify({ text: sentence }),
        signal: combinedSignal,
      });

      if (!res.ok) {
        throw new Error("语音合成失败");
      }

      const blob = await res.blob();

      if (stopped || combinedSignal.aborted) break;

      await playAudioBlob(blob, combinedSignal);
    }
  })();

  streamPromise.finally(() => {
    activeAudioControllers.delete(controller);
  });

  const cleanup = () => {
    stopped = true;
    controller.abort();
    activeAudioControllers.delete(controller);
  };

  streamPromise.catch(() => {});

  return cleanup;
}

export function stopAllAudio(): void {
  for (const controller of activeAudioControllers) {
    controller.abort();
  }
  activeAudioControllers.clear();
}
