export type {
  Point2D,
  Point3D,
  EyeCoords,
  GazeVector,
  HeadPose,
  GazeSample,
  ProctorEventType,
  ProctorEvent,
  LookingAwayReason,
  GazeRulesConfig,
  CaptureTier,
  CaptureInfo,
  CalibrationInfo,
  CreateProctorSessionOptions,
  ProctorSession,
} from "./types";

export { IRIS, EYE_CONTOUR } from "./types";

export { createProctorSession, downloadBlob, buildSample } from "./createProctorSession";
export { exportGazeFile, exportEvents } from "./exportGazeFile";
export {
  evaluateGazeRules,
  createGazeRulesState,
  isLookingAway,
  resolveGazeRulesConfig,
  GAZE_RULES_DEFAULTS,
  BASELINE_MIN_SAMPLES,
  emitIfChanged,
  irisOffsetRatio,
  maxIrisOffset,
  sampleSignedGaze,
  assessAway,
  type GazeRulesState,
  type GazeBaselineAccum,
  type ResolvedGazeRulesConfig,
  type GazeBaseline,
  type AwayAssessment,
} from "./gazeRules";
export {
  drawDebugOverlay,
  syncOverlayCanvas,
  type OverlayOptions,
} from "./drawOverlay";
export {
  extractEyes,
  estimateGaze,
  estimateGazeNormalized,
  matrixToHeadPose,
} from "./landmarks";
export {
  tierFromVideoSettings,
  defaultSampleHz,
  frameStride,
  shouldSampleByVideoTime,
  resolveSampling,
  clamp,
  openCameraStream,
  IDEAL_CAMERA_CONSTRAINTS,
  FALLBACK_CAMERA_CONSTRAINTS,
  type ResolvedSampling,
} from "./sampling";
