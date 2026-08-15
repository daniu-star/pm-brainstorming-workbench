import type { NormalizedLandmark } from "@mediapipe/tasks-vision";
import type { EyeCoords, GazeVector, HeadPose, Point2D, Point3D } from "./types";
import { EYE_CONTOUR, IRIS } from "./types";

export function toPoint2D(lm: NormalizedLandmark): Point2D {
  return { x: lm.x, y: lm.y };
}

export function pickContour(
  landmarks: NormalizedLandmark[],
  indices: readonly number[],
): Point2D[] {
  const out: Point2D[] = [];
  for (const i of indices) {
    const lm = landmarks[i];
    if (lm) out.push(toPoint2D(lm));
  }
  return out;
}

export function extractEyes(landmarks: NormalizedLandmark[]): {
  leftEye: EyeCoords;
  rightEye: EyeCoords;
} {
  const leftIris = landmarks[IRIS.LEFT_CENTER];
  const rightIris = landmarks[IRIS.RIGHT_CENTER];
  return {
    leftEye: {
      iris: leftIris ? toPoint2D(leftIris) : { x: 0, y: 0 },
      contour: pickContour(landmarks, EYE_CONTOUR.LEFT),
    },
    rightEye: {
      iris: rightIris ? toPoint2D(rightIris) : { x: 0, y: 0 },
      contour: pickContour(landmarks, EYE_CONTOUR.RIGHT),
    },
  };
}

function eyeMid(contour: Point2D[]): Point2D | null {
  if (!contour.length) return null;
  let sx = 0;
  let sy = 0;
  for (const p of contour) {
    sx += p.x;
    sy += p.y;
  }
  return { x: sx / contour.length, y: sy / contour.length };
}

function eyeHalfWidth(contour: Point2D[]): number {
  let minX = Infinity;
  let maxX = -Infinity;
  for (const p of contour) {
    minX = Math.min(minX, p.x);
    maxX = Math.max(maxX, p.x);
  }
  return Math.max((maxX - minX) / 2, 1e-4);
}

function eyeHalfHeight(contour: Point2D[]): number {
  let minY = Infinity;
  let maxY = -Infinity;
  for (const p of contour) {
    minY = Math.min(minY, p.y);
    maxY = Math.max(maxY, p.y);
  }
  return Math.max((maxY - minY) / 2, 1e-4);
}

/** Simple gaze vector: iris displacement from eye contour center (image-normalized). */
export function estimateGaze(eye: EyeCoords): GazeVector {
  const mid = eyeMid(eye.contour);
  if (!mid) return { dx: 0, dy: 0 };
  return { dx: eye.iris.x - mid.x, dy: eye.iris.y - mid.y };
}

/**
 * Signed gaze with anisotropic normalization so left/right and up/down are comparable:
 * dx /= half eye width, dy /= half eye height.
 * Positive dx ≈ image right; positive dy ≈ image down.
 */
export function estimateGazeNormalized(eye: EyeCoords): GazeVector {
  const mid = eyeMid(eye.contour);
  if (!mid || !eye.contour.length) return { dx: 0, dy: 0 };
  const halfW = eyeHalfWidth(eye.contour);
  const halfH = eyeHalfHeight(eye.contour);
  return {
    dx: (eye.iris.x - mid.x) / halfW,
    dy: (eye.iris.y - mid.y) / halfH,
  };
}

/**
 * Convert MediaPipe 4x4 facial transformation matrix (column-major flat array)
 * into approximate Euler degrees (pitch/yaw/roll).
 */
export function matrixToHeadPose(matrix: number[]): HeadPose {
  const r01 = matrix[4] ?? 0;
  const r11 = matrix[5] ?? 1;
  const r20 = matrix[2] ?? 0;
  const r21 = matrix[6] ?? 0;
  const r22 = matrix[10] ?? 1;

  const pitch = (Math.asin(Math.max(-1, Math.min(1, -r21))) * 180) / Math.PI;
  const yaw = (Math.atan2(r20, r22) * 180) / Math.PI;
  const roll = (Math.atan2(r01, r11) * 180) / Math.PI;
  return { pitch, yaw, roll };
}

export function toFullLandmarks(landmarks: NormalizedLandmark[]): Point3D[] {
  return landmarks.map((lm) => ({ x: lm.x, y: lm.y, z: lm.z }));
}
