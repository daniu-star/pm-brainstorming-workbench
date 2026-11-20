/**
 * 聊天区「失败提示」的纯逻辑（CHAT-03）。
 *
 * 背景：store 里的 ``lastFailedRunId`` 只活在内存里，刷新后丢失；现在由
 * ``loadSession -> hydrateFailedRun`` 从 ``GET /sessions/{id}/runs`` 恢复最近的
 * 失败任务。聊天区只关心「对话类」任务（clarify / brainstorm），闭环或审计的失败
 * 有其自己的页面提示，不应把它们的报错塞进群聊。
 */

export interface ChatRunFailure {
  sessionId: string;
  action: string | null;
  status: string;
  errorMessage: string | null;
  createdAt?: string;
}

const CHAT_ACTIONS = new Set(["clarify", "brainstorm"]);

/** 找最近一条「对话类」失败任务（同会话、failed、action ∈ clarify/brainstorm）。 */
export function latestChatFailure(
  runs: Record<string, ChatRunFailure | undefined>,
  sessionId: string | null | undefined
): { runId: string; errorMessage: string | null } | null {
  if (!sessionId) return null;
  let best: { runId: string; createdAt: string; errorMessage: string | null } | null = null;
  for (const [runId, run] of Object.entries(runs)) {
    if (!run || run.status !== "failed") continue;
    if (run.sessionId !== sessionId) continue;
    if (!run.action || !CHAT_ACTIONS.has(run.action)) continue;
    const createdAt = run.createdAt ?? "";
    if (!best || createdAt >= best.createdAt) {
      best = { runId, createdAt, errorMessage: run.errorMessage };
    }
  }
  return best ? { runId: best.runId, errorMessage: best.errorMessage } : null;
}

/** 错误条文案：当前请求错误优先，其次恢复到的最近对话失败。 */
export function displayChatError(
  error: string | null,
  runs: Record<string, ChatRunFailure | undefined>,
  sessionId: string | null | undefined
): string | null {
  if (error) return error;
  const failure = latestChatFailure(runs, sessionId);
  if (!failure) return null;
  return failure.errorMessage ?? "上一次任务执行失败，可重试上一步";
}
