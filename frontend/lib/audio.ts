"use client";

import { apiUrl } from "@/lib/api";

export async function playTTS(text: string): Promise<() => void> {
  let aborted = false;

  const res = await fetch(apiUrl("/api/voice/tts"), {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });

  if (!res.ok || aborted) {
    throw new Error("语音合成失败");
  }

  const blob = await res.blob();
  if (aborted) return () => {};

  const url = URL.createObjectURL(blob);
  const audio = new Audio(url);

  const cleanup = () => {
    audio.pause();
    audio.src = "";
    URL.revokeObjectURL(url);
  };

  audio.onended = cleanup;
  audio.onerror = cleanup;

  try {
    await audio.play();
  } catch (error) {
    // B084：主动 cleanup 释放资源；AbortError（被新播放/暂停打断）静默处理
    cleanup();
    if (error instanceof DOMException && error.name === "AbortError") {
      return () => {};
    }
    throw error;
  }

  return cleanup;
}
