import { estimateGazeNormalized } from "./landmarks";
import type {
  EyeCoords,
  GazeRulesConfig,
  GazeSample,
  GazeVector,
  HeadPose,
  LookingAwayReason,
  Point2D,
  ProctorEvent,
  ProctorEventType,
} from "./types";

/**
 * Resolved rule defaults.
 * Soft iris uses ‖signedGaze − baselineGaze‖ (half-eye-width units).
 * Defaults tuned lower so mild eye/head shifts trigger more easily.
 */
export type ResolvedGazeRulesConfig = {
  yawDegThreshold: number;
  pitchDegThreshold: number;
  irisOffsetThreshold: number;
  yawDegExit: number;
  pitchDegExit: number;
  irisOffsetExit: number;
  hardYawDeg: number;
  hardPitchDeg: number;
  hardIrisOffset: number;
  awayEnterMs: number;
  okRecoverMs: number;
  presenceDebounceMs: number;
  baselineLearnMs: number;
  emaAlpha: number;
  cooldownMs: number;
  /** @deprecated retained for callers that still pass it */
  debounceFrames: number;
};

const SOFT_YAW = 18;
const SOFT_PITCH = 14;
const SOFT_IRIS = 0.12;

export const GAZE_RULES_DEFAULTS: ResolvedGazeRulesConfig = {
  yawDegThreshold: SOFT_YAW,
  pitchDegThreshold: SOFT_PITCH,
  irisOffsetThreshold: SOFT_IRIS,
  yawDegExit: SOFT_YAW * 0.7,
  pitchDegExit: SOFT_PITCH * 0.7,
  irisOffsetExit: SOFT_IRIS * 0.7,
  hardYawDeg: 35,
  hardPitchDeg: 28,
  hardIrisOffset: 0.32,
  awayEnterMs: 1500,
  okRecoverMs: 500,
  presenceDebounceMs: 500,
  baselineLearnMs: 2500,
  emaAlpha: 0.35,
  cooldownMs: 0,
  debounceFrames: 4,
};

export function resolveGazeRulesConfig(
  rules?: GazeRulesConfig,
): ResolvedGazeRulesConfig {
  const merged: ResolvedGazeRulesConfig = {
    ...GAZE_RULES_DEFAULTS,
    ...pickDefined(rules),
  };

  // Backward compat: debounceFrames → presenceDebounceMs when unset
  if (
    rules?.presenceDebounceMs === undefined &&
    rules?.debounceFrames !== undefined
  ) {
    merged.presenceDebounceMs = rules.debounceFrames * 125;
  }

  // Ensure exit < enter for hysteresis
  if (merged.yawDegExit >= merged.yawDegThreshold) {
    merged.yawDegExit = merged.yawDegThreshold * 0.7;
  }
  if (merged.pitchDegExit >= merged.pitchDegThreshold) {
    merged.pitchDegExit = merged.pitchDegThreshold * 0.7;
  }
  if (merged.irisOffsetExit >= merged.irisOffsetThreshold) {
    merged.irisOffsetExit = merged.irisOffsetThreshold * 0.7;
  }

  return merged;
}

function pickDefined<T extends object>(obj?: T): Partial<T> {
  if (!obj) return {};
  const out: Partial<T> = {};
  for (const [k, v] of Object.entries(obj)) {
    if (v !== undefined) (out as Record<string, unknown>)[k] = v;
  }
  return out;
}

/** Features + baseline: head pose + signed gaze (halfW-normalized). */
export type GazeBaseline = {
  yaw: number;
  pitch: number;
  /** Signed gaze dx (÷ half eye width), average of both eyes. */
  gazeDx: number;
  /** Signed gaze dy (÷ half eye height), average of both eyes. */
  gazeDy: number;
  /** ‖(gazeDx, gazeDy)‖ — anisotropic magnitude for hard iris / logging. */
  irisOff: number;
};

export type GazeRulesState = {
  lastType: ProctorEventType | null;
  /** Presence candidate + timestamp (sample.t) */
  presenceKind: "face_missing" | "multi_face" | null;
  presenceSince: number | null;
  /** Soft/hard away continuous since (sample.t); null when not away */
  awaySince: number | null;
  /** Whether looking_away already emitted for current episode */
  lookingAwayEmitted: boolean;
  awayEpisodeStartedAt: number | null;
  /** Sustained OK since (for face_ok / recover) */
  okSince: number | null;
  softAwayLatched: boolean;
  baseline: GazeBaseline | null;
  baselineAccum: GazeBaseline & { n: number };
  baselineLearnStart: number | null;
  baselineFrozen: boolean;
  ema: GazeBaseline | null;
  lastCooldownUntil: number;
  lastReason: LookingAwayReason | null;
  lastIrisOff: number;
  lastYaw: number;
  lastPitch: number;
  lastGazeDx: number;
  lastGazeDy: number;
};

const ZERO_BASELINE: GazeBaseline = {
  yaw: 0,
  pitch: 0,
  gazeDx: 0,
  gazeDy: 0,
  irisOff: 0,
};

export function createGazeRulesState(): GazeRulesState {
  return {
    lastType: null,
    presenceKind: null,
    presenceSince: null,
    awaySince: null,
    lookingAwayEmitted: false,
    awayEpisodeStartedAt: null,
    okSince: null,
    softAwayLatched: false,
    baseline: null,
    baselineAccum: { ...ZERO_BASELINE, n: 0 },
    baselineLearnStart: null,
    baselineFrozen: false,
    ema: null,
    lastCooldownUntil: 0,
    lastReason: null,
    lastIrisOff: 0,
    lastYaw: 0,
    lastPitch: 0,
    lastGazeDx: 0,
    lastGazeDy: 0,
  };
}

export function emitIfChanged(
  state: GazeRulesState,
  type: ProctorEventType,
  t: number,
  detail?: string,
  payload?: Record<string, unknown>,
): ProctorEvent | null {
  if (state.lastType === type) return null;
  state.lastType = type;
  return { type, t, detail, payload };
}

function eyeCenter(contour: Point2D[]): Point2D | null {
  if (!contour.length) return null;
  let sx = 0;
  let sy = 0;
  for (const p of contour) {
    sx += p.x;
    sy += p.y;
  }
  return { x: sx / contour.length, y: sy / contour.length };
}

/** Iris offset magnitude with anisotropic halfW/halfH (same as ‖normalized gaze‖). */
export function irisOffsetRatio(
  iris: Point2D | undefined,
  contour: Point2D[] | undefined,
): number {
  if (!iris || !contour?.length) return 0;
  const c = eyeCenter(contour);
  if (!c) return 0;
  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  for (const p of contour) {
    minX = Math.min(minX, p.x);
    maxX = Math.max(maxX, p.x);
    minY = Math.min(minY, p.y);
    maxY = Math.max(maxY, p.y);
  }
  const halfW = Math.max((maxX - minX) / 2, 1e-4);
  const halfH = Math.max((maxY - minY) / 2, 1e-4);
  return Math.hypot((iris.x - c.x) / halfW, (iris.y - c.y) / halfH);
}

export function maxIrisOffset(sample: GazeSample): number {
  const leftOff = irisOffsetRatio(sample.leftEye?.iris, sample.leftEye?.contour);
  const rightOff = irisOffsetRatio(
    sample.rightEye?.iris,
    sample.rightEye?.contour,
  );
  return Math.max(leftOff, rightOff);
}

function gazeFromEyeOrFallback(
  eye: EyeCoords | undefined,
  fallback?: GazeVector,
): GazeVector {
  if (eye?.contour?.length && eye.iris) {
    return estimateGazeNormalized(eye);
  }
  return { dx: fallback?.dx ?? 0, dy: fallback?.dy ?? 0 };
}

/**
 * Mean signed gaze of both eyes (dx÷halfW, dy÷halfH).
 * Prefer eye landmarks; fall back to sample leftGaze/rightGaze if needed.
 */
export function sampleSignedGaze(sample: GazeSample): GazeVector {
  const left = gazeFromEyeOrFallback(sample.leftEye, sample.leftGaze);
  const right = gazeFromEyeOrFallback(sample.rightEye, sample.rightGaze);
  return {
    dx: (left.dx + right.dx) / 2,
    dy: (left.dy + right.dy) / 2,
  };
}

function featuresFromSample(sample: GazeSample): GazeBaseline {
  const gaze = sampleSignedGaze(sample);
  return {
    yaw: sample.headPose?.yaw ?? 0,
    pitch: sample.headPose?.pitch ?? 0,
    gazeDx: gaze.dx,
    gazeDy: gaze.dy,
    irisOff: Math.hypot(gaze.dx, gaze.dy),
  };
}

function applyEma(
  prev: GazeBaseline | null,
  next: GazeBaseline,
  alpha: number,
): GazeBaseline {
  if (!prev || alpha <= 0) return { ...next };
  const a = Math.min(1, Math.max(0, alpha));
  const gazeDx = a * next.gazeDx + (1 - a) * prev.gazeDx;
  const gazeDy = a * next.gazeDy + (1 - a) * prev.gazeDy;
  return {
    yaw: a * next.yaw + (1 - a) * prev.yaw,
    pitch: a * next.pitch + (1 - a) * prev.pitch,
    gazeDx,
    gazeDy,
    irisOff: Math.hypot(gazeDx, gazeDy),
  };
}

export type AwayAssessment = {
  isAway: boolean;
  reason: LookingAwayReason | null;
  hard: boolean;
  soft: boolean;
  yaw: number;
  pitch: number;
  irisOff: number;
  gazeDx: number;
  gazeDy: number;
  relYaw: number;
  relPitch: number;
  /** ‖δgaze‖ relative to baseline (halfW units). */
  relIris: number;
  relGazeDx: number;
  relGazeDy: number;
};

/**
 * Soft = relative-to-baseline + hysteresis; hard = absolute caps.
 * Soft iris uses ‖signedGaze − baselineGaze‖ (not |ΔirisOff|).
 * When baseline is missing, soft gaze uses ‖gaze‖ vs soft threshold.
 */
export function assessAway(
  features: GazeBaseline,
  state: GazeRulesState,
  cfg: ResolvedGazeRulesConfig,
): AwayAssessment {
  const base = state.baseline ?? ZERO_BASELINE;
  const relYaw = Math.abs(features.yaw - base.yaw);
  const relPitch = Math.abs(features.pitch - base.pitch);
  const relGazeDx = features.gazeDx - base.gazeDx;
  const relGazeDy = features.gazeDy - base.gazeDy;
  const relIris = Math.hypot(relGazeDx, relGazeDy);

  const hardHead =
    Math.abs(features.yaw) >= cfg.hardYawDeg ||
    Math.abs(features.pitch) >= cfg.hardPitchDeg;
  const hardIris = features.irisOff >= cfg.hardIrisOffset;
  const hard = hardHead || hardIris;

  let softHead: boolean;
  let softIris: boolean;
  if (state.softAwayLatched) {
    softHead =
      relYaw >= cfg.yawDegExit || relPitch >= cfg.pitchDegExit;
    softIris = relIris >= cfg.irisOffsetExit;
  } else {
    softHead =
      relYaw >= cfg.yawDegThreshold || relPitch >= cfg.pitchDegThreshold;
    softIris = relIris >= cfg.irisOffsetThreshold;
  }
  const soft = softHead || softIris;

  const head = hardHead || softHead;
  const iris = hardIris || softIris;
  let reason: LookingAwayReason | null = null;
  if (head && iris) reason = "both";
  else if (head) reason = "head";
  else if (iris) reason = "iris";

  return {
    isAway: hard || soft,
    reason,
    hard,
    soft,
    yaw: features.yaw,
    pitch: features.pitch,
    irisOff: features.irisOff,
    gazeDx: features.gazeDx,
    gazeDy: features.gazeDy,
    relYaw,
    relPitch,
    relIris,
    relGazeDx,
    relGazeDy,
  };
}

/**
 * Absolute hard / soft check without baseline (legacy helper for tests).
 * Soft uses absolute thresholds; prefer assessAway for full pipeline.
 */
export function isLookingAway(
  sample: GazeSample,
  cfgInput?: GazeRulesConfig | ResolvedGazeRulesConfig,
): boolean {
  const cfg = resolveGazeRulesConfig(cfgInput);
  const pose: HeadPose | undefined = sample.headPose;
  const features = featuresFromSample(sample);
  if (pose) {
    if (Math.abs(pose.yaw) >= cfg.yawDegThreshold) return true;
    if (Math.abs(pose.pitch) >= cfg.pitchDegThreshold) return true;
  }
  if (features.irisOff >= cfg.irisOffsetThreshold) return true;
  if (Math.abs(pose?.yaw ?? 0) >= cfg.hardYawDeg) return true;
  if (Math.abs(pose?.pitch ?? 0) >= cfg.hardPitchDeg) return true;
  if (features.irisOff >= cfg.hardIrisOffset) return true;
  return false;
}

function resetAwayEpisode(state: GazeRulesState): void {
  state.awaySince = null;
  state.lookingAwayEmitted = false;
  state.awayEpisodeStartedAt = null;
  state.softAwayLatched = false;
  state.baselineFrozen = false;
}

function resetPresence(state: GazeRulesState): void {
  state.presenceKind = null;
  state.presenceSince = null;
}

function updateBaseline(
  state: GazeRulesState,
  features: GazeBaseline,
  t: number,
  cfg: ResolvedGazeRulesConfig,
  allowLearn: boolean,
): void {
  if (state.baselineFrozen || !allowLearn) return;

  if (state.baselineLearnStart === null) {
    state.baselineLearnStart = t;
  }

  // Continue refining while learning window OR while OK with no baseline yet
  const learning =
    state.baseline === null ||
    t - state.baselineLearnStart < cfg.baselineLearnMs;

  if (!learning && state.baseline) return;

  state.baselineAccum.yaw += features.yaw;
  state.baselineAccum.pitch += features.pitch;
  state.baselineAccum.gazeDx += features.gazeDx;
  state.baselineAccum.gazeDy += features.gazeDy;
  state.baselineAccum.n += 1;

  const n = state.baselineAccum.n;
  const gazeDx = state.baselineAccum.gazeDx / n;
  const gazeDy = state.baselineAccum.gazeDy / n;
  state.baseline = {
    yaw: state.baselineAccum.yaw / n,
    pitch: state.baselineAccum.pitch / n,
    gazeDx,
    gazeDy,
    irisOff: Math.hypot(gazeDx, gazeDy),
  };
}

function awayPayload(
  state: GazeRulesState,
  assessment: AwayAssessment,
  t: number,
): Record<string, unknown> {
  const startedAt = state.awayEpisodeStartedAt ?? state.awaySince ?? t;
  return {
    reason: assessment.reason,
    irisOff: assessment.irisOff,
    gazeDx: assessment.gazeDx,
    gazeDy: assessment.gazeDy,
    yaw: assessment.yaw,
    pitch: assessment.pitch,
    relYaw: assessment.relYaw,
    relPitch: assessment.relPitch,
    relIris: assessment.relIris,
    relGazeDx: assessment.relGazeDx,
    relGazeDy: assessment.relGazeDy,
    hard: assessment.hard,
    startedAt,
    durationMs: t - startedAt,
  };
}

function faceOkPayload(
  state: GazeRulesState,
  t: number,
): Record<string, unknown> {
  const startedAt = state.awayEpisodeStartedAt;
  const payload: Record<string, unknown> = {
    irisOff: state.lastIrisOff,
    gazeDx: state.lastGazeDx,
    gazeDy: state.lastGazeDy,
    yaw: state.lastYaw,
    pitch: state.lastPitch,
  };
  if (startedAt != null) {
    payload.startedAt = startedAt;
    payload.durationMs = t - startedAt;
    if (state.lastReason) payload.reason = state.lastReason;
  }
  return payload;
}

/**
 * Evaluate face/gaze rules against a sample using sample.t timestamps.
 * Returns at most one event when state changes.
 */
export function evaluateGazeRules(
  sample: GazeSample,
  state: GazeRulesState,
  rules?: GazeRulesConfig,
): ProctorEvent | null {
  const cfg = resolveGazeRulesConfig(rules);
  const t = sample.t;
  const n = sample.faceCount;

  if (n === 0) {
    resetAwayEpisode(state);
    state.okSince = null;
    state.ema = null;
    if (state.presenceKind !== "face_missing") {
      state.presenceKind = "face_missing";
      state.presenceSince = t;
    }
    const since = state.presenceSince ?? t;
    if (t - since >= cfg.presenceDebounceMs) {
      return emitIfChanged(state, "face_missing", t, "No face detected");
    }
    return null;
  }

  if (n >= 2) {
    resetAwayEpisode(state);
    state.okSince = null;
    state.ema = null;
    if (state.presenceKind !== "multi_face") {
      state.presenceKind = "multi_face";
      state.presenceSince = t;
    }
    const since = state.presenceSince ?? t;
    if (t - since >= cfg.presenceDebounceMs) {
      return emitIfChanged(state, "multi_face", t, `faceCount=${n}`, {
        faceCount: n,
      });
    }
    return null;
  }

  // Single face
  resetPresence(state);

  const raw = featuresFromSample(sample);
  state.ema = applyEma(state.ema, raw, cfg.emaAlpha);
  const features = state.ema;

  state.lastYaw = features.yaw;
  state.lastPitch = features.pitch;
  state.lastIrisOff = features.irisOff;
  state.lastGazeDx = features.gazeDx;
  state.lastGazeDy = features.gazeDy;

  const assessment = assessAway(features, state, cfg);
  // Soft needs a learned baseline; before that only hard caps start away
  // (otherwise habitual non-zero gaze vs zero vector freezes learning).
  const baselineReady = state.baseline !== null;
  const isAway = assessment.hard || (baselineReady && assessment.soft);
  state.softAwayLatched = Boolean(
    assessment.hard || (baselineReady && assessment.soft),
  );
  if (assessment.reason) state.lastReason = assessment.reason;

  if (isAway) {
    state.okSince = null;

    if (state.awaySince === null) {
      state.awaySince = t;
      state.awayEpisodeStartedAt = t;
      // Freeze immediately so baseline cannot drift toward the away pose during enter window
      state.baselineFrozen = true;
    }

    // Do not learn baseline while away (pending or emitted)
    if (
      !state.lookingAwayEmitted &&
      t - state.awaySince >= cfg.awayEnterMs
    ) {
      if (cfg.cooldownMs > 0 && t < state.lastCooldownUntil) {
        return null;
      }
      state.lookingAwayEmitted = true;
      state.lastCooldownUntil = t + cfg.cooldownMs;
      return emitIfChanged(
        state,
        "looking_away",
        t,
        "Gaze/head away from screen",
        awayPayload(state, assessment, t),
      );
    }
    return null;
  }

  // Learn / refine baseline only while face_ok path (not away)
  updateBaseline(state, features, t, cfg, !state.baselineFrozen);

  // Not away — clear pending short deviation
  const hadEpisode = state.lookingAwayEmitted;
  const episodeStartedAt = state.awayEpisodeStartedAt;
  state.awaySince = null;
  state.softAwayLatched = false;

  if (hadEpisode) {
    // Keep episode markers until face_ok so payload can include duration
    state.awayEpisodeStartedAt = episodeStartedAt;
    state.baselineFrozen = true;
    if (state.okSince === null) state.okSince = t;
    if (t - state.okSince >= cfg.okRecoverMs) {
      const ev = emitIfChanged(
        state,
        "face_ok",
        t,
        "Primary face looking toward screen",
        faceOkPayload(state, t),
      );
      state.lookingAwayEmitted = false;
      state.awayEpisodeStartedAt = null;
      state.baselineFrozen = false;
      // Resume baseline refinement after recover
      state.baselineLearnStart = t;
      return ev;
    }
    return null;
  }

  // No looking_away episode — normal face_ok path (e.g. after presence)
  state.lookingAwayEmitted = false;
  state.awayEpisodeStartedAt = null;
  state.baselineFrozen = false;
  if (state.okSince === null) state.okSince = t;
  if (t - state.okSince >= cfg.okRecoverMs) {
    return emitIfChanged(
      state,
      "face_ok",
      t,
      "Primary face looking toward screen",
      {
        irisOff: features.irisOff,
        gazeDx: features.gazeDx,
        gazeDy: features.gazeDy,
        yaw: features.yaw,
        pitch: features.pitch,
      },
    );
  }
  return null;
}
