"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { BrainIcon, MicIcon, ShieldIcon, SignalIcon } from "@/components/icons";
import { useSessionStore } from "@/store/sessionStore";
import { MessageList } from "@/components/chat/MessageList";
import { playTTS } from "@/lib/audio";
import { apiUrl } from "@/lib/api";
import type { ProctorSession } from "@/lib/proctor-gaze/types";
import { InterviewHeader } from "./InterviewHeader";
import { InterviewInput } from "./InterviewInput";
import { InterviewCamera } from "./InterviewCamera";

/** B008：维度 key 与后端 dimensions_update 事件对齐 */
const AUDIT_DIMENSIONS: { key: string; label: string }[] = [
  { key: "problem_validity", label: "问题证据" },
  { key: "solution_effectiveness", label: "方案有效性" },
  { key: "technical_risk", label: "技术风险" },
  { key: "business_loop", label: "商业闭环" },
  { key: "user_adoption", label: "用户采用" },
  { key: "execution_risk", label: "执行风险" },
];

type LatencyTone = "good" | "fair" | "poor" | "down";

const LATENCY_TONE_STYLES: Record<LatencyTone, string> = {
  good: "text-emerald-300",
  fair: "text-amber-300",
  poor: "text-red-300",
  down: "text-red-300",
};

export function InterviewView({
  /** B007/B048：是否渲染摄像头（用户同意摄像头且未被跳过） */
  showCamera = false,
  /** B048：摄像头失败后降级为仅文字面试 */
  onSkipCamera,
  /** B041：接收 proctor session 供结束面试导出 */
  onProctorSession,
  /** B041/B087：结束面试 */
  onEndInterview,
}: {
  showCamera?: boolean;
  onSkipCamera?: () => void;
  onProctorSession?: (session: ProctorSession | null) => void;
  onEndInterview?: () => void;
} = {}) {
  const { messages, interviewMode, isStreaming, setPlayingAudio, isPlayingAudio } = useSessionStore();
  const coveredDimensions = useSessionStore((s) => s.coveredDimensions);

  const lastMessageRef = useRef<string | null>(null);
  // B024：保存当前 TTS cleanup，新消息/切模式/卸载前调用
  const ttsCleanupRef = useRef<(() => void) | null>(null);
  const mountedRef = useRef(true);
  const [ttsNotice, setTtsNotice] = useState<string | null>(null);
  const [speakingText, setSpeakingText] = useState<string | null>(null);

  // B082：真实 RTT 分档显示
  const [latency, setLatency] = useState<{ label: string; tone: LatencyTone }>({
    label: "连接检测中…",
    tone: "good",
  });

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      // B024：卸载时停止音频并释放资源
      ttsCleanupRef.current?.();
      ttsCleanupRef.current = null;
    };
  }, []);

  // B024/B075：模式切换（voice→text）时停止播放
  useEffect(() => {
    if (interviewMode !== "voice") {
      ttsCleanupRef.current?.();
      ttsCleanupRef.current = null;
    }
  }, [interviewMode]);

  useEffect(() => {
    let cancelled = false;
    const timer = setInterval(() => void measure(), 30_000);

    async function measure() {
      try {
        const startedAt = performance.now();
        const res = await fetch(apiUrl("/api/voice/capabilities"), {
          credentials: "include",
        });
        if (cancelled) return;
        if (!res.ok) {
          setLatency({ label: "连接中断", tone: "down" });
          return;
        }
        const ms = Math.round(performance.now() - startedAt);
        if (ms > 300) setLatency({ label: `网络较差 · ${ms}ms`, tone: "poor" });
        else if (ms >= 100) setLatency({ label: `连接一般 · ${ms}ms`, tone: "fair" });
        else setLatency({ label: `连接稳定 · ${ms}ms`, tone: "good" });
      } catch {
        if (!cancelled) setLatency({ label: "连接中断", tone: "down" });
      }
    }

    void measure();
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  // B008：真实覆盖维度（store 由 dimensions_update 事件驱动）
  const auditProgress = useMemo(
    () => Math.min(AUDIT_DIMENSIONS.length, coveredDimensions.length),
    [coveredDimensions],
  );

  // B024：TTS 生命周期——新消息先停旧音频
  useEffect(() => {
    if (interviewMode !== "voice" || isStreaming) return;

    const lastAiMessage = [...messages].reverse().find((message) => message.role === "assistant");
    if (!lastAiMessage) return;

    const key = lastAiMessage.content.slice(0, 100);
    if (key === lastMessageRef.current || isPlayingAudio) return;
    lastMessageRef.current = key;

    ttsCleanupRef.current?.();
    ttsCleanupRef.current = null;
    setTtsNotice(null);
    setSpeakingText(lastAiMessage.content);
    setPlayingAudio(true);

    playTTS(lastAiMessage.content)
      .then((cleanup) => {
        if (!mountedRef.current) {
          cleanup();
          return;
        }
        ttsCleanupRef.current = cleanup;
      })
      .catch(() => {
        // B046：失败时提示读字（一次性）
        if (mountedRef.current) setTtsNotice("语音播放失败，请阅读文字");
      })
      .finally(() => {
        if (mountedRef.current) setPlayingAudio(false);
      });
  }, [messages, interviewMode, isStreaming, isPlayingAudio, setPlayingAudio]);

  return (
    <div className="flex h-dvh flex-col bg-[#05080c]">
      <InterviewHeader onEndInterview={onEndInterview} />

      <div className="grid min-h-0 flex-1 lg:grid-cols-[minmax(0,1fr)_420px]">
        <section className="audit-stage flex min-h-[430px] flex-col border-b border-white/10 lg:border-b-0 lg:border-r">
          <div className="relative z-10 flex items-center justify-between px-5 py-4">
            <div className="flex items-center gap-2 text-[10px] text-zinc-500">
              <SignalIcon size={12} className={LATENCY_TONE_STYLES[latency.tone]} />
              {latency.label}
            </div>
            <div className="flex items-center gap-2 rounded-full border border-red-300/20 bg-red-300/5 px-2.5 py-1 text-[10px] text-red-200">
              <span className="size-1.5 animate-pulse rounded-full bg-red-300" />
              审计进行中
            </div>
          </div>

          <div className="relative z-10 flex flex-1 items-center justify-center px-5 py-8">
            <div className="text-center">
              <div className="relative mx-auto flex size-40 items-center justify-center md:size-52">
                <div className="audit-avatar-ring absolute inset-0 rounded-full border border-cyan-300/25" />
                <div className="absolute inset-4 rounded-full border border-cyan-300/10" />
                <div className="flex size-28 items-center justify-center overflow-hidden rounded-full border border-white/10 bg-[#0b141d] shadow-2xl md:size-36">
                  <img src="/avatars/interviewer.svg" alt="AI 产品审计官" className="size-full object-cover" />
                </div>
                <span className="absolute bottom-3 right-3 flex size-8 items-center justify-center rounded-full border-4 border-[#071019] bg-emerald-400 text-emerald-950 md:bottom-5 md:right-5">
                  <MicIcon size={13} />
                </span>
              </div>

              <div className="mt-6">
                <p className="text-xs font-medium uppercase text-cyan-200/60">Lead Product Auditor</p>
                <h2 className="mt-2 text-xl font-semibold text-white">AI 产品审计官</h2>
                <p className="mt-2 text-xs text-zinc-500">
                  {isStreaming ? "正在分析你的回答…" : isPlayingAudio ? "正在发言…" : "等待你的回答"}
                </p>
              </div>

              <div className="mx-auto mt-5 flex h-7 items-center justify-center gap-1" aria-hidden="true">
                {Array.from({ length: 18 }, (_, index) => (
                  <span
                    key={index}
                    className={`w-0.5 rounded-full bg-cyan-300/80 ${
                      isStreaming || isPlayingAudio ? "audit-wave-bar" : ""
                    }`}
                    style={{ height: `${8 + ((index * 7) % 18)}px` }}
                  />
                ))}
              </div>
            </div>

            {showCamera && (
              <InterviewCamera onSkipCamera={onSkipCamera} onSessionReady={onProctorSession} />
            )}
          </div>

          <div className="relative z-10 border-t border-white/10 bg-black/20 px-5 py-4">
            <div className="mb-3 flex items-center justify-between">
              <span className="text-[10px] font-medium uppercase text-zinc-500">Audit coverage</span>
              <span className="font-mono text-[10px] text-cyan-200 tabular-nums">
                {auditProgress} / {AUDIT_DIMENSIONS.length}
              </span>
            </div>
            <div className="grid grid-cols-3 gap-2 md:grid-cols-6">
              {AUDIT_DIMENSIONS.map((dimension) => {
                // B008：格子点亮条件 = 后端已上报覆盖该维度
                const isCovered = coveredDimensions.includes(dimension.key);
                return (
                  <div
                    key={dimension.key}
                    className={`rounded-md border px-2 py-2 text-center text-[10px] ${
                      isCovered
                        ? "border-cyan-300/25 bg-cyan-300/5 text-cyan-100"
                        : "border-white/10 text-zinc-600"
                    }`}
                  >
                    {dimension.label}
                  </div>
                );
              })}
            </div>
          </div>
        </section>

        <aside className="flex min-h-0 flex-col bg-[#080c12]">
          <div className="flex h-12 shrink-0 items-center justify-between border-b border-white/10 px-4">
            <div className="flex items-center gap-2">
              <BrainIcon size={14} className="text-cyan-200" />
              <span className="text-xs font-semibold text-zinc-200">审计纪要</span>
            </div>
            <span className="flex items-center gap-1.5 text-[10px] text-zinc-500">
              <ShieldIcon size={10} />
              自动记录
            </span>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-2 py-3">
            {/* B046：TTS 失败一次性提示（aria-live） */}
            {ttsNotice && (
              <div
                role="status"
                aria-live="polite"
                className="mx-2 mb-2 flex items-center justify-between gap-2 rounded-lg border border-amber-400/25 bg-amber-400/5 px-3 py-2 text-[10px] leading-4 text-amber-200"
              >
                <span>{ttsNotice}</span>
                <button
                  type="button"
                  onClick={() => setTtsNotice(null)}
                  aria-label="关闭提示"
                  className="shrink-0 text-amber-300/70 transition-colors hover:text-amber-200"
                >
                  ✕
                </button>
              </div>
            )}

            {/* B046：TTS 播放时高亮当前播报内容（文字始终展示于消息列表，此处为字幕条） */}
            {interviewMode === "voice" && isPlayingAudio && speakingText && (
              <div className="mx-2 mb-2 rounded-lg bg-cyan-400/5 px-3 py-2 ring-1 ring-cyan-400/50">
                <p className="mb-0.5 text-[10px] font-medium uppercase text-cyan-300/70">正在播报</p>
                <p className="max-h-20 overflow-y-auto text-xs leading-5 text-zinc-200">{speakingText}</p>
              </div>
            )}

            <MessageList />
          </div>

          <InterviewInput isPlayingAudio={isPlayingAudio} />
        </aside>
      </div>
    </div>
  );
}
