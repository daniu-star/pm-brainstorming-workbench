"""第 42 批：SSE 断线后切换轮询兜底 + 全局「正在重试」横幅。"""
from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent


def patch_file(path: pathlib.Path, pairs: list[tuple[str, str]], *, label: str) -> str:
    src = path.read_text(encoding="utf-8")
    for old, new in pairs:
        actual = src.count(old)
        assert actual == 1, f"{label}: expected 1 occurrence, found {actual}:\n{old[:120]}"
        src = src.replace(old, new)
    path.write_text(src, encoding="utf-8", newline="")
    return f"patched {label}"


# ---------------------------------------------------------------- 1. store：轮询兜底
store = ROOT / "store" / "sessionStore.ts"

POLL_BLOCK = '''  let runStreamCancel: (() => void) | null = null;

  // ---- 轮询兜底（第 42 批） ---------------------------------------------- //
  /**
   * SSE 连续重连失败时，改用轮询把任务进度与结果同步回来。
   *
   * 线上实测：Vercel → HF Space 链路偶发 21 秒断开（约 5% 请求），事件流会以
   * `eof` 收尾。旧实现重连 2 次后直接提示「事件流已断开，请刷新页面继续」，
   * 用户以为任务失败了，实际上后端还在跑。轮询 2.5 秒一次只读 GET 很便宜，
   * 终态再走 finalizeRun，和 SSE 共用同一条收口逻辑。
   */
  const RUN_POLL_INTERVAL_MS = 2_500;
  const RUN_POLL_MAX_TICKS = 240; // 2.5s × 240 = 10 分钟

  let runPollTimer: ReturnType<typeof setTimeout> | null = null;
  let runPollRunId: string | null = null;
  let runPollTicks = 0;

  function stopRunPolling(): void {
    if (runPollTimer) {
      clearTimeout(runPollTimer);
      runPollTimer = null;
    }
    runPollRunId = null;
    runPollTicks = 0;
  }

  function startRunPolling(runId: string, captured: SessionGuard): void {
    stopRunPolling();
    runPollRunId = runId;
    runPollTicks = 0;
    pushWarning("实时连接中断，已切换为轮询同步进度（任务仍在后台执行）");

    const tick = async (): Promise<void> => {
      if (!assertCurrent(captured) || runPollRunId !== runId) return;
      runPollTicks += 1;
      if (runPollTicks > RUN_POLL_MAX_TICKS) {
        stopRunPolling();
        set({ isStreaming: false, subscribingRunId: null });
        setError("任务仍在后台执行，请稍后刷新页面查看结果");
        return;
      }
      try {
        const run = await getRun(runId);
        if (!assertCurrent(captured) || runPollRunId !== runId) return;
        if (isTerminalRunStatus(run.status)) {
          stopRunPolling();
          await finalizeRun(runId, captured);
          return;
        }
      } catch {
        // 轮询本身就是抖动下的兜底：单次失败不打扰用户，下一轮继续。
      }
      if (!assertCurrent(captured) || runPollRunId !== runId) return;
      runPollTimer = setTimeout(() => {
        void tick();
      }, RUN_POLL_INTERVAL_MS);
    };

    void tick();
  }
'''

store_pairs: list[tuple[str, str]] = [
    ("  let runStreamCancel: (() => void) | null = null;\n", POLL_BLOCK),
    # finalizeRun：收口时停轮询
    (
        "    if (!sessionId) return;\n    reconnectAttempts.delete(runId);\n",
        "    if (!sessionId) return;\n    reconnectAttempts.delete(runId);\n    stopRunPolling();\n",
    ),
    # reconnectRun：重连耗尽 → 轮询（运行中分支）
    (
        '''      if (attempt >= 2) {
        reconnectAttempts.delete(runId);
        setError("事件流已断开，请刷新页面继续");
        set({ isStreaming: false, subscribingRunId: null });
        return;
      }''',
        '''      if (attempt >= 2) {
        // 不再把用户丢在错误页：切轮询继续同步进度（第 42 批）。
        reconnectAttempts.delete(runId);
        set({ subscribingRunId: null });
        startRunPolling(runId, captured);
        return;
      }''',
    ),
    # reconnectRun：重连期间 getRun 也失败 → 同样转轮询
    (
        '''      if (attempt >= 2) {
        reconnectAttempts.delete(runId);
        setError(errorMessage(error));
        set({ isStreaming: false, subscribingRunId: null });
        return;
      }''',
        '''      if (attempt >= 2) {
        // 事件流和状态查询都拿不到时，轮询是唯一还能自愈的路径。
        void error;
        reconnectAttempts.delete(runId);
        set({ subscribingRunId: null });
        startRunPolling(runId, captured);
        return;
      }''',
    ),
    # openRunStream：重新订阅成功即停轮询
    (
        '''  function openRunStream(runId: string): void {
    const state = get();''',
        '''  function openRunStream(runId: string): void {
    stopRunPolling();
    const state = get();''',
    ),
    # unsubscribe：显式退订时停轮询
    (
        '''    unsubscribe: () => {
      if (runStreamCancel) {
        runStreamCancel();
        runStreamCancel = null;
      }''',
        '''    unsubscribe: () => {
      stopRunPolling();
      if (runStreamCancel) {
        runStreamCancel();
        runStreamCancel = null;
      }''',
    ),
]
print(patch_file(store, store_pairs, label="store/sessionStore.ts"))

# ---------------------------------------------------------------- 2. 全局重试横幅
banner = ROOT / "components" / "system" / "ConnectionBanner.tsx"
banner.parent.mkdir(parents=True, exist_ok=True)
banner.write_text(
    '''"use client";

import { useEffect, useState } from "react";

import { subscribeRetryNotices, type RetryNotice } from "@/lib/api";

/**
 * 全局「正在自动重试」横幅（第 42 批）。
 *
 * 线上后端在 Hugging Face Space，前置代理偶发 21 秒断开；客户端会自动重试，
 * 但重试期间如果页面毫无反馈，用户只会以为「又报错了」。这条横幅把重试过程
 * 明确说出来，恢复后自动收起。
 */
export function ConnectionBanner(): React.ReactNode {
  const [notice, setNotice] = useState<RetryNotice | null>(null);

  useEffect(() => {
    let hideTimer: ReturnType<typeof setTimeout> | null = null;

    const unsubscribe = subscribeRetryNotices((next) => {
      if (hideTimer) {
        clearTimeout(hideTimer);
        hideTimer = null;
      }
      if (next) {
        setNotice(next);
        return;
      }
      setNotice((current) => {
        if (!current) return current;
        // 恢复后多留 1.5 秒，避免横幅一闪而过让人怀疑自己看错。
        hideTimer = setTimeout(() => setNotice(null), 1_500);
        return current;
      });
    });

    return () => {
      unsubscribe();
      if (hideTimer) clearTimeout(hideTimer);
    };
  }, []);

  if (!notice) return null;

  return (
    <div
      role="status"
      aria-live="polite"
      className="fixed inset-x-0 top-0 z-40 flex items-center justify-center gap-2 border-b border-amber-400/40 bg-amber-500/15 px-4 py-2 text-xs text-amber-100 backdrop-blur"
    >
      <span className="h-2 w-2 animate-pulse rounded-full bg-amber-300" aria-hidden="true" />
      <span>
        连接不稳定（{notice.reason}），正在自动重试 {notice.attempt}/{Math.max(notice.attempts - 1, 1)}
        …
      </span>
    </div>
  );
}
''',
    encoding="utf-8",
    newline="",
)
print("wrote components/system/ConnectionBanner.tsx")

# ---------------------------------------------------------------- 3. layout 挂载
layout = ROOT / "app" / "layout.tsx"
print(
    patch_file(
        layout,
        [
            (
                'import { AuthGate } from "@/components/auth/AuthGate";',
                'import { AuthGate } from "@/components/auth/AuthGate";\nimport { ConnectionBanner } from "@/components/system/ConnectionBanner";',
            ),
            (
                "        <ToastProvider>\n          <AuthGate>",
                "        <ToastProvider>\n          <ConnectionBanner />\n          <AuthGate>",
            ),
        ],
        label="app/layout.tsx",
    )
)

# ---------------------------------------------------------------- 4. tsconfig 渲染入口
for name in ("tsconfig.render.json", "tsconfig.test.json"):
    cfg_path = ROOT / name
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    files = cfg.get("files", [])
    added: list[str] = []
    candidates = (
        ["components/system/ConnectionBanner.tsx"]
        if name == "tsconfig.render.json"
        else []
    )
    for candidate in candidates:
        if candidate not in files:
            files.append(candidate)
            added.append(candidate)
    if added:
        cfg["files"] = files
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="")
        print(f"tsconfig {name}: +{added}")
    else:
        print(f"tsconfig {name}: no change")
