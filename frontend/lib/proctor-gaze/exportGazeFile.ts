import type { GazeSample, ProctorEvent } from "./types";

/** B086：导出文件首行元数据。 */
export type GazeExportMeta = {
  exportedAt: string;
  sampleCount: number;
  eventCount: number;
};

function buildJsonlBlob(meta: GazeExportMeta, rows: readonly unknown[]): Blob {
  const lines = [JSON.stringify({ _meta: meta })];
  for (const row of rows) lines.push(JSON.stringify(row));
  return new Blob([lines.join("\n") + "\n"], {
    type: "application/x-ndjson",
  });
}

/**
 * Build a JSONL blob: first line is {"_meta":{exportedAt,sampleCount,eventCount}},
 * followed by one GazeSample per line.
 */
export function exportGazeFile(
  samples: readonly GazeSample[],
  options?: { eventCount?: number },
): Blob {
  return buildJsonlBlob(
    {
      exportedAt: new Date().toISOString(),
      sampleCount: samples.length,
      eventCount: options?.eventCount ?? 0,
    },
    samples,
  );
}

/** Build a JSONL blob of proctor events（同样带 _meta 首行）。 */
export function exportEvents(
  events: readonly ProctorEvent[],
  options?: { sampleCount?: number },
): Blob {
  return buildJsonlBlob(
    {
      exportedAt: new Date().toISOString(),
      sampleCount: options?.sampleCount ?? 0,
      eventCount: events.length,
    },
    events,
  );
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
