"use client";

import { useEffect, useState } from "react";
import { useSessionStore } from "@/store/sessionStore";
import { displayChatError, latestChatFailure, latestPartialBrainstormRun, queuedRunHint } from "@/lib/chatNotices";
import { shouldShowStartDiscussion } from "@/lib/phaseRouter";
import { MessageList } from "./MessageList";
import { RoleSelector } from "./RoleSelector";
import { InputBox } from "./InputBox";
import { InterviewBanner } from "./InterviewBanner";

export function ChatPanel() {
  const {
    phase,
    v2Phase,
    runs,
    currentSessionId,
    error,
    clearError,
    messages,
    messagesBySession,
    lastFailedSend,
    clearLastFailedSend,
    lastFailedRunId,
    retryFailedRun,
    sendMessage,
    isStreaming,
  } = useSessionStore();

  // 「开始讨论」的 once 守卫：点击后在回执到达（runs 出现 brainstorm）前禁用，防止重复提交
  const [discussionRequested, setDiscussionRequested] = useState(false);
  // 部分成功提示条被用户手动关闭的 Run（按 runId 记，换会话/换 Run 自动重新出现）
  const [dismissedPartialRunId, setDismissedPartialRunId] = useState<string | null>(null);
  // 排队计时：只在真的有 queued Run 时才启动定时器（5s tick），避免无意义重渲染
  const [queuedNowMs, setQueuedNowMs] = useState(() => Date.now());

  useEffect(() => {
    setDiscussionRequested(false);
    setDismissedPartialRunId(null);
  }, [currentSessionId]);

  useEffect(() => {
    // 请求失败时解除守卫，允许重试；成功后由 runs / 消息证据接管（横幅自然消失）
    if (error) setDiscussionRequested(false);
  }, [error]);

  const retrySend = () => {
    if (!lastFailedSend || isStreaming) return;
    const { content, targetRole, requestId } = lastFailedSend;
    clearLastFailedSend();
    // 复用首次的 request_id：同一 Idempotency-Key 让后端把重试识别为同一条命令（FE-03）
    sendMessage(content, targetRole, requestId ? { requestId } : undefined);
  };

  // 澄清结束进入脑暴后，尚无 brainstorm 产物时给一个显式的「开始讨论」入口，
  // 不点按钮就不会发请求（避免页面一进来就自动开跑）。
  // 产物判断以持久化消息为准（刷新后 runs 为空，仅靠内存会在 F5 后重复开 Run）。
  const sessionMessages = currentSessionId
    ? messagesBySession[currentSessionId] ?? []
    : [];
  // 失败 Run 不算「已有脑暴产物」：否则 401/超时后「开始讨论」会被永久隐藏，
  // 而空态文案仍在提示点这个不存在的按钮（CHAT-01）。
  const hasBrainstormRun = Object.values(runs).some(
    (run) => run.sessionId === currentSessionId && run.action === "brainstorm" && run.status !== "failed"
  );
  // 脑暴 Run 失败：错误条给「重试上一步」，复用 store.retryFailedRun（backend retry_run 支持）
  const chatFailure = latestChatFailure(runs, currentSessionId);
  const failedBrainstormRunId = (() => {
    if (!chatFailure) return null;
    const run = runs[chatFailure.runId];
    return run?.action === "brainstorm" ? chatFailure.runId : null;
  })();
  const retryBrainstorm = async () => {
    if (!failedBrainstormRunId || isStreaming) return;
    setDiscussionRequested(true);
    try {
      // retryFailedRun 失败时返回 false（store 已写 error）：保留错误条与按钮，
      // 不能 clearError 把刚写入的失败原因抹掉（FE-DX-01 / CHAT-02）。
      const ok = await retryFailedRun(failedBrainstormRunId);
      if (ok) clearError();
      else setDiscussionRequested(false);
    } catch {
      // 失败原因由 store 写回 error / runs，错误条保留可再次重试
      setDiscussionRequested(false);
    }
  };
  // 部分成功的脑暴 Run：有专家本轮失败（AC-14），给「重试失败专家」入口；
  // 它不是失败，不能塞进错误条（会显示空错误文案），单独用琥珀色提示条。
  const hasQueuedRun = Object.values(runs).some(
    (run) => run.sessionId === currentSessionId && run.status === "queued"
  );
  useEffect(() => {
    if (!hasQueuedRun) return;
    setQueuedNowMs(Date.now());
    const timer = setInterval(() => setQueuedNowMs(Date.now()), 5000);
    return () => clearInterval(timer);
  }, [hasQueuedRun]);
  // Worker 没起来时命令会一直排队：超过 45s 明确提示可执行的启动命令
  const queuedHint = queuedRunHint(runs, currentSessionId, { nowMs: queuedNowMs });

  const partialBrainstormRunId = latestPartialBrainstormRun(runs, currentSessionId);
  const showPartialBrainstorm =
    partialBrainstormRunId !== null && partialBrainstormRunId !== dismissedPartialRunId;
  const retryPartialBrainstorm = async () => {
    if (!partialBrainstormRunId || isStreaming) return;
    try {
      // 成功时 Run 状态离开 partial_succeeded，提示条自然消失；失败时保留以便再试
      await retryFailedRun(partialBrainstormRunId);
    } catch {
      // 失败原因由 store 写回 error / runs，提示条保留可再次重试
    }
  };
  const showStartDiscussion = shouldShowStartDiscussion({
    v2Phase,
    phase,
    hasBrainstormRun,
    messages: sessionMessages,
  });

  const requestDiscussion = () => {
    if (discussionRequested || isStreaming) return;
    setDiscussionRequested(true);
    sendMessage("开始讨论", "all");
  };

  const auditing = v2Phase ? v2Phase === "auditing" : phase === "interview";
  const chatTitle = v2Phase
    ? v2Phase === "auditing"
      ? "AI 审计官"
      : v2Phase === "audit_completed"
        ? "审计已完成"
        : v2Phase === "clarifying"
          ? "产品教练 · 思路梳理"
          : "产品脑暴群聊"
    : phase === "interview"
      ? "AI 面试官"
      : phase === "coach"
        ? "产品教练 · 思路梳理"
        : "产品脑暴群聊";

  // 刷新后 store.error 已经清空，但失败原因可以从后端恢复（见 store.hydrateFailedRun）：
  // 同一条错误条渲染「上次任务失败原因」，否则用户只看到消息没有回复（CHAT-03）。
  const displayError = displayChatError(error, runs, currentSessionId);

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* Chat header — group name */}
      <div className="h-11 bg-dark-800/60 border-b border-zinc-800/40 flex items-center px-4 shrink-0">
        <span className="text-sm font-medium text-zinc-300">
          {chatTitle}
        </span>
        <span className="ml-auto text-[10px] text-zinc-500">
          {messages.length} 条消息
        </span>
      </div>

      {displayError && (
        <div
          role="alert"
          className="mx-3 mt-2 px-3 py-2 bg-red-900/20 border border-red-800/40 rounded-xl text-red-400 text-sm flex justify-between items-center gap-2"
        >
          <span className="min-w-0 flex-1 break-words">{displayError}</span>
          <span className="flex shrink-0 items-center gap-1">
            {failedBrainstormRunId ? (
              <button
                onClick={() => void retryBrainstorm()}
                disabled={isStreaming}
                className="min-h-[40px] px-2 text-xs font-medium text-red-200 underline underline-offset-2 hover:text-red-100 disabled:cursor-not-allowed disabled:opacity-50"
              >
                重试上一步
              </button>
            ) : null}
            <button
              onClick={clearError}
              aria-label="关闭错误提示"
              className="text-red-300 hover:text-red-200 ml-2 shrink-0 min-h-[44px] min-w-[44px] flex items-center justify-center"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 6L6 18M6 6l12 12"/></svg>
            </button>
          </span>
        </div>
      )}

      {lastFailedSend && !isStreaming && (
        <div className="mx-3 mt-2 px-3 py-2 bg-amber-900/20 border border-amber-800/40 rounded-xl text-amber-300 text-sm flex justify-between items-center gap-2">
          <span className="truncate">
            发送失败{lastFailedSend.content ? `：${lastFailedSend.content.slice(0, 24)}${lastFailedSend.content.length > 24 ? "…" : ""}` : ""}
          </span>
          <span className="flex items-center gap-1 shrink-0">
            <button
              onClick={retrySend}
              className="min-h-[40px] px-3 text-xs font-medium text-amber-200 hover:text-amber-100 underline underline-offset-2"
            >
              重试
            </button>
            <button
              onClick={clearLastFailedSend}
              aria-label="放弃重试"
              className="min-h-[40px] min-w-[40px] flex items-center justify-center text-amber-300/70 hover:text-amber-200"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 6L6 18M6 6l12 12"/></svg>
            </button>
          </span>
        </div>
      )}

      {queuedHint && (
        <div
          role="status"
          className="mx-3 mt-2 px-3 py-2 bg-zinc-800/60 border border-zinc-700/60 rounded-xl text-zinc-300 text-sm break-words"
        >
          {queuedHint}
        </div>
      )}

      {showPartialBrainstorm && (
        <div className="mx-3 mt-2 px-3 py-2 bg-amber-900/20 border border-amber-800/40 rounded-xl text-amber-300 text-sm flex justify-between items-center gap-2">
          <span className="min-w-0 flex-1 break-words">有专家本轮失败，可重试失败专家补齐</span>
          <span className="flex shrink-0 items-center gap-1">
            <button
              onClick={() => void retryPartialBrainstorm()}
              disabled={isStreaming}
              className="min-h-[40px] px-3 text-xs font-medium text-amber-200 hover:text-amber-100 underline underline-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
            >
              重试失败专家
            </button>
            <button
              onClick={() => setDismissedPartialRunId(partialBrainstormRunId)}
              aria-label="关闭部分失败提示"
              className="min-h-[40px] min-w-[40px] flex items-center justify-center text-amber-300/70 hover:text-amber-200"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M18 6L6 18M6 6l12 12"/></svg>
            </button>
          </span>
        </div>
      )}

      {showStartDiscussion && (
        <div className="mx-3 mt-2 flex flex-wrap items-center gap-2 rounded-xl border border-brand-500/30 bg-brand-600/10 px-3 py-2">
          <span className="text-xs text-zinc-300">简报已就绪，四位专家可以开始讨论了。</span>
          <button
            onClick={requestDiscussion}
            disabled={isStreaming || discussionRequested}
            className="ml-auto inline-flex min-h-[40px] items-center justify-center rounded-lg border border-brand-500/40 bg-brand-600/25 px-3 text-xs font-medium text-brand-300 transition-colors hover:bg-brand-600/35 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {discussionRequested ? "提交中…" : "开始讨论"}
          </button>
        </div>
      )}

      {auditing || v2Phase === "audit_completed" || (!v2Phase && phase === "interview") ? (
        <InterviewBanner />
      ) : null}

      <MessageList />

      {phase === "brainstorm" && messages.length > 0 && <RoleSelector />}

      <InputBox />
    </div>
  );
}
