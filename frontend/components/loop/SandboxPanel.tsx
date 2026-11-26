"use client";

/**
 * Loop Studio 的真实执行沙箱面板（第 38 批）。
 *
 * 交互：
 * 1. 首屏读 `GET /api/v2/sandbox`（开关 + 白名单）与 `GET /api/v2/loops/{id}/sandbox-runs`；
 * 2. 输入一行命令 → 前端预校验（长度 / 元字符）→ `POST .../sandbox-runs`；
 * 3. 每次运行都作为 `sandbox_run` 产物落档，面板里按最新在前列出退出码、耗时与输出尾部。
 *
 * 只读闭环（历史回看）不允许再跑命令——那会在旧闭环上追加产物。
 */
import { useCallback, useEffect, useMemo, useState } from "react";

import { errorMessage, isApiError } from "@/lib/api";
import {
  allowedCommandsText,
  checkCommand,
  formatCommandLine,
  getSandboxStatus,
  listSandboxRuns,
  MAX_COMMAND_ARG_LEN,
  MAX_TIMEOUT_SECONDS,
  parseCommandLine,
  runSandbox,
  sandboxBlockedHint,
  sandboxRunSummary,
  SANDBOX_STATUS_LABELS,
  SANDBOX_STATUS_TONES,
  tailText,
  type SandboxRunReceipt,
  type SandboxStatusView,
} from "@/lib/loopSandbox";

const TONE_CLASSES: Record<"green" | "red" | "amber" | "zinc", string> = {
  green: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  red: "bg-red-500/15 text-red-300 border-red-500/30",
  amber: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  zinc: "bg-zinc-500/15 text-zinc-400 border-zinc-500/30",
};

function Badge({ text, tone = "zinc" }: { text: string; tone?: keyof typeof TONE_CLASSES }) {
  return (
    <span className={`rounded-full border px-2 py-0.5 text-[10px] leading-relaxed ${TONE_CLASSES[tone]}`}>
      {text}
    </span>
  );
}

function OutputBlock({ label, text }: { label: string; text: string }) {
  if (!text) return null;
  return (
    <div className="mt-2">
      <p className="mb-1 text-[10px] uppercase tracking-wide text-zinc-500">{label}</p>
      <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-lg border border-zinc-800/60 bg-black/40 p-2 text-[10px] leading-relaxed text-zinc-300">
        {text}
      </pre>
    </div>
  );
}

export function SandboxPanel({ loopId, readOnly = false }: { loopId: string; readOnly?: boolean }) {
  const [status, setStatus] = useState<SandboxStatusView | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [runs, setRuns] = useState<SandboxRunReceipt[] | null>(null);
  const [runsError, setRunsError] = useState<string | null>(null);
  const [command, setCommand] = useState("");
  const [timeoutSeconds, setTimeoutSeconds] = useState("");
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const reloadRuns = useCallback(async () => {
    try {
      const page = await listSandboxRuns(loopId, { limit: 20 });
      setRuns(page.items ?? []);
      setRunsError(null);
    } catch (err) {
      setRuns([]);
      setRunsError(errorMessage(err));
    }
  }, [loopId]);

  useEffect(() => {
    let aborted = false;
    getSandboxStatus()
      .then((view) => {
        if (aborted) return;
        setStatus(view);
        setStatusError(null);
        if (view.timeout_seconds > 0) setTimeoutSeconds(String(view.timeout_seconds));
      })
      .catch((err) => {
        if (aborted) return;
        setStatus(null);
        setStatusError(errorMessage(err));
      });
    return () => {
      aborted = true;
    };
  }, []);

  useEffect(() => {
    let aborted = false;
    setRuns(null);
    listSandboxRuns(loopId, { limit: 20 })
      .then((page) => {
        if (aborted) return;
        setRuns(page.items ?? []);
        setRunsError(null);
      })
      .catch((err) => {
        if (aborted) return;
        setRuns([]);
        setRunsError(errorMessage(err));
      });
    return () => {
      aborted = true;
    };
  }, [loopId]);

  const blockedHint = sandboxBlockedHint(status);
  const argv = useMemo(() => parseCommandLine(command), [command]);
  const timeoutValue = timeoutSeconds.trim() === "" ? null : Number(timeoutSeconds);
  const timeoutValid =
    timeoutValue === null ||
    (Number.isFinite(timeoutValue) && timeoutValue >= 1 && timeoutValue <= MAX_TIMEOUT_SECONDS);
  const canRun = Boolean(!readOnly && !blockedHint && !busy && argv.length > 0 && timeoutValid);

  const submit = async () => {
    const check = checkCommand(argv);
    if (!check.ok) {
      setFormError(check.reason ?? "命令不合法。");
      return;
    }
    if (!timeoutValid) {
      setFormError(`超时必须是 1 到 ${MAX_TIMEOUT_SECONDS} 秒之间的整数。`);
      return;
    }
    setBusy(true);
    setFormError(null);
    setNotice(null);
    try {
      const receipt = await runSandbox(loopId, {
        command: argv,
        timeout_seconds: timeoutValue,
      });
      setNotice(`命令已执行：${SANDBOX_STATUS_LABELS[receipt.status]}（${sandboxRunSummary(receipt)}）`);
      setRuns((prev) => [receipt, ...(prev ?? [])]);
      await reloadRuns();
    } catch (err) {
      setFormError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section
      aria-label="执行沙箱"
      className="rounded-xl border border-zinc-800/60 bg-dark-800/40 px-4 py-3"
    >
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-[11px] font-semibold text-zinc-200">执行沙箱</h2>
        <div className="flex flex-wrap items-center gap-2">
          <Badge
            text={status?.enabled ? "已开启" : "未开启"}
            tone={status?.enabled ? "green" : "zinc"}
          />
          <Badge text={`白名单：${allowedCommandsText(status)}`} />
        </div>
      </div>

      <p className="mb-3 text-[10px] leading-relaxed text-zinc-500">
        命令以 argv 直传（不经过 shell），在独立临时目录里执行，超时会强制结束；每次运行都作为
        <code className="mx-1 rounded bg-black/40 px-1">sandbox_run</code>
        产物留档。这是<b>进程级隔离，不是安全边界</b>，只建议在本地 / 受信环境开启。
      </p>

      {statusError ? (
        <p role="alert" className="mb-3 text-[11px] leading-relaxed text-red-400">
          {statusError}
        </p>
      ) : null}

      {blockedHint ? (
        <p className="mb-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[11px] leading-relaxed text-amber-300">
          {blockedHint}
        </p>
      ) : null}

      {readOnly ? (
        <p className="mb-3 rounded-lg border border-zinc-700/60 bg-zinc-800/40 px-3 py-2 text-[11px] leading-relaxed text-zinc-400">
          历史闭环只读回看：要再执行命令，请先在「历史闭环」里切回当前闭环。
        </p>
      ) : null}

      <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
        <label className="flex-1 text-[10px] text-zinc-500">
          命令
          <input
            aria-label="沙箱命令"
            value={command}
            disabled={busy || Boolean(blockedHint) || readOnly}
            onChange={(event) => setCommand(event.target.value)}
            placeholder='例如 python -c "print(1+1)"'
            className="mt-1 min-h-[40px] w-full rounded-lg border border-zinc-700/60 bg-dark-900/60 px-2 font-mono text-[11px] text-zinc-200 disabled:opacity-60"
          />
        </label>
        <label className="text-[10px] text-zinc-500 sm:w-28">
          超时（秒）
          <input
            aria-label="超时秒数"
            type="number"
            min={1}
            max={MAX_TIMEOUT_SECONDS}
            value={timeoutSeconds}
            disabled={busy || Boolean(blockedHint) || readOnly}
            onChange={(event) => setTimeoutSeconds(event.target.value)}
            className="mt-1 min-h-[40px] w-full rounded-lg border border-zinc-700/60 bg-dark-900/60 px-2 text-[11px] text-zinc-200 disabled:opacity-60"
          />
        </label>
        <button
          type="button"
          disabled={!canRun}
          onClick={() => void submit()}
          className="min-h-[40px] rounded-lg bg-brand-500/90 px-3 text-[11px] font-medium text-white disabled:opacity-50"
        >
          {busy ? "执行中…" : "运行命令"}
        </button>
      </div>

      {argv.length > 0 && !blockedHint && !readOnly ? (
        <p className="mt-2 text-[10px] leading-relaxed text-zinc-500">
          将执行：<code className="rounded bg-black/40 px-1">{formatCommandLine(argv)}</code>
          {argv.some((arg) => arg.length > MAX_COMMAND_ARG_LEN) ? "（有参数超长）" : ""}
        </p>
      ) : null}

      {formError ? (
        <p role="alert" className="mt-2 text-[11px] leading-relaxed text-red-400">
          {formError}
        </p>
      ) : null}
      {notice ? (
        <p role="status" className="mt-2 text-[11px] leading-relaxed text-amber-300">
          {notice}
        </p>
      ) : null}

      <div className="mt-3 border-t border-zinc-800/60 pt-3">
        <div className="mb-2 flex items-center gap-2">
          <h3 className="text-[11px] font-semibold text-zinc-300">运行记录</h3>
          {runs ? <Badge text={`${runs.length} 条`} /> : <Badge text="读取中…" />}
        </div>
        {runsError ? (
          <p role="alert" className="text-[11px] leading-relaxed text-red-400">
            {runsError}
          </p>
        ) : null}
        {runs && runs.length === 0 && !runsError ? (
          <p className="text-[11px] leading-relaxed text-zinc-500">还没有运行记录。</p>
        ) : null}
        <ul className="flex flex-col gap-2">
          {(runs ?? []).map((receipt) => {
            const stdout = tailText(receipt.stdout_tail);
            const stderr = tailText(receipt.stderr_tail);
            return (
              <li
                key={receipt.id}
                className="rounded-lg border border-zinc-800/60 bg-dark-900/40 px-3 py-2"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <Badge
                    text={SANDBOX_STATUS_LABELS[receipt.status] ?? receipt.status}
                    tone={SANDBOX_STATUS_TONES[receipt.status] ?? "zinc"}
                  />
                  <code className="text-[11px] text-zinc-200">{receipt.command_display}</code>
                </div>
                <p className="mt-1 text-[10px] leading-relaxed text-zinc-500">
                  {sandboxRunSummary(receipt)}
                  {receipt.truncated ? " · 输出已截断" : ""}
                </p>
                <OutputBlock label="stdout" text={stdout.text} />
                <OutputBlock label="stderr" text={stderr.text} />
              </li>
            );
          })}
        </ul>
      </div>
    </section>
  );
}

export default SandboxPanel;
