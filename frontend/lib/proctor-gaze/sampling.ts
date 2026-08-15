import type { CaptureTier } from "./types";

/** Ideal camera constraints: lock ~30fps with HD ideal size. */
export const IDEAL_CAMERA_CONSTRAINTS: MediaTrackConstraints = {
  facingMode: "user",
  width: { ideal: 1280 },
  height: { ideal: 720 },
  frameRate: { ideal: 30, max: 30 },
};

/** Fallback when frameRate lock is rejected by the device. */
export const FALLBACK_CAMERA_CONSTRAINTS: MediaTrackConstraints = {
  facingMode: "user",
  width: { ideal: 1280 },
  height: { ideal: 720 },
};

/**
 * Map camera width/height to a capture tier.
 * hd: ≥1280 on long edge or ≥720 on short; sd: ≥640/480; else low.
 */
export function tierFromVideoSettings(
  width: number,
  height: number,
): CaptureTier {
  const w = Math.max(0, width);
  const h = Math.max(0, height);
  const longEdge = Math.max(w, h);
  const shortEdge = Math.min(w, h);
  if (longEdge >= 1280 || shortEdge >= 720) return "hd";
  if (longEdge >= 640 || shortEdge >= 480) return "sd";
  return "low";
}

/**
 * Default sampleHz from fps + tier.
 * HD: clamp(round(fps/3), 6, 10). SD/low use slightly lower bands.
 */
export function defaultSampleHz(fps: number, tier: CaptureTier): number {
  const safeFps = Number.isFinite(fps) && fps > 0 ? fps : 30;
  const base = Math.round(safeFps / 3);
  if (tier === "hd") return clamp(base, 6, 10);
  if (tier === "sd") return clamp(base, 5, 8);
  return clamp(base, 4, 6);
}

/** Frames between samples/detects: max(1, round(fps / sampleHz)). */
export function frameStride(fps: number, sampleHz: number): number {
  const safeFps = Number.isFinite(fps) && fps > 0 ? fps : 30;
  const hz = Math.max(1, sampleHz);
  return Math.max(1, Math.round(safeFps / hz));
}

/**
 * True when video timeline advanced enough for the next sample
 * (prefer over pure wall-clock when video timestamps are available).
 */
export function shouldSampleByVideoTime(
  lastSampleVideoTime: number,
  currentVideoTime: number,
  targetHz: number,
): boolean {
  if (lastSampleVideoTime < 0) return true;
  const hz = Math.max(1, targetHz);
  const minDelta = 1 / hz;
  return currentVideoTime - lastSampleVideoTime >= minDelta - 1e-6;
}

export function clamp(n: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, n));
}

export type ResolvedSampling = {
  tier: CaptureTier;
  fps: number;
  sampleHz: number;
  frameStride: number;
  sampleHzSource: "explicit" | "auto";
};

/**
 * Resolve sampleHz / stride from track settings.
 * Explicit sampleHz always wins over auto tiering.
 */
export function resolveSampling(options: {
  width: number;
  height: number;
  frameRate?: number;
  explicitSampleHz?: number;
}): ResolvedSampling {
  const tier = tierFromVideoSettings(options.width, options.height);
  const fps =
    options.frameRate && options.frameRate > 0 ? options.frameRate : 30;
  const sampleHzSource =
    options.explicitSampleHz !== undefined ? "explicit" : "auto";
  const sampleHz =
    options.explicitSampleHz !== undefined
      ? Math.max(1, options.explicitSampleHz)
      : defaultSampleHz(fps, tier);
  return {
    tier,
    fps,
    sampleHz,
    frameStride: frameStride(fps, sampleHz),
    sampleHzSource,
  };
}

/**
 * Open camera with 30fps ideal lock; fall back without frameRate if needed.
 * Browser-only helper (uses navigator.mediaDevices).
 */
export async function openCameraStream(): Promise<MediaStream> {
  try {
    return await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: IDEAL_CAMERA_CONSTRAINTS,
    });
  } catch {
    return await navigator.mediaDevices.getUserMedia({
      audio: false,
      video: FALLBACK_CAMERA_CONSTRAINTS,
    });
  }
}
