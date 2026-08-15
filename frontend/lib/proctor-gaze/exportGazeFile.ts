import type { GazeSample, ProctorEvent } from "./types";

/** Build a JSONL blob (one GazeSample per line). */
export function exportGazeFile(samples: readonly GazeSample[]): Blob {
  const lines = samples.map((s) => JSON.stringify(s));
  return new Blob([lines.join("\n") + (lines.length ? "\n" : "")], {
    type: "application/x-ndjson",
  });
}

/** Build a JSONL blob of proctor events. */
export function exportEvents(events: readonly ProctorEvent[]): Blob {
  const lines = events.map((e) => JSON.stringify(e));
  return new Blob([lines.join("\n") + (lines.length ? "\n" : "")], {
    type: "application/x-ndjson",
  });
}

/** Trigger a browser download for a Blob. */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
