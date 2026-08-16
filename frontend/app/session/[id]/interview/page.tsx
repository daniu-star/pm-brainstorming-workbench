"use client";

import { useCallback, useEffect, useRef, useState, Suspense } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { useSessionStore } from "@/store/sessionStore";
import { InterviewView } from "@/components/interview/InterviewView";
import { HistoryDrawer } from "@/components/HistoryDrawer";
import { BrainIcon, ShieldIcon, VideoIcon } from "@/components/icons";
import { downloadBlob } from "@/lib/proctor-gaze/exportGazeFile";
import type { ProctorSession } from "@/lib/proctor-gaze/types";

/** B007：同意模式 —— camera = 摄像头面试，text = 仅文字面试 */
type ConsentMode = "camera" | "text";

const CONSENT_STORAGE_PREFIX = "interview_consent_";

function readStoredConsent(sessionId: string | null): ConsentMode | null {
  if (!sessionId || typeof window === "undefined") return null;
  try {
    const stored = window.sessionStorage.getItem(`${CONSENT_STORAGE_PREFIX}${sessionId}`);
    return stored === "camera" || stored === "text" ? stored : null;
  } catch {
    return null;
  }
}

function writeStoredConsent(sessionId: string | null, mode: ConsentMode) {
  if (!sessionId || typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(`${CONSENT_STORAGE_PREFIX}${sessionId}`, mode);
  } catch {
    // sessionStorage 不可用时静默降级（每次进入重新确认）
  }
}

function InterviewContent() {
  const params = useParams();
  const router = useRouter();
  const searchParams = useSearchParams();
  const isPreview = searchParams.get("preview") === "1";
  const { sessionId, loadSession, startInterview, isHistoryOpen, toggleHistory } =
    useSessionStore();
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  // B007：同意状态（sessionStorage 持久，刷新不重复弹窗）
  const [consent, setConsent] = useState<ConsentMode | null>(null);
  const [consentChecked, setConsentChecked] = useState(false);
  // B048：摄像头失败后可降级隐藏摄像头区域
  const [cameraEnabled, setCameraEnabled] = useState(false);
  const triggeredRef = useRef(false);
  // B041：由 InterviewCamera 暴露的监考会话（结束面试时导出）
  const proctorSessionRef = useRef<ProctorSession | null>(null);

  useEffect(() => {
    const id = params?.id as string;
    if (id) {
      loadSession(id)
        .then(() => setLoading(false))
        .catch((err) => {
          setLoadError(err instanceof Error ? err.message : "加载会话失败");
          setLoading(false);
        });
    }
  }, [params?.id]);

  // B007：加载完成后读取已存储的同意状态
  useEffect(() => {
    if (loading) return;
    setConsent(readStoredConsent(sessionId));
    setConsentChecked(true);
  }, [loading, sessionId]);

  // B007：仅当用户已做出选择（本次或此前存储）后才自动开始一次面试
  useEffect(() => {
    if (!loading && sessionId && consent && !triggeredRef.current && !isPreview) {
      triggeredRef.current = true;
      startInterview();
    }
  }, [loading, sessionId, consent, startInterview, isPreview]);

  // 摄像头渲染条件：用户同意摄像头模式（预览模式不启动摄像头）
  useEffect(() => {
    setCameraEnabled(!isPreview && consent === "camera");
  }, [consent, isPreview]);

  const chooseConsent = useCallback(
    (mode: ConsentMode) => {
      writeStoredConsent(sessionId, mode);
      setConsent(mode);
    },
    [sessionId],
  );

  const handleProctorSession = useCallback((session: ProctorSession | null) => {
    proctorSessionRef.current = session;
  }, []);

  const handleSkipCamera = useCallback(() => setCameraEnabled(false), []);

  // B041/B087：结束面试——导出监考数据（本地落盘）后返回工作台
  const handleEndInterview = useCallback(() => {
    const session = proctorSessionRef.current;
    if (session) {
      const ts = Date.now();
      try {
        downloadBlob(session.exportGazeFile(), `gaze_${sessionId ?? "unknown"}_${ts}.jsonl`);
        downloadBlob(session.exportEvents(), `events_${sessionId ?? "unknown"}_${ts}.jsonl`);
      } catch {
        // 导出失败不阻断退出流程
      }
      try {
        session.stop();
      } catch {
        // 已在卸载路径兜底停止
      }
      proctorSessionRef.current = null;
    }
    router.push(sessionId ? `/session/${sessionId}` : "/");
  }, [router, sessionId]);

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-dark-900 bg-mesh bg-grid">
        <div className="text-center">
          <div className="mb-6 flex justify-center">
            <BrainIcon size={48} className="text-red-500 animate-pulse" />
          </div>
          <div className="text-zinc-300 text-lg font-medium mb-2">准备面试...</div>
          <div className="mt-4 w-48 h-1 bg-dark-700 rounded-full mx-auto overflow-hidden">
            <div className="h-full bg-red-500/50 rounded-full animate-pulse" style={{ width: "60%" }} />
          </div>
        </div>
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-dark-900 bg-mesh bg-grid">
        <div className="text-center max-w-md">
          <div className="text-red-400 text-lg font-semibold mb-2">加载失败</div>
          <div className="text-zinc-400 text-sm mb-4">{loadError}</div>
          <a href="/" className="text-cyan-300 hover:text-cyan-200 text-sm underline">
            返回首页
          </a>
        </div>
      </div>
    );
  }

  // B007：未做出同意选择前，先渲染告知卡片（预览模式/无会话除外）
  if (!consent && !isPreview && consentChecked && sessionId) {
    return (
      <div className="min-h-screen bg-dark-900 bg-mesh bg-grid px-4 py-10">
        <div className="mx-auto max-w-lg">
          <div className="rounded-2xl border border-white/10 bg-[#0b1118] p-6 shadow-2xl sm:p-8">
            <div className="mb-5 flex items-center gap-3">
              <span className="flex size-10 shrink-0 items-center justify-center rounded-xl border border-cyan-300/20 bg-cyan-300/10 text-cyan-200">
                <VideoIcon size={20} />
              </span>
              <div>
                <h1 className="text-base font-semibold text-white">面试监考告知与同意</h1>
                <p className="mt-0.5 text-[10px] text-zinc-500">开始前请阅读以下说明</p>
              </div>
            </div>

            <div className="space-y-3 text-xs leading-5 text-zinc-300">
              <p>
                为辅助产品方案审计，你可以选择开启摄像头面试。开启后，将在你的浏览器本地分析以下信息：
              </p>
              <ul className="space-y-1.5 rounded-lg border border-white/10 bg-black/20 px-3.5 py-3 text-zinc-400">
                <li>· 人脸关键点（478 点，用于检测是否有人脸、是否多人入镜）</li>
                <li>· 头部姿态（俯仰 / 偏转角度）</li>
                <li>· 视线方向（基于虹膜位置的视线偏离估计）</li>
              </ul>
              <p className="flex items-start gap-2 text-zinc-400">
                <ShieldIcon size={14} className="mt-0.5 shrink-0 text-emerald-300" />
                <span>
                  所有分析均在本地浏览器完成，不会上传任何视频或人脸数据；仅在结束面试时可导出本地审计文件，且仅用于面试辅助审计。
                </span>
              </p>
              <p className="text-zinc-500">不同意开启摄像头也可继续仅文字面试，不影响审计流程。</p>
            </div>

            <div className="mt-6 flex flex-col gap-2.5">
              <button
                type="button"
                onClick={() => chooseConsent("camera")}
                className="min-h-[44px] rounded-xl bg-cyan-300 px-4 py-2.5 text-sm font-semibold text-[#031014] transition-colors hover:bg-cyan-200"
              >
                同意并开启摄像头面试
              </button>
              <button
                type="button"
                onClick={() => chooseConsent("text")}
                className="min-h-[44px] rounded-xl border border-white/10 px-4 py-2.5 text-sm text-zinc-300 transition-colors hover:border-white/20 hover:text-white"
              >
                仅文字面试（不开摄像头）
              </button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <>
      <InterviewView
        showCamera={cameraEnabled}
        onSkipCamera={handleSkipCamera}
        onProctorSession={handleProctorSession}
        onEndInterview={handleEndInterview}
      />
      <HistoryDrawer isOpen={isHistoryOpen} onClose={toggleHistory} />
    </>
  );
}

export default function InterviewPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen flex items-center justify-center bg-dark-900 bg-mesh bg-grid">
          <div className="w-10 h-10 border-2 border-red-500/30 border-t-red-500 rounded-full animate-spin" />
        </div>
      }
    >
      <InterviewContent />
    </Suspense>
  );
}
