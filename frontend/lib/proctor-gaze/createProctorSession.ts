import {
  FaceLandmarker,
  FilesetResolver,
  type FaceLandmarkerResult,
} from "@mediapipe/tasks-vision";
import { downloadBlob, exportEvents, exportGazeFile } from "./exportGazeFile";
import {
  BASELINE_MIN_SAMPLES,
  createGazeRulesState,
  evaluateGazeRules,
  resolveGazeRulesConfig,
} from "./gazeRules";
import {
  estimateGaze,
  extractEyes,
  matrixToHeadPose,
  toFullLandmarks,
} from "./landmarks";
import {
  openCameraStream,
  resolveSampling,
  shouldSampleByVideoTime,
} from "./sampling";
import type {
  CaptureInfo,
  CreateProctorSessionOptions,
  GazeSample,
  ProctorEvent,
  ProctorSession,
} from "./types";

function joinUrl(base: string, path: string): string {
  const b = base.replace(/\/+$/, "");
  const p = path.replace(/^\/+/, "");
  return `${b}/${p}`;
}

async function createLandmarker(
  modelBaseUrl: string,
  numFaces: number,
  preferGpu: boolean,
): Promise<{
  landmarker: FaceLandmarker;
  delegate: "GPU" | "CPU";
  /** B049：preferGpu 但 GPU 初始化失败时为 true */
  fallback: boolean;
}> {
  const wasmPath = joinUrl(modelBaseUrl, "wasm");
  const modelPath = joinUrl(modelBaseUrl, "face_landmarker.task");
  const vision = await FilesetResolver.forVisionTasks(wasmPath);

  const tryCreate = async (delegate: "GPU" | "CPU") =>
    FaceLandmarker.createFromOptions(vision, {
      baseOptions: {
        modelAssetPath: modelPath,
        delegate,
      },
      runningMode: "VIDEO",
      numFaces,
      outputFacialTransformationMatrixes: true,
      outputFaceBlendshapes: false,
    });

  if (preferGpu) {
    try {
      return { landmarker: await tryCreate("GPU"), delegate: "GPU", fallback: false };
    } catch {
      return { landmarker: await tryCreate("CPU"), delegate: "CPU", fallback: true };
    }
  }
  return { landmarker: await tryCreate("CPU"), delegate: "CPU", fallback: false };
}

/** B042：ring buffer 上限 —— samples 约 5 分钟 @10Hz，events 500 条。 */
const MAX_SAMPLES = 3000;
const MAX_EVENTS = 500;

/** Build a raw GazeSample (no EMA — smoothing lives in gazeRules only). */
export function buildSample(
  result: FaceLandmarkerResult,
  fullLandmarks: boolean,
  t: number,
): GazeSample {
  const faceCount = result.faceLandmarks?.length ?? 0;
  const sample: GazeSample = { t, faceCount };
  if (faceCount === 0) return sample;

  const landmarks = result.faceLandmarks[0]!;
  const eyes = extractEyes(landmarks);
  sample.leftEye = eyes.leftEye;
  sample.rightEye = eyes.rightEye;
  sample.leftGaze = estimateGaze(eyes.leftEye);
  sample.rightGaze = estimateGaze(eyes.rightEye);

  const matrices = result.facialTransformationMatrixes;
  if (matrices?.[0]?.data) {
    sample.headPose = matrixToHeadPose(Array.from(matrices[0].data));
  }

  if (fullLandmarks) {
    sample.fullLandmarks = toFullLandmarks(landmarks);
  }
  return sample;
}

/**
 * Create a browser-side proctoring session.
 * Inference runs entirely in the browser; ECS only hosts static assets (+ optional JSON API).
 *
 * Capture: lock ~30fps → read getSettings() tier → frame-aligned sample/detect (~8–10Hz).
 * Rules: EMA → baseline-relative + hysteresis → ms episode timing → looking_away after awayEnterMs.
 * B042: samples/events use ring buffers (MAX_SAMPLES/MAX_EVENTS) to bound memory.
 */
export function createProctorSession(
  options: CreateProctorSessionOptions,
): ProctorSession {
  const {
    video,
    modelBaseUrl,
    numFaces = 2,
    fullLandmarks = false,
    gazeRules,
    onEvent,
    onSample,
    preferGpu = true,
  } = options;

  const explicitSampleHz = options.sampleHz;
  const resolvedRules = resolveGazeRulesConfig(gazeRules);

  let landmarker: FaceLandmarker | null = null;
  let stream: MediaStream | null = null;
  let running = false;
  let paused = false;
  let rafId = 0;
  let lastVideoTime = -1;
  let lastSampleVideoTime = -1;
  let frameAccum = 0;
  let lastResult: FaceLandmarkerResult | null = null;
  let captureInfo: CaptureInfo | null = null;
  let activeSampleHz = explicitSampleHz ?? 8;
  let activeStride = 3;

  const samples: GazeSample[] = [];
  const events: ProctorEvent[] = [];
  const rulesState = createGazeRulesState();

  const pushEvent = (event: ProctorEvent) => {
    events.push(event);
    if (events.length > MAX_EVENTS) events.shift();
    onEvent?.(event);
  };

  const onVisibility = () => {
    paused = document.hidden;
  };

  const releaseTracks = () => {
    if (!stream) return;
    for (const track of stream.getTracks()) track.stop();
    stream = null;
    video.srcObject = null;
  };

  const loop = () => {
    if (!running) return;
    rafId = requestAnimationFrame(loop);
    if (paused || !landmarker) return;
    if (video.readyState < 2) return;

    // New decoded frame only
    if (video.currentTime === lastVideoTime) return;
    lastVideoTime = video.currentTime;

    // Frame-count downsampling aligned with sampleHz (detect + sample together)
    frameAccum += 1;
    const dueByStride = frameAccum >= activeStride;
    const dueByTime = shouldSampleByVideoTime(
      lastSampleVideoTime,
      video.currentTime,
      activeSampleHz,
    );
    if (!dueByStride && !dueByTime) return;
    frameAccum = 0;
    lastSampleVideoTime = video.currentTime;

    const now = performance.now();
    try {
      lastResult = landmarker.detectForVideo(video, now);
    } catch (err) {
      pushEvent({
        type: "camera_error",
        t: Date.now(),
        detail: err instanceof Error ? err.message : String(err),
      });
      return;
    }

    // Raw coordinates only — EMA is inside evaluateGazeRules
    const sample = buildSample(lastResult, fullLandmarks, Date.now());
    samples.push(sample);
    if (samples.length > MAX_SAMPLES) samples.shift();
    onSample?.(sample);

    const ev = evaluateGazeRules(sample, rulesState, gazeRules);
    if (ev) pushEvent(ev);
  };

  const start = async () => {
    if (running) return;

    try {
      stream = await openCameraStream();
    } catch (err) {
      const detail =
        err instanceof DOMException
          ? `${err.name}: ${err.message}`
          : err instanceof Error
            ? err.message
            : String(err);
      pushEvent({
        type: "camera_error",
        t: Date.now(),
        detail:
          detail.includes("NotAllowed") || detail.includes("Permission")
            ? `Camera permission denied. Use HTTPS (https://127.0.0.1 or LAN IP) and allow camera access. (${detail})`
            : `Failed to open camera: ${detail}`,
      });
      throw err;
    }

    video.srcObject = stream;
    video.playsInline = true;
    video.muted = true;
    await video.play();

    const track = stream.getVideoTracks()[0];
    const settings = track?.getSettings?.() ?? {};
    const width = settings.width ?? video.videoWidth ?? 1280;
    const height = settings.height ?? video.videoHeight ?? 720;
    const frameRate = settings.frameRate ?? 30;

    const sampling = resolveSampling({
      width,
      height,
      frameRate,
      explicitSampleHz,
    });
    activeSampleHz = sampling.sampleHz;
    activeStride = sampling.frameStride;

    const {
      landmarker: created,
      delegate,
      fallback,
    } = await createLandmarker(modelBaseUrl, numFaces, preferGpu);
    landmarker = created;

    captureInfo = {
      width,
      height,
      frameRate: sampling.fps,
      tier: sampling.tier,
      sampleHz: sampling.sampleHz,
      frameStride: sampling.frameStride,
      awayEnterMs: resolvedRules.awayEnterMs,
      sampleHzSource: sampling.sampleHzSource,
      delegate,
    };

    running = true;
    paused = document.hidden;
    document.addEventListener("visibilitychange", onVisibility);
    lastVideoTime = -1;
    lastSampleVideoTime = -1;
    frameAccum = 0;
    // B049：GPU 失败回退 CPU 时在 session_started payload 中带上 delegate/fallback
    pushEvent({
      type: "session_started",
      t: Date.now(),
      detail: `tier=${captureInfo.tier} sampleHz=${captureInfo.sampleHz} awayEnterMs=${captureInfo.awayEnterMs} delegate=${delegate}${fallback ? " (fallback)" : ""}`,
      payload: { ...captureInfo, delegate, fallback },
    });
    rafId = requestAnimationFrame(loop);
  };

  const stop = () => {
    if (!running && !stream) return;
    running = false;
    cancelAnimationFrame(rafId);
    document.removeEventListener("visibilitychange", onVisibility);
    landmarker?.close();
    landmarker = null;
    lastResult = null;
    captureInfo = null;
    releaseTracks();
    pushEvent({ type: "session_stopped", t: Date.now() });
    // B042：显式清空 ring buffer 释放引用（导出应在 stop 前完成）
    samples.length = 0;
    events.length = 0;
  };

  return {
    start,
    stop,
    exportGazeFile: () => exportGazeFile(samples, { eventCount: events.length }),
    exportEvents: () => exportEvents(events, { sampleCount: samples.length }),
    getSamples: () => samples,
    getEvents: () => events,
    isRunning: () => running,
    getLastResult: () => lastResult,
    getCaptureInfo: () => captureInfo,
    getCalibration: () => ({
      ready: rulesState.baselineFinalized,
      startedAt: rulesState.baselineLearnStart,
      samples: rulesState.baselineAccum.n,
      requiredSamples: BASELINE_MIN_SAMPLES,
      requiredMs: resolvedRules.baselineLearnMs,
    }),
  };
}

export { downloadBlob };
