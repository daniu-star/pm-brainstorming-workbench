"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { User, Video } from "lucide-react";
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

const MEASUREMENT_STYLES: Record<MeasurementStatus, string> = {
  loading: "bg-black/55 text-cyan-100",
  normal: "bg-emerald-950/75 text-emerald-200",
  missing: "bg-amber-950/80 text-amber-200",
  multiple: "bg-red-950/80 text-red-200",
  away: "bg-amber-950/80 text-amber-200",
};

function getCameraErrorMessage(error: unknown) {
  if (!(error instanceof DOMException)) {
    return "摄像头或人脸测量模型启动失败，请刷新后重试";
  }

  switch (error.name) {
    case "NotAllowedError":
    case "SecurityError":
      return "摄像头权限未开启，请在浏览器地址栏中允许访问";
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

export function InterviewCamera() {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const sessionRef = useRef<ProctorSession | null>(null);
  const overlayFrameRef = useRef(0);
  const attemptRef = useRef(0);
  const mountedRef = useRef(false);
  const [status, setStatus] = useState<CameraStatus>("idle");
  const [measurementStatus, setMeasurementStatus] = useState<MeasurementStatus>("loading");
  const [captureInfo, setCaptureInfo] = useState<CaptureInfo | null>(null);
  const [faceCount, setFaceCount] = useState(0);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const stopSession = useCallback(() => {
    cancelAnimationFrame(overlayFrameRef.current);
    overlayFrameRef.current = 0;
    sessionRef.current?.stop();
    sessionRef.current = null;

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
          gazeArrowScale: 30,
        });
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

    try {
      const gaze = await import("@/lib/proctor-gaze");
      if (!mountedRef.current || attempt !== attemptRef.current) return;

      const session = gaze.createProctorSession({
        video,
        modelBaseUrl: "/models",
        numFaces: 2,
        gazeRules: {
          awayEnterMs: 1500,
          presenceDebounceMs: 500,
        },
        onEvent: handleEvent,
        onSample: (sample) => {
          if (!mountedRef.current || attempt !== attemptRef.current) return;
          setFaceCount(sample.faceCount);
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

  return (
    <aside className="absolute right-4 top-20 z-30 block w-44 overflow-hidden rounded-xl border border-white/10 bg-[#0b1118] shadow-2xl sm:w-64">
      <div className="relative flex aspect-video items-center justify-center overflow-hidden bg-black/40">
        <video
          ref={videoRef}
          autoPlay
          muted
          playsInline
          aria-label="你的摄像头实时画面"
          onPlaying={() => setStatus((current) => (current === "requesting" ? "active" : current))}
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
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 px-4 text-center">
            {status === "requesting" ? (
              <>
                <span className="size-5 animate-spin rounded-full border-2 border-cyan-200/20 border-t-cyan-200" />
                <span className="text-[10px] text-zinc-400">正在连接摄像头与测量模型…</span>
              </>
            ) : (
              <>
                <User size={28} className="text-zinc-700" />
                <span className="text-[9px] leading-4 text-zinc-500">
                  {errorMessage ?? "准备开启摄像头"}
                </span>
                <button
                  type="button"
                  onClick={() => void startCamera()}
                  className="rounded-md border border-cyan-300/25 bg-cyan-300/10 px-2.5 py-1 text-[9px] text-cyan-100 transition-colors hover:bg-cyan-300/15"
                >
                  重新开启
                </button>
              </>
            )}
          </div>
        )}

        {status === "active" && (
          <span
            className={`absolute left-2 top-2 flex items-center gap-1 rounded-full px-2 py-1 text-[8px] backdrop-blur-sm ${MEASUREMENT_STYLES[measurementStatus]}`}
          >
            <span className="size-1.5 rounded-full bg-current" />
            {MEASUREMENT_LABELS[measurementStatus]}
          </span>
        )}
      </div>

      <div className="flex items-center justify-between gap-2 px-3 py-2">
        <div className="min-w-0">
          <p className="truncate text-[10px] text-zinc-300">
            你 · {status === "active" ? "人脸与视线测量中" : "等待视频"}
          </p>
          {status === "active" && (
            <p className="mt-0.5 truncate font-mono text-[8px] text-zinc-600">
              {captureInfo
                ? `${captureInfo.width}×${captureInfo.height} · ${captureInfo.sampleHz}Hz · ${faceCount} face`
                : "正在初始化测量参数…"}
            </p>
          )}
        </div>
        <Video
          size={12}
          className={status === "active" ? "shrink-0 text-emerald-300" : "shrink-0 text-zinc-600"}
        />
      </div>
    </aside>
  );
}
