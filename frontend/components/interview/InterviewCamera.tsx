"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { UserIcon, VideoIcon } from "@/components/icons";
import type { CaptureInfo, ProctorEvent, ProctorSession } from "@/lib/proctor-gaze/types";

type CameraStatus = "idle" | "requesting" | "active" | "error";
type MeasurementStatus = "loading" | "normal" | "missing" | "multiple" | "away";
type GazeModule = typeof import("@/lib/proctor-gaze");

const MEASUREMENT_LABELS: Record<MeasurementStatus, string> = {
  loading: "正在加载人脸模型",
  normal: "人脸与视线正常",
  missing: "未检测到人脸",
  multiple: "检测到多人",
  away: "检测到视线偏离",
};

/** B047：aria-live 播报文案 */
const MEASUREMENT_ARIA: Record<MeasurementStatus, string> = {
  loading: "监考状态：模型加载中",
  normal: "监考状态：正常",
  missing: "监考状态：未检测到人脸",
  multiple: "监考状态：检测到多人",
  away: "监考状态：视线偏离",
};

/** B096：missing 用 amber、away 用 orange 区分 */
const MEASUREMENT_STYLES: Record<MeasurementStatus, string> = {
  loading: "bg-black/55 text-cyan-100",
  normal: "bg-emerald-950/75 text-emerald-200",
  missing: "bg-amber-950/80 text-amber-200",
  multiple: "bg-red-950/80 text-red-200",
  away: "bg-orange-950/80 text-orange-200",
};

/** B044：校准建议持续时长（与 baselineLearnMs 对齐） */
const CALIBRATION_SECONDS = 3;

function getCameraErrorMessage(error: unknown) {
  if (!(error instanceof DOMException)) {
    return "摄像头或人脸测量模型启动失败，请刷新后重试";
  }

  switch (error.name) {
    case "NotAllowedError":
    case "SecurityError":
      // B048：补充拒绝后果说明
      return "摄像头权限未开启，请在浏览器地址栏中允许访问；拒绝后本次面试将不包含监考数据";
    case "NotFoundError":
    case "DevicesNotFoundError":
      return "未检测到可用摄像头";
    case "NotReadableError":
    case "TrackStartError":
      return "摄像头正被其他应用占用";
    case "OverconstrainedError":
      return "当前摄像头不支持所需的视频设置";
    default:
      return "摄像头启动失败，请检查设备或浏览器设置";
  }
}

export function InterviewCamera({
  onSkipCamera,
  onSessionReady,
}: {
  /** B048：摄像头失败后降级为仅文字面试 */
  onSkipCamera?: () => void;
  /** B041：把 proctor session 暴露给父级（结束面试时导出数据） */
  onSessionReady?: (session: ProctorSession | null) => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const sessionRef = useRef<ProctorSession | null>(null);
  const overlayFrameRef = useRef(0);
  const attemptRef = useRef(0);
  const mountedRef = useRef(false);
  const retryButtonRef = useRef<HTMLButtonElement>(null);
  const onSkipCameraRef = useRef(onSkipCamera);
  const onSessionReadyRef = useRef(onSessionReady);
  const [status, setStatus] = useState<CameraStatus>("idle");
  const [measurementStatus, setMeasurementStatus] = useState<MeasurementStatus>("loading");
  const [captureInfo, setCaptureInfo] = useState<CaptureInfo | null>(null);
  const [faceCount, setFaceCount] = useState(0);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  // B104：点击放大/还原预览
  const [enlarged, setEnlarged] = useState(false);
  // B044：null = 校准完成（或未开始），number = 已校准秒数
  const [calibrationSecond, setCalibrationSecond] = useState<number | null>(null);

  onSkipCameraRef.current = onSkipCamera;
  onSessionReadyRef.current = onSessionReady;

  const stopSession = useCallback(() => {
    cancelAnimationFrame(overlayFrameRef.current);
    overlayFrameRef.current = 0;
    sessionRef.current?.stop();
    sessionRef.current = null;
    // B041：session 停止后通知父级清空引用，避免导出已停止的会话
    onSessionReadyRef.current?.(null);

    const ctx = canvasRef.current?.getContext("2d");
    if (ctx && canvasRef.current) {
      ctx.clearRect(0, 0, canvasRef.current.width, canvasRef.current.height);
    }
  }, []);

  const handleEvent = useCallback((event: ProctorEvent) => {
    if (!mountedRef.current) return;

    switch (event.type) {
      case "session_started":
        setMeasurementStatus("normal");
        break;
      case "face_missing":
        setMeasurementStatus("missing");
        break;
      case "multi_face":
        setMeasurementStatus("multiple");
        break;
      case "looking_away":
        setMeasurementStatus("away");
        break;
      case "face_ok":
        setMeasurementStatus("normal");
        break;
      case "camera_error":
        setStatus("error");
        setErrorMessage("摄像头或人脸测量发生异常，请重新开启");
        break;
    }
  }, []);

  const startOverlay = useCallback((gaze: GazeModule, session: ProctorSession) => {
    const render = () => {
      if (!mountedRef.current || sessionRef.current !== session) return;

      const video = videoRef.current;
      const canvas = canvasRef.current;
      const ctx = canvas?.getContext("2d");
      if (video && canvas && ctx) {
        gaze.syncOverlayCanvas(canvas, video);
        const result = session.getLastResult() as Parameters<GazeModule["drawDebugOverlay"]>[1];
        gaze.drawDebugOverlay(ctx, result, video, {
          drawFaceMesh: false,
          drawGazeArrow: true,
          // B081：箭头缩短，降低叠加层对预览的干扰
          gazeArrowScale: 18,
        });
      }

      // B044：基于基线学习开始时间显示校准进度（秒级更新，避免每帧 setState）
      const calibration = session.getCalibration();
      if (calibration.ready) {
        setCalibrationSecond((current) => (current === null ? current : null));
      } else {
        const elapsed = calibration.startedAt === null
          ? 0
          : Math.min(
              CALIBRATION_SECONDS,
              Math.max(0, Math.floor((Date.now() - calibration.startedAt) / 1000)),
            );
        setCalibrationSecond((current) => (current === elapsed ? current : elapsed));
      }

      overlayFrameRef.current = requestAnimationFrame(render);
    };

    overlayFrameRef.current = requestAnimationFrame(render);
  }, []);

  const startCamera = useCallback(async () => {
    const video = videoRef.current;
    if (!video) return;

    if (!window.isSecureContext) {
      setStatus("error");
      setErrorMessage("摄像头需要通过 HTTPS 或 localhost 打开");
      return;
    }

    if (!navigator.mediaDevices?.getUserMedia) {
      setStatus("error");
      setErrorMessage("当前浏览器不支持摄像头，请使用最新版 Chrome 或 Edge");
      return;
    }

    const attempt = ++attemptRef.current;
    stopSession();
    setStatus("requesting");
    setMeasurementStatus("loading");
    setCaptureInfo(null);
    setFaceCount(0);
    setErrorMessage(null);
    setCalibrationSecond(null);

    try {
      const gaze = await import("@/lib/proctor-gaze");
      if (!mountedRef.current || attempt !== attemptRef.current) return;

      const session = gaze.createProctorSession({
        video,
        modelBaseUrl: "/models",
        numFaces: 2,
        // B043：阈值交给 gazeRules 默认值（yaw 25° / pitch 20° / iris 0.18 / awayEnterMs 3s）
        onEvent: handleEvent,
        onSample: (sample) => {
          if (!mountedRef.current || attempt !== attemptRef.current) return;
          // B042：仅 faceCount 变化时才更新 state，避免高频重渲染
          setFaceCount((current) => (current === sample.faceCount ? current : sample.faceCount));
        },
      });

      sessionRef.current = session;
      await session.start();

      if (!mountedRef.current || attempt !== attemptRef.current) {
        session.stop();
        return;
      }

      setStatus("active");
      setCaptureInfo(session.getCaptureInfo());
      // B041：session 就绪后暴露给父级，供“结束面试”导出
      onSessionReadyRef.current?.(session);
      startOverlay(gaze, session);

      const videoTrack = (video.srcObject as MediaStream | null)?.getVideoTracks()[0];
      videoTrack?.addEventListener(
        "ended",
        () => {
          if (!mountedRef.current || sessionRef.current !== session) return;
          stopSession();
          setStatus("error");
          setErrorMessage("摄像头连接已中断，请重新开启");
        },
        { once: true },
      );
    } catch (error) {
      if (!mountedRef.current || attempt !== attemptRef.current) return;
      stopSession();
      setStatus("error");
      setErrorMessage(getCameraErrorMessage(error));
    }
  }, [handleEvent, startOverlay, stopSession]);

  useEffect(() => {
    mountedRef.current = true;
    void startCamera();

    return () => {
      mountedRef.current = false;
      attemptRef.current += 1;
      stopSession();
    };
  }, [startCamera, stopSession]);

  // B105：进入错误态时自动聚焦“重新开启”按钮
  useEffect(() => {
    if (status === "error") retryButtonRef.current?.focus();
  }, [status]);

  return (
    <aside
      className={`absolute bottom-5 right-5 block overflow-hidden rounded-xl border border-white/10 bg-[#0b1118] shadow-2xl transition-[width] duration-200 ${
        enlarged ? "w-80" : "w-44 sm:w-64"
      }`}
    >
      {/* B047：状态播报（视障用户） */}
      <div aria-live="polite" className="sr-only">
        {status === "active"
          ? MEASUREMENT_ARIA[measurementStatus]
          : status === "error"
            ? "监考状态：摄像头不可用"
            : "监考状态：正在启动摄像头"}
      </div>

      <div
        className="relative flex aspect-video cursor-zoom-in items-center justify-center overflow-hidden bg-black/40"
        // B104：点击放大/还原预览
        role="button"
        tabIndex={0}
        aria-label={enlarged ? "缩小摄像头预览" : "放大摄像头预览"}
        onClick={() => setEnlarged((current) => !current)}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            setEnlarged((current) => !current);
          }
        }}
      >
        {/* B088：状态仅在 landmarker 就绪后置为 active，onPlaying 不再提前置位 */}
        <video
          ref={videoRef}
          autoPlay
          muted
          playsInline
          aria-label="你的摄像头实时画面"
          className={`size-full scale-x-[-1] object-cover transition-opacity duration-300 ${
            status === "active" ? "opacity-100" : "opacity-0"
          }`}
        />
        <canvas
          ref={canvasRef}
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 size-full scale-x-[-1]"
        />

        {status !== "active" && (
          // B047：错误浮层 role="alert"
          <div
            role={status === "error" ? "alert" : undefined}
            className="absolute inset-0 flex flex-col items-center justify-center gap-2 px-4 text-center"
          >
            {status === "requesting" ? (
              <>
                <span className="size-5 animate-spin rounded-full border-2 border-cyan-200/20 border-t-cyan-200" />
                <span className="text-[10px] text-zinc-400">正在连接摄像头与测量模型…</span>
              </>
            ) : (
              <>
                <UserIcon size={28} className="text-zinc-700" />
                <span className="text-[10px] leading-4 text-zinc-500">
                  {errorMessage ?? "准备开启摄像头"}
                </span>
                {/* B093：加大触控目标 */}
                <button
                  ref={retryButtonRef}
                  type="button"
                  onClick={() => void startCamera()}
                  className="min-h-[44px] rounded-md border border-cyan-300/25 bg-cyan-300/10 px-4 py-2 text-xs text-cyan-100 transition-colors hover:bg-cyan-300/15"
                >
                  重新开启
                </button>
                {/* B048：摄像头失败降级为仅文字面试 */}
                {status === "error" && onSkipCamera && (
                  <button
                    type="button"
                    onClick={onSkipCamera}
                    className="min-h-[44px] rounded-md px-4 py-2 text-xs text-zinc-400 underline-offset-4 transition-colors hover:text-zinc-200 hover:underline"
                  >
                    继续仅文字面试
                  </button>
                )}
              </>
            )}
          </div>
        )}

        {status === "active" && (
          <span
            className={`absolute left-2 top-2 flex items-center gap-1 rounded-full px-2 py-1 text-[10px] backdrop-blur-sm ${MEASUREMENT_STYLES[measurementStatus]}`}
          >
            <span className="size-1.5 rounded-full bg-current" />
            {MEASUREMENT_LABELS[measurementStatus]}
          </span>
        )}

        {/* B044：校准阶段提示 */}
        {status === "active" && calibrationSecond !== null && (
          <div className="absolute inset-x-2 top-9 rounded-lg border border-cyan-300/25 bg-black/60 px-2.5 py-1.5 text-center text-[10px] leading-4 text-cyan-100 backdrop-blur-sm">
            请正对摄像头保持 3 秒（校准中 {calibrationSecond}/{CALIBRATION_SECONDS}s）
          </div>
        )}
      </div>

      <div className="flex items-center justify-between gap-2 px-3 py-2">
        <div className="min-w-0">
          <p className="truncate text-[10px] text-zinc-300">
            你 · {status === "active" ? "人脸与视线测量中" : "等待视频"}
          </p>
          {status === "active" && (
            <p className="mt-0.5 truncate font-mono text-[10px] text-zinc-500">
              {captureInfo
                ? `${captureInfo.width}×${captureInfo.height} · ${captureInfo.sampleHz}Hz · ${faceCount} face${
                    // B049：CPU 回退提示
                    captureInfo.delegate === "CPU" ? "（CPU 模式，性能可能下降）" : ""
                  }`
                : "正在初始化测量参数…"}
            </p>
          )}
        </div>
        <VideoIcon
          size={12}
          className={status === "active" ? "shrink-0 text-emerald-300" : "shrink-0 text-zinc-600"}
        />
      </div>
    </aside>
  );
}
