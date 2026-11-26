"use client";

/**
 * Loop Studio 真实执行沙箱的客户端调用（第 38 批）。
 *
 * 服务端语义见 `backend/services/loop_sandbox.py`：
 * * **fail-closed**：`loop_sandbox_enabled` 默认关闭，且白名单为空时一律拒绝；
 * * argv 直传（`shell=False`），拒绝 shell 元字符；
 * * 每次运行都作为 `sandbox_run` 产物落档（`append_artifact`，不去重）；
 * * 这是**进程级隔离**，不是安全边界——只建议在本地 / 受信环境开启。
 */
import { apiV2 } from "@/lib/api";

export type SandboxRunStatus = "passed" | "failed" | "timeout";

export interface SandboxStatusView {
  enabled: boolean;
  allowed_commands: string[];
  timeout_seconds: number;
  max_output_bytes: number;
  note: string;
}

export interface SandboxRunReceipt {
  id: string;
  loop_id: string;
  status: SandboxRunStatus;
  command_display: string;
  exit_code: number | null;
  duration_ms: number;
  timeout_seconds: number;
  stdout_tail: string;
  stderr_tail: string;
  truncated: boolean;
  created_at: string;
}

export interface SandboxRunListPage {
  items: SandboxRunReceipt[];
}

/** 与后端 `SandboxRunPayload` 对齐：最多 12 个参数、每个 ≤200 字符。 */
export const MAX_COMMAND_ARGS = 12;
export const MAX_COMMAND_ARG_LEN = 200;
export const MAX_TIMEOUT_SECONDS = 60;

export const SANDBOX_STATUS_LABELS: Record<SandboxRunStatus, string> = {
  passed: "通过",
  failed: "失败",
  timeout: "超时",
};

export const SANDBOX_STATUS_TONES: Record<SandboxRunStatus, "green" | "red" | "amber"> = {
  passed: "green",
  failed: "red",
  timeout: "amber",
};

/** 浏览器里也要挡住的 shell 元字符（服务端同样会拒绝，这里只是提前给提示）。 */
const SHELL_METACHARS = ["&&", "||", ";", "|", ">", "<", "`", "$(", "\x00"];

/**
 * 把一行命令切成 argv。
 *
 * 支持 `"` / `'` 包裹与 `\` 转义（Windows 路径里的反斜杠原样保留，只有
 * 引号前的反斜杠才转义），这样用户可以直接粘贴带空格的路径。
 * 空行返回 `[]`（调用方据此禁用按钮）。
 */
export function parseCommandLine(text: string): string[] {
  const argv: string[] = [];
  let current = "";
  let started = false;
  let quote: '"' | "'" | null = null;

  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (quote) {
      if (ch === "\\" && i + 1 < text.length && text[i + 1] === quote) {
        current += quote;
        i += 1;
        continue;
      }
      if (ch === quote) {
        quote = null;
        continue;
      }
      current += ch;
      continue;
    }
    if (ch === '"' || ch === "'") {
      quote = ch;
      started = true;
      continue;
    }
    if (ch === " " || ch === "\t" || ch === "\n" || ch === "\r") {
      if (started) {
        argv.push(current);
        current = "";
        started = false;
      }
      continue;
    }
    current += ch;
    started = true;
  }
  if (started) argv.push(current);
  return argv;
}

/** argv → 可读命令行（含空格的参数用双引号包起来）。 */
export function formatCommandLine(argv: string[]): string {
  return argv
    .map((arg) => (arg === "" || /[\s"]/.test(arg) ? `"${arg.replace(/"/g, '\\"')}"` : arg))
    .join(" ");
}

export interface CommandCheck {
  ok: boolean;
  /** 不通过时的可操作提示（ok=true 时缺省）。 */
  reason?: string;
}

/** 前端预校验：与服务端的长度 / 元字符规则保持一致，减少一次 422 往返。 */
export function checkCommand(argv: string[]): CommandCheck {
  if (argv.length === 0) return { ok: false, reason: "先输入要执行的命令。" };
  if (argv.length > MAX_COMMAND_ARGS) {
    return { ok: false, reason: `参数太多：最多 ${MAX_COMMAND_ARGS} 个，当前 ${argv.length} 个。` };
  }
  for (const arg of argv) {
    if (arg.length === 0) return { ok: false, reason: "参数不能为空字符串。" };
    if (arg.length > MAX_COMMAND_ARG_LEN) {
      return { ok: false, reason: `单个参数太长：最多 ${MAX_COMMAND_ARG_LEN} 个字符。` };
    }
    for (const meta of SHELL_METACHARS) {
      if (arg.includes(meta)) {
        return { ok: false, reason: `命令里不能包含 shell 元字符「${meta}」，请拆成单个可执行文件加参数。` };
      }
    }
  }
  return { ok: true };
}

/** 沙箱不可用时的可操作提示（null 表示可用）。 */
export function sandboxBlockedHint(status: SandboxStatusView | null): string | null {
  if (!status) return "正在读取沙箱配置…";
  if (!status.enabled) {
    return "沙箱未开启：在 backend/.env 里设置 LOOP_SANDBOX_ENABLED=true 后重启服务（start.bat）。";
  }
  if (status.allowed_commands.length === 0) {
    return "沙箱已开启但没有白名单：设置 LOOP_SANDBOX_ALLOWED_COMMANDS=python,node 后重启服务。";
  }
  return null;
}

/** 一条运行结果的摘要（徽标旁的一行字）。 */
export function sandboxRunSummary(receipt: SandboxRunReceipt): string {
  if (receipt.status === "timeout") {
    return `超过 ${receipt.timeout_seconds} 秒被强制结束 · 耗时 ${formatDuration(receipt.duration_ms)}`;
  }
  const code = receipt.exit_code === null ? "未知" : String(receipt.exit_code);
  return `退出码 ${code} · 耗时 ${formatDuration(receipt.duration_ms)}`;
}

export function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  return `${Math.floor(seconds / 60)}m${Math.round(seconds % 60)}s`;
}

/** 输出尾部展示：折叠多余空行，超长时保留尾部（报错通常在最后）。 */
export function tailText(text: string, limit = 2000): { text: string; clipped: boolean } {
  const normalized = (text || "").replace(/\r\n/g, "\n").replace(/\n+$/g, "");
  if (normalized.length <= limit) return { text: normalized, clipped: false };
  return { text: `…${normalized.slice(normalized.length - limit)}`, clipped: true };
}

/** 白名单文案：`python, node`。 */
export function allowedCommandsText(status: SandboxStatusView | null): string {
  if (!status || status.allowed_commands.length === 0) return "未配置";
  return status.allowed_commands.join("、");
}

export async function getSandboxStatus(signal?: AbortSignal): Promise<SandboxStatusView> {
  return apiV2<SandboxStatusView>("/api/v2/sandbox", { signal });
}

export async function listSandboxRuns(
  loopId: string,
  options: { limit?: number; signal?: AbortSignal } = {},
): Promise<SandboxRunListPage> {
  const limit = options.limit ?? 20;
  return apiV2<SandboxRunListPage>(`/api/v2/loops/${encodeURIComponent(loopId)}/sandbox-runs`, {
    query: { limit },
    signal: options.signal,
  });
}

export async function runSandbox(
  loopId: string,
  payload: { command: string[]; timeout_seconds?: number | null },
): Promise<SandboxRunReceipt> {
  // 服务端最长跑 60s；客户端超时要留出余量，否则按钮会先报 TIMEOUT。
  const grace = (payload.timeout_seconds ?? 20) * 1000 + 10_000;
  return apiV2<SandboxRunReceipt>(`/api/v2/loops/${encodeURIComponent(loopId)}/sandbox-runs`, {
    method: "POST",
    body: payload,
    timeoutMs: grace,
  });
}
