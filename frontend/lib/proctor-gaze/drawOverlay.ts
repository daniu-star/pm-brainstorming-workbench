import {
  DrawingUtils,
  FaceLandmarker,
  type FaceLandmarkerResult,
} from "@mediapipe/tasks-vision";
import { EYE_CONTOUR, IRIS } from "./types";

export type OverlayOptions = {
  /** Draw simplified full-face tessellation (default false) */
  drawFaceMesh?: boolean;
  /** Draw short gaze arrows from iris (default true) */
  drawGazeArrow?: boolean;
  /** Scale for gaze arrow length in CSS pixels (default 40) */
  gazeArrowScale?: number;
};

/**
 * Draw eye contours + iris centers (+ optional mesh / gaze arrows) on a canvas
 * sized to match the video element display size.
 */
export function drawDebugOverlay(
  ctx: CanvasRenderingContext2D,
  result: FaceLandmarkerResult | null,
  video: HTMLVideoElement,
  options: OverlayOptions = {},
): void {
  const { drawFaceMesh = false, drawGazeArrow = true, gazeArrowScale = 40 } = options;
  const w = ctx.canvas.width;
  const h = ctx.canvas.height;
  ctx.clearRect(0, 0, w, h);

  if (!result?.faceLandmarks?.length) return;

  const drawingUtils = new DrawingUtils(ctx);

  for (const landmarks of result.faceLandmarks) {
    if (drawFaceMesh) {
      drawingUtils.drawConnectors(landmarks, FaceLandmarker.FACE_LANDMARKS_TESSELATION, {
        color: "rgba(120,160,200,0.25)",
        lineWidth: 0.5,
      });
    }

    drawingUtils.drawConnectors(landmarks, FaceLandmarker.FACE_LANDMARKS_LEFT_EYE, {
      color: "#2dd4bf",
      lineWidth: 1.5,
    });
    drawingUtils.drawConnectors(landmarks, FaceLandmarker.FACE_LANDMARKS_RIGHT_EYE, {
      color: "#2dd4bf",
      lineWidth: 1.5,
    });
    drawingUtils.drawConnectors(landmarks, FaceLandmarker.FACE_LANDMARKS_LEFT_IRIS, {
      color: "#f59e0b",
      lineWidth: 1.5,
    });
    drawingUtils.drawConnectors(landmarks, FaceLandmarker.FACE_LANDMARKS_RIGHT_IRIS, {
      color: "#f59e0b",
      lineWidth: 1.5,
    });

    for (const idx of [IRIS.LEFT_CENTER, IRIS.RIGHT_CENTER]) {
      const lm = landmarks[idx];
      if (!lm) continue;
      ctx.beginPath();
      ctx.fillStyle = "#ef4444";
      ctx.arc(lm.x * w, lm.y * h, 3, 0, Math.PI * 2);
      ctx.fill();
    }

    if (drawGazeArrow) {
      drawIrisGazeArrow(ctx, landmarks, IRIS.LEFT_CENTER, EYE_CONTOUR.LEFT, w, h, gazeArrowScale);
      drawIrisGazeArrow(ctx, landmarks, IRIS.RIGHT_CENTER, EYE_CONTOUR.RIGHT, w, h, gazeArrowScale);
    }
  }
}

function drawIrisGazeArrow(
  ctx: CanvasRenderingContext2D,
  landmarks: { x: number; y: number; z: number }[],
  irisIdx: number,
  contourIdx: readonly number[],
  w: number,
  h: number,
  scale: number,
): void {
  const iris = landmarks[irisIdx];
  if (!iris) return;
  let sx = 0;
  let sy = 0;
  let n = 0;
  for (const i of contourIdx) {
    const p = landmarks[i];
    if (!p) continue;
    sx += p.x;
    sy += p.y;
    n += 1;
  }
  if (!n) return;
  const cx = sx / n;
  const cy = sy / n;
  const dx = iris.x - cx;
  const dy = iris.y - cy;
  const x0 = iris.x * w;
  const y0 = iris.y * h;
  const x1 = x0 + dx * scale * 8;
  const y1 = y0 + dy * scale * 8;

  ctx.beginPath();
  ctx.strokeStyle = "rgba(239,68,68,0.85)";
  ctx.lineWidth = 2;
  ctx.moveTo(x0, y0);
  ctx.lineTo(x1, y1);
  ctx.stroke();
}

/** Keep canvas bitmap size aligned with video client size (devicePixelRatio aware). */
export function syncOverlayCanvas(
  canvas: HTMLCanvasElement,
  video: HTMLVideoElement,
): void {
  const dpr = window.devicePixelRatio || 1;
  const cw = Math.max(1, Math.round(video.clientWidth * dpr));
  const ch = Math.max(1, Math.round(video.clientHeight * dpr));
  if (canvas.width !== cw || canvas.height !== ch) {
    canvas.width = cw;
    canvas.height = ch;
  }
  canvas.style.width = `${video.clientWidth}px`;
  canvas.style.height = `${video.clientHeight}px`;
}
