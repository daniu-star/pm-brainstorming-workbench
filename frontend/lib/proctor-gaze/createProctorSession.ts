import {
  FaceLandmarker,
  FilesetResolver,
  type FaceLandmarkerResult,
} from "@mediapipe/tasks-vision";
import { downloadBlob, exportEvents, exportGazeFile } from "./exportGazeFile";
import {
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
): Promise<FaceLandmarker> {
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
      return await tryCreate("GPU");
    } catch {
      return await tryCreate("CPU");
    }
  }
  return tryCreate("CPU");
}

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
    captureInfo = {
      width,
      height,
      frameRate: sampling.fps,
      tier: sampling.tier,
      sampleHz: sampling.sampleHz,
      frameStride: sampling.frameStride,
      awayEnterMs: resolvedRules.awayEnterMs,
      sampleHzSource: sampling.sampleHzSource,
    };

    landmarker = await createLandmarker(modelBaseUrl, numFaces, preferGpu);

    running = true;
    paused = document.hidden;
    document.addEventListener("visibilitychange", onVisibility);
    lastVideoTime = -1;
    lastSampleVideoTime = -1;
    frameAccum = 0;
    pushEvent({
      type: "session_started",
      t: Date.now(),
      detail: `tier=${captureInfo.tier} sampleHz=${captureInfo.sampleHz} awayEnterMs=${captureInfo.awayEnterMs}`,
      payload: { ...captureInfo },
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
  };

  return {
    start,
    stop,
    exportGazeFile: () => exportGazeFile(samples),
    exportEvents: () => exportEvents(events),
    getSamples: () => samples,
    getEvents: () => events,
    isRunning: () => running,
    getLastResult: () => lastResult,
    getCaptureInfo: () => captureInfo,
  };
}
export { downloadBlob };
