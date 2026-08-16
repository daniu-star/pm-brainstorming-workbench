/** Stable public types for @proctor-gaze/sdk */

export type Point2D = { x: number; y: number };
export type Point3D = { x: number; y: number; z: number };

export type EyeCoords = {
  /** Iris center in normalized image coords [0,1] */
  iris: Point2D;
  /** Simplified eye contour (normalized) */
  contour: Point2D[];
};

export type GazeVector = {
  /** Approximate gaze direction in screen-ish space; origin at iris */
  dx: number;
  dy: number;
};

export type HeadPose = {
  /** Euler-ish degrees derived from facial transformation matrix */
  pitch: number;
  yaw: number;
  roll: number;
};

export type GazeSample = {
  /** Unix ms */
  t: number;
  faceCount: number;
  /** Primary face (index 0) when present */
  leftEye?: EyeCoords;
  rightEye?: EyeCoords;
  leftGaze?: GazeVector;
  rightGaze?: GazeVector;
  headPose?: HeadPose;
  /** Opt-in: all 478 landmarks for face 0 */
  fullLandmarks?: Point3D[];
};

export type ProctorEventType =
  | "face_missing"
  | "multi_face"
  | "looking_away"
  | "face_ok"
  | "camera_error"
  | "session_started"
  | "session_stopped";

export type LookingAwayReason = "head" | "iris" | "both";

export type ProctorEvent = {
  type: ProctorEventType;
  t: number;
  detail?: string;
  payload?: Record<string, unknown>;
};

/**
 * Gaze / presence rule knobs.
 *
 * Soft thresholds are **relative to a learned baseline** with enter/exit hysteresis.
 * Soft iris uses ‖signedGaze − baselineGaze‖ (dx÷halfW, dy÷halfH).
 * Hard caps are absolute. Defaults (B043): iris 0.18, yaw 25°, pitch 20°.
 *
 * `looking_away` is a sustained episode (≥ awayEnterMs), not a short edge.
 * `face_missing` / `multi_face` use presenceDebounceMs only (~500ms), and
 * recovery emits `face_ok` only after okRecoverMs (~800ms, B085) of a single
 * face.
 */
export type GazeRulesConfig = {
  /**
   * Soft enter: |yaw − baseline.yaw| (deg). Default 25.
   */
  yawDegThreshold?: number;
  /** Soft enter: |pitch − baseline.pitch| (deg). Default 20. */
  pitchDegThreshold?: number;
  /**
   * Soft enter: ‖δgaze‖ vs baseline (dx÷halfW, dy÷halfH). Default 0.18.
   * Field name kept for compat; semantics are signed-vector difference norm.
   */
  irisOffsetThreshold?: number;
  /** Soft exit yaw (hysteresis); default ≈ 0.7 × enter. Must be < enter. */
  yawDegExit?: number;
  /** Soft exit pitch; default ≈ 0.7 × enter. */
  pitchDegExit?: number;
  /** Soft exit ‖δgaze‖; default ≈ 0.7 × enter. */
  irisOffsetExit?: number;
  /** Hard absolute |yaw| (deg). Default 35. */
  hardYawDeg?: number;
  /** Hard absolute |pitch| (deg). Default 28. */
  hardPitchDeg?: number;
  /** Hard absolute ‖gaze‖ (halfW units). Default 0.32. */
  hardIrisOffset?: number;
  /**
   * Continuous soft/hard deviation required before emitting `looking_away`.
   * Default 3000ms (sustained episode, not a short thinking pause).
   */
  awayEnterMs?: number;
  /** Sustained OK required after away/presence before emitting `face_ok`. Default 800ms. */
  okRecoverMs?: number;
  /**
   * Debounce for `face_missing` / `multi_face` only (NOT looking_away).
   * Default 500ms.
   */
  presenceDebounceMs?: number;
  /** Neutral pose learning window at start / while OK. Default 2500ms. */
  baselineLearnMs?: number;
  /** EMA alpha on yaw/pitch/gaze vector in rules (0 = off). Default 0.35. */
  emaAlpha?: number;
  /** Optional cooldown after looking_away before another can fire. Default 0. */
  cooldownMs?: number;
  /**
   * @deprecated Prefer presenceDebounceMs. Mapped as frames × 125ms when
   * presenceDebounceMs is unset (assumes ~8Hz).
   */
  debounceFrames?: number;
};

export type CaptureTier = "low" | "sd" | "hd";

export type CaptureInfo = {
  width: number;
  height: number;
  frameRate: number;
  tier: CaptureTier;
  sampleHz: number;
  frameStride: number;
  awayEnterMs: number;
  sampleHzSource: "explicit" | "auto";
  /** B049：实际使用的推理 delegate（GPU 失败回退 CPU 时为 "CPU"） */
  delegate?: "GPU" | "CPU";
};

/** B044：基线校准进度（供 UI 显示"校准中 x/3s"）。 */
export type CalibrationInfo = {
  /** 基线是否已建立（≥20 样本取中位数后冻结） */
  ready: boolean;
  /** 校准学习开始时间（Unix ms），null 表示尚未采到首个单人样本 */
  startedAt: number | null;
  /** 已累积样本数 */
  samples: number;
  /** 需要的最少样本数 */
  requiredSamples: number;
  /** 校准建议持续时长（ms） */
  requiredMs: number;
};

export type CreateProctorSessionOptions = {
  video: HTMLVideoElement;
  /** Base URL for wasm + model, e.g. "/models" → /models/wasm, /models/face_landmarker.task */
  modelBaseUrl: string;
  /**
   * Sampling / emit rate for onSample.
   * If omitted, derived from camera fps + tier: clamp(round(fps/3), 6, 10) for hd.
   * Explicit value always wins over auto tiering.
   */
  sampleHz?: number;
  numFaces?: number;
  /** Include all 478 landmarks in samples (default false) */
  fullLandmarks?: boolean;
  gazeRules?: GazeRulesConfig;
  onEvent?: (event: ProctorEvent) => void;
  onSample?: (sample: GazeSample) => void;
  /** Prefer GPU delegate; falls back to CPU */
  preferGpu?: boolean;
};

export type ProctorSession = {
  start: () => Promise<void>;
  stop: () => void;
  exportGazeFile: () => Blob;
  exportEvents: () => Blob;
  getSamples: () => readonly GazeSample[];
  getEvents: () => readonly ProctorEvent[];
  isRunning: () => boolean;
  /** Latest MediaPipe result for debug overlay (null when idle). */
  getLastResult: () => unknown;
  /** Resolved capture / sampling knobs after start (null before start). */
  getCaptureInfo: () => CaptureInfo | null;
  /** B044：基线校准进度（供 UI 显示校准提示）。 */
  getCalibration: () => CalibrationInfo;
};

/** Landmark index helpers for overlay consumers */
export const IRIS = {
  LEFT_CENTER: 468,
  RIGHT_CENTER: 473,
} as const;

export const EYE_CONTOUR = {
  LEFT: [33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246] as const,
  RIGHT: [362, 382, 381, 380, 374, 373, 390, 249, 263, 466, 388, 387, 386, 385, 384, 398] as const,
};
