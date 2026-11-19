"use client";

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useParams, useSearchParams } from "next/navigation";
import Link from "next/link";
import { useSessionStore } from "@/store/sessionStore";
import { isApiError } from "@/lib/api";
import type { CommandReceipt, StartAuditRecovery } from "@/lib/contracts.generated";
import {
  AUDIT_LOAD_ERROR_TEXT,
  AUDIT_LOADING_TEXT,
  AUDIT_QUESTION_HARD_LIMIT,
  AUDIT_STATUS_CLASSES,
  ASSESSMENT_CLASSES,
  MAX_ANSWER_CHARS,
  NOT_GENERATED,
  answeredCount,
  assessmentLabel,
  auditErrorText,
  auditStatusLabel,
  canControlAudit,
  canRetryContinuation,
  canSubmitAnswer,
  CLOSING_POLL_DELAYS_MS,
  CONTINUATION_NOTICE_TEXT,
  controlStatusHint,
  endReasonLabel,
  isAuditFinished,
  normalizeAudit,
  pickCurrentQuestion,
  questionOrdinalText,
  resolveAnswerText,
  resolveAuditEntryState,
  resolveClosingRecoveryState,
  resolveClosingRetryTarget,
  validateAnswerInput,
} from "@/lib/auditView";
import type { NormalizedAudit, NormalizedQuestion } from "@/lib/auditView";
import { canStartAuditPhase } from "@/lib/phaseRouter";
import { BrainIcon, ShieldIcon } from "@/components/icons";

/** 200 的 StartAuditRecovery 与 202 的 RunReceipt / ControlReceipt 判别 */
function isStartAuditRecovery(receipt: CommandReceipt): receipt is StartAuditRecovery {
  return !("run_id" in receipt) && !("control_id" in receipt);
}

function Chip({ text, className }: { text: string; className: string }) {
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium ${className}`}
    >
      {text}
    </span>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-zinc-700/40 bg-dark-800/40 p-4">
      <h3 className="mb-2 text-xs font-semibold tracking-wide text-zinc-400">{title}</h3>
      {children}
    </section>
  );
}

function StringList({ items }: { items: string[] }) {
  if (items.length === 0) {
    return <div className="text-sm text-zinc-500">{NOT_GENERATED}</div>;
  }
  return (
    <ul className="space-y-1.5">
      {items.map((item, index) => (
        <li key={`${index}-${item.slice(0, 12)}`} className="text-sm leading-relaxed text-zinc-300">
          · {item}
        </li>
      ))}
    </ul>
  );
}

function ReportView({
  audit,
  onRestart,
  restarting = false,
}: {
  audit: NormalizedAudit;
  onRestart?: () => void;
  restarting?: boolean;
}) {
  const report = audit.report;
  const cancelled = audit.status === "cancelled";

  return (
    <div className="space-y-3">
      {cancelled ? (
        <div className="rounded-xl border border-zinc-600/50 bg-zinc-700/20 p-3 text-sm text-zinc-300">
          已取消，未生成结论
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2 text-xs text-zinc-400">
          <Chip
            text={`审计${auditStatusLabel(audit.status)}`}
            className={AUDIT_STATUS_CLASSES[audit.status]}
          />
          {report ? (
            <>
              <span>{`结束原因：${report.endReasonLabel}`}</span>
              <span>·</span>
              <span>{`问题数：${report.questionCount} / ${AUDIT_QUESTION_HARD_LIMIT}`}</span>
              <span>·</span>
              <span>{report.generatedBy === "fallback" ? "规则兜底生成" : "模型生成"}</span>
              {report.humanRevised ? <Chip text="人工修订" className="text-sky-300 border-sky-500/40" /> : null}
            </>
          ) : null}
        </div>
      )}

      {onRestart ? (
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={onRestart}
            disabled={restarting}
            className="min-h-[36px] rounded-lg border border-brand-500/40 bg-brand-600/20 px-3 text-xs font-medium text-brand-300 transition-colors hover:bg-brand-600/30 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {restarting ? "启动中…" : "重新审计"}
          </button>
          <span className="text-[11px] text-zinc-500">基于当前产品版本重新发起一轮审计</span>
        </div>
      ) : null}

      {!report ? (
        <Section title="审计报告">
          <div className="text-sm text-zinc-500">报告{NOT_GENERATED}</div>
        </Section>
      ) : (
        <>
          <Section title="维度评估">
            {report.dimensionStates.length === 0 ? (
              <div className="text-sm text-zinc-500">{NOT_GENERATED}</div>
            ) : (
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                {report.dimensionStates.map((dimension) => (
                  <div
                    key={dimension.key}
                    className="rounded-lg border border-zinc-700/40 bg-dark-900/40 p-2.5"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-medium text-zinc-300">{dimension.label}</span>
                      <Chip
                        text={dimension.assessmentLabel}
                        className={ASSESSMENT_CLASSES[dimension.assessment]}
                      />
                    </div>
                    {dimension.rationale ? (
                      <div className="mt-1 text-[11px] leading-relaxed text-zinc-500">
                        {dimension.rationale}
                      </div>
                    ) : null}
                  </div>
                ))}
              </div>
            )}
          </Section>

          <Section title="支持论据">
            <StringList items={report.supportedPoints} />
          </Section>
          <Section title="未解决缺口">
            <StringList items={report.unresolvedGaps} />
          </Section>
          <Section title="建议实验">
            <StringList items={report.recommendedExperiments} />
          </Section>
          <Section title="局限性说明">
            {report.limitations.trim().length > 0 ? (
              <div className="text-sm leading-relaxed text-zinc-300">{report.limitations}</div>
            ) : (
              <div className="text-sm text-zinc-500">{NOT_GENERATED}</div>
            )}
          </Section>
          {report.missingFields.length > 0 ? (
            <div className="text-[11px] text-amber-300/80">
              {`报告缺字段（显示为「${NOT_GENERATED}」）：${report.missingFields.join("、")}`}
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}

function QuestionHistory({
  audit,
  messages,
}: {
  audit: NormalizedAudit;
  messages: readonly { id: string; content: string }[];
}) {
  if (audit.questions.length === 0) {
    return <div className="text-sm text-zinc-500">暂无问题</div>;
  }
  return (
    <div className="space-y-2">
      {audit.questions.map((question) => {
        const answer = resolveAnswerText(messages, question.answerMessageId);
        return (
          <div
            key={question.id}
            className="rounded-lg border border-zinc-700/40 bg-dark-900/40 p-3"
          >
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-[11px] text-zinc-500">{question.ordinalText}</span>
              <Chip text={question.dimensionLabel} className="text-zinc-300 border-zinc-600/50" />
              <Chip
                text={question.assessmentLabel}
                className={ASSESSMENT_CLASSES[question.assessment]}
              />
            </div>
            <div className="mt-1.5 text-sm leading-relaxed text-zinc-200">
              {question.hasText ? question.questionText : "（问题文本未生成）"}
            </div>
            {answer ? (
              <div className="mt-1.5 border-l-2 border-brand-500/40 pl-2 text-[12px] leading-relaxed text-zinc-400">
                {answer}
              </div>
            ) : question.answerMessageId ? (
              <div className="mt-1.5 text-[11px] text-zinc-500">已回答（回答正文未加载）</div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

/** pending_continuation：上一问处理失败，提供「重试继续 / 结束审计」而不是空等 */
function ContinuationNotice({
  busy,
  onRetry,
  onEnd,
}: {
  busy: boolean;
  onRetry: () => void;
  onEnd: () => void;
}) {
  return (
    <Section title="当前问题">
      <div className="text-sm leading-relaxed text-amber-300">
        {CONTINUATION_NOTICE_TEXT}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={onRetry}
          disabled={busy}
          className="min-h-[40px] rounded-lg border border-amber-500/40 bg-amber-500/15 px-3 text-xs font-medium text-amber-300 hover:bg-amber-500/25 disabled:cursor-not-allowed disabled:opacity-50"
        >
          重试继续
        </button>
        <button
          type="button"
          onClick={onEnd}
          disabled={busy}
          className="min-h-[40px] rounded-lg border border-zinc-600/60 px-3 text-xs font-medium text-zinc-200 hover:bg-zinc-700/30 disabled:cursor-not-allowed disabled:opacity-50"
        >
          结束审计
        </button>
      </div>
    </Section>
  );
}

function CurrentQuestionCard({
  audit,
  submitting,
  onSubmit,
  onRetry,
  onEnd,
}: {
  audit: NormalizedAudit;
  submitting: boolean;
  onSubmit: (value: string) => Promise<void>;
  onRetry: () => void;
  onEnd: () => void;
}) {
  const question: NormalizedQuestion | null = pickCurrentQuestion(audit);
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);

  useEffect(() => {
    setText("");
    setError(null);
    setWarning(null);
  }, [audit.id, question?.id]);

  const handleSubmit = async () => {
    const validation = validateAnswerInput(text);
    if (!validation.ok) {
      setError(validation.error);
      return;
    }
    setError(null);
    setWarning(validation.warning);
    if (validation.warning) {
      setText(validation.value);
    }
    await onSubmit(validation.value);
  };

  if (!question) {
    if (canRetryContinuation(audit, false)) {
      return <ContinuationNotice busy={submitting} onRetry={onRetry} onEnd={onEnd} />;
    }
    return (
      <Section title="当前问题">
        <div className="text-sm text-zinc-500">
          {audit.status === "active" ? "本轮问题已回答完，等待审计收尾" : "当前没有待回答的问题"}
        </div>
      </Section>
    );
  }

  return (
    <Section title="当前问题">
      <div className="mb-1.5 flex flex-wrap items-center gap-2">
        <span className="text-[11px] font-medium text-brand-300">{question.ordinalText}</span>
        <Chip text={question.dimensionLabel} className="text-zinc-300 border-zinc-600/50" />
        <Chip
          text={question.assessmentLabel}
          className={ASSESSMENT_CLASSES[question.assessment]}
        />
      </div>
      <div className="text-sm leading-relaxed text-zinc-100">
        {question.hasText ? question.questionText : "（问题文本未生成）"}
      </div>

      <div className="mt-3">
        <textarea
          value={text}
          onChange={(event) => setText(event.target.value)}
          rows={4}
          maxLength={MAX_ANSWER_CHARS}
          disabled={submitting}
          placeholder="用你自己的话回答这个问题（1–8000 字）"
          className="w-full resize-y rounded-lg border border-zinc-700/60 bg-dark-900/60 px-3 py-2 text-sm leading-relaxed text-zinc-100 placeholder:text-zinc-600 focus:border-brand-500/60 focus:outline-none disabled:opacity-60"
        />
        <div className="mt-1 flex items-center justify-between gap-2">
          <span className="text-[11px] text-zinc-500">
            {`${text.length} / ${MAX_ANSWER_CHARS}`}
          </span>
          <button
            onClick={() => {
              void handleSubmit();
            }}
            disabled={submitting}
            className="min-h-[40px] rounded-lg border border-brand-500/40 bg-brand-600/20 px-4 text-xs font-medium text-brand-300 transition-colors hover:bg-brand-600/30 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {submitting ? "提交中…" : "提交回答"}
          </button>
        </div>
        {warning ? (
          <div className="mt-1.5 text-[11px] text-amber-300">{warning}</div>
        ) : null}
        {error ? (
          <div className="mt-1.5 rounded-lg border border-red-800/40 bg-red-900/20 px-2.5 py-1.5 text-[11px] text-red-400">
            {error}
          </div>
        ) : null}
      </div>
    </Section>
  );
}

function AuditPageContent() {
  const params = useParams();
  const searchParams = useSearchParams();
  const sessionIdParam = params?.id as string | undefined;
  const auditParam = searchParams?.get("audit") ?? null;

  const sessionId = useSessionStore((state) => state.currentSessionId);
  const sessionView = useSessionStore((state) => state.sessionView);
  const audits = useSessionStore((state) => state.audits);
  const auditErrors = useSessionStore((state) => state.auditErrors);
  const messagesBySession = useSessionStore((state) => state.messagesBySession);
  const productVersion = useSessionStore((state) => state.productVersion);
  const sessionLoading = useSessionStore((state) => state.sessionLoading);
  const legacyMode = useSessionStore((state) => state.legacyMode);
  const isStreaming = useSessionStore((state) => state.isStreaming);
  const loadSession = useSessionStore((state) => state.loadSession);
  const sendCommand = useSessionStore((state) => state.sendCommand);
  const hydrateAuditById = useSessionStore((state) => state.hydrateAuditById);
  const reloadSession = useSessionStore((state) => state.reloadSession);
  const v2Phase = useSessionStore((state) => state.v2Phase);
  const lastFailedRunId = useSessionStore((state) => state.lastFailedRunId);
  const retryFailedRun = useSessionStore((state) => state.retryFailedRun);

  const [starting, setStarting] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionNotice, setActionNotice] = useState<string | null>(null);
  const [confirmCancel, setConfirmCancel] = useState(false);
  // 已提交结束/取消请求但没有可订阅的收尾 Run：靠轮询等后端把审计推进到终态
  const [closeRequested, setCloseRequested] = useState(false);
  // closing 退避轮询耗尽（60s 封顶）后置 true：渲染「重试生成报告」恢复入口
  const [closingPollExhausted, setClosingPollExhausted] = useState(false);

  // 1) 进入页面先确保会话已加载（直接访问 /session/{id}/audit 时 store 可能是空的）
  useEffect(() => {
    if (!sessionIdParam) return;
    if (sessionId === sessionIdParam && sessionView) return;
    void loadSession(sessionIdParam);
  }, [sessionIdParam, sessionId, sessionView, loadSession]);

  // 2) 有审计 id 就拉快照：?audit=<id> 优先，否则用会话上的 current_audit_id
  const auditId = auditParam ?? sessionView?.current_audit_id ?? null;
  const [auditLoading, setAuditLoading] = useState(false);
  // hydrateAuditById 把握取失败写进 auditErrors 并返回 false：这里只负责收尾 loading
  const reloadAudit = useCallback(
    async (targetId: string): Promise<boolean> => {
      setAuditLoading(true);
      try {
        return await hydrateAuditById(targetId);
      } finally {
        setAuditLoading(false);
      }
    },
    [hydrateAuditById]
  );
  useEffect(() => {
    if (!auditId) return;
    void reloadAudit(auditId);
  }, [auditId, reloadAudit]);

  const audit = useMemo(
    () => (auditId ? normalizeAudit(audits[auditId]) : null),
    [auditId, audits]
  );
  const auditError = auditId ? auditErrors[auditId] ?? null : null;

  const sessionVersion = sessionView?.session_version ?? null;
  const expectedProductVersion = productVersion || sessionView?.current_product_version || 1;
  const currentMessageViews = sessionId ? messagesBySession[sessionId] ?? [] : [];
  const finished = isAuditFinished(audit);
  // 只有 brainstorming / audit_completed 允许发起（重新）审计（state_machine.py:43）
  const canRestart = finished && Boolean(v2Phase && canStartAuditPhase(v2Phase));
  // 读取失败 ≠ 没有审计：非 404 的失败只渲染错误态 + 重试，禁止渲染成「开始审计」
  const entryState = resolveAuditEntryState({
    auditId,
    hasAudit: Boolean(audit),
    auditError,
    auditLoading,
    sessionLoading,
    legacyMode,
    starting,
    v2Phase,
  });
  const canStart = entryState.canStart;

  const handleStartAudit = useCallback(async () => {
    setStarting(true);
    setActionError(null);
    setActionNotice(null);
    try {
      const receipt = await sendCommand(
        "start_audit",
        { expected_product_version: expectedProductVersion },
        { expectedSessionVersion: sessionVersion }
      );
      // 200 StartAuditRecovery：说明已有审计实例，直接续用，绝不重复建审计
      if (isStartAuditRecovery(receipt)) {
        setActionNotice("已有进行中的审计，已继续该审计");
        await hydrateAuditById(receipt.audit_id);
      } else {
        setActionNotice("审计已启动");
        // 重新审计 / 首次审计：立即拉新一轮审计快照（不必等轮询）
        if (receipt.audit_id) await hydrateAuditById(receipt.audit_id);
      }
    } catch (error) {
      setActionError(
        isApiError(error)
          ? auditErrorText(error.code, error.message)
          : auditErrorText(null, "启动审计失败")
      );
    } finally {
      setStarting(false);
    }
  }, [sendCommand, expectedProductVersion, sessionVersion, hydrateAuditById]);

  const handleAnswer = useCallback(
    async (answer: string) => {
      if (!audit) return;
      const question = pickCurrentQuestion(audit);
      if (!question) return;
      setSubmitting(true);
      setActionError(null);
      try {
        await sendCommand(
          "answer_audit",
          { audit_id: audit.id, question_id: question.id, answer },
          { expectedSessionVersion: sessionVersion }
        );
        setActionNotice("回答已提交");
      } catch (error) {
        setActionError(
          isApiError(error)
            ? auditErrorText(error.code, error.message)
            : auditErrorText(null, "回答提交失败")
        );
      } finally {
        setSubmitting(false);
      }
    },
    [audit, sendCommand, sessionVersion]
  );

  const handleControl = useCallback(
    async (action: "end_audit" | "cancel_audit") => {
      if (!audit) return;
      setActionError(null);
      setActionNotice(null);
      try {
        const receipt = await sendCommand(
          action,
          { audit_id: audit.id },
          { expectedSessionVersion: sessionVersion }
        );
        const controlStatus = "control_id" in receipt ? receipt.status : null;
        // 只有在没有收尾 Run 可订阅时才需要页面轮询兜底
        if ("control_id" in receipt && !receipt.related_run_id) setCloseRequested(true);
        setActionNotice(
          `${action === "end_audit" ? "结束审计请求" : "取消审计请求"}：${controlStatusHint(
            controlStatus
          )}`
        );
        setConfirmCancel(false);
        await hydrateAuditById(audit.id);
      } catch (error) {
        setActionError(
          isApiError(error)
            ? auditErrorText(error.code, error.message)
            : auditErrorText(null, "请求失败")
        );
      }
    },
    [audit, sendCommand, sessionVersion, hydrateAuditById]
  );

  // 刷新失败时也走同一错误态（store 已把失败写进 auditErrors）
  const handleRefresh = useCallback(async () => {
    const targetId = audit?.id ?? auditId;
    if (!targetId) return;
    setActionError(null);
    const ok = await reloadAudit(targetId);
    // 只有 store 里确实记了读取失败才报错（切会话导致的守卫失效不算失败）
    if (!ok && useSessionStore.getState().auditErrors[targetId]) {
      setActionError(AUDIT_LOAD_ERROR_TEXT);
    }
  }, [audit, auditId, reloadAudit]);

  // pending_continuation：重试上一问失败的 Run（store 会订阅新 Run 并回执）
  const handleRetryContinuation = useCallback(() => {
    if (!lastFailedRunId) {
      setActionError("没有可重试的任务，请点「刷新」重新读取审计状态。");
      return;
    }
    void retryFailedRun(lastFailedRunId);
  }, [lastFailedRunId, retryFailedRun]);

  const handleEndAudit = useCallback(() => {
    void handleControl("end_audit");
  }, [handleControl]);

  // closing 超时/失败后的「重试生成报告」：有 failed run 就重试该 run，否则重拉快照
  const handleRetryReport = useCallback(() => {
    setActionError(null);
    if (
      resolveClosingRetryTarget(lastFailedRunId) === "retry_failed_run" &&
      lastFailedRunId
    ) {
      void retryFailedRun(lastFailedRunId);
      return;
    }
    void handleRefresh();
  }, [lastFailedRunId, retryFailedRun, handleRefresh]);

  // 审计收尾（closing）但没有可订阅的收尾 Run 时，按 1s/2s/4s… 退避轮询快照（≤60s）。
  // 依赖 id/status 而非整个 audit 对象，避免每次轮询结果都重置退避序列。
  const auditIdentity = audit?.id ?? null;
  const auditStatus = audit?.status ?? null;
  const auditFinished = isAuditFinished(audit);
  // 阶段/审计对象变化后重新计时，避免上一轮的超时状态污染新状态
  useEffect(() => {
    setClosingPollExhausted(false);
  }, [auditIdentity, auditStatus]);
  useEffect(() => {
    const waitingClose = auditStatus === "closing" || (closeRequested && !auditFinished);
    if (!auditIdentity || !waitingClose || isStreaming) return;
    let cancelled = false;
    let index = 0;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const tick = () => {
      if (cancelled) return;
      if (index >= CLOSING_POLL_DELAYS_MS.length) {
        setClosingPollExhausted(true);
        return;
      }
      const delay = CLOSING_POLL_DELAYS_MS[index];
      index += 1;
      timer = setTimeout(() => {
        if (cancelled) return;
        void hydrateAuditById(auditIdentity).finally(() => {
          if (!cancelled) tick();
        });
      }, delay);
    };
    tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [auditIdentity, auditStatus, auditFinished, closeRequested, isStreaming, hydrateAuditById]);

  const closingRecovery = resolveClosingRecoveryState({
    status: auditStatus,
    pollExhausted: closingPollExhausted,
    hasFailedRun: Boolean(lastFailedRunId),
  });

  // VERSION_CONFLICT 等需要重新同步：提示 + 重载会话与审计快照
  useEffect(() => {
    if (!actionError) return;
    if (!actionError.includes("内容已在别处更新")) return;
    setActionNotice(actionError);
    setActionError(null);
    void reloadSession().then(() => (auditId ? hydrateAuditById(auditId) : undefined));
  }, [actionError, reloadSession, hydrateAuditById, auditId]);

  return (
    <div className="min-h-screen bg-dark-900 bg-mesh">
      <header className="sticky top-0 z-10 flex min-h-[52px] items-center gap-3 border-b border-zinc-800/50 bg-dark-900/80 px-4 backdrop-blur">
        <Link
          href={sessionIdParam ? `/session/${sessionIdParam}` : "/"}
          className="text-xs text-zinc-500 underline-offset-2 hover:text-zinc-300 hover:underline"
        >
          ← 返回工作台
        </Link>
        <div className="flex items-center gap-2">
          <ShieldIcon className="h-4 w-4 text-brand-300" />
          <h1 className="text-sm font-semibold text-zinc-100">专家审计</h1>
        </div>
        {audit ? (
          <div className="ml-auto flex flex-wrap items-center gap-2">
            <Chip text={audit.statusLabel} className={AUDIT_STATUS_CLASSES[audit.status]} />
            <span className="text-[11px] text-zinc-500">
              {`已回答 ${answeredCount(audit)} / ${AUDIT_QUESTION_HARD_LIMIT}`}
            </span>
            <span className="text-[11px] text-zinc-500">{`v${audit.productVersion}`}</span>
            <button
              onClick={() => {
                void handleRefresh();
              }}
              className="min-h-[36px] rounded-lg border border-zinc-700/60 px-2.5 text-[11px] text-zinc-400 hover:text-zinc-200"
            >
              刷新
            </button>
          </div>
        ) : null}
      </header>

      <main className="mx-auto w-full max-w-3xl space-y-3 px-4 py-4">
        {actionNotice ? (
          <div className="rounded-xl border border-emerald-700/40 bg-emerald-900/20 px-3 py-2 text-xs text-emerald-300">
            {actionNotice}
          </div>
        ) : null}
        {actionError ? (
          <div className="rounded-xl border border-red-800/40 bg-red-900/20 px-3 py-2 text-xs text-red-400">
            {actionError}
          </div>
        ) : null}

        {!audit ? (
          entryState.showLoadError ? (
            <Section title="审计信息">
              <div className="space-y-2">
                <p className="text-sm text-red-400">
                  {AUDIT_LOAD_ERROR_TEXT}
                  {entryState.loadErrorText ? `：${entryState.loadErrorText}` : ""}
                </p>
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    onClick={() => {
                      if (auditId) void reloadAudit(auditId);
                    }}
                    disabled={auditLoading}
                    className="min-h-[40px] rounded-lg border border-zinc-600/60 px-3 text-xs font-medium text-zinc-200 hover:bg-zinc-700/30 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {auditLoading ? "重试中…" : "重试"}
                  </button>
                </div>
              </div>
            </Section>
          ) : entryState.showNotReady ? (
            <Section title="审计信息">
              <div className="flex items-start gap-3">
                <BrainIcon className="mt-0.5 h-8 w-8 shrink-0 text-brand-300/70" />
                <div className="min-w-0 flex-1 space-y-2">
                  <p className="text-sm leading-relaxed text-zinc-300">
                    {entryState.notReadyText}
                  </p>
                  <Link
                    href={`/session/${sessionIdParam ?? ""}`}
                    className="inline-flex min-h-[40px] items-center rounded-lg border border-zinc-600/60 px-3 text-xs font-medium text-zinc-200 hover:bg-zinc-700/30"
                  >
                    回到工作台
                  </Link>
                </div>
              </div>
            </Section>
          ) : entryState.showStartPanel ? (
            <Section title="开始审计">
              <div className="flex items-start gap-3">
                <BrainIcon className="mt-0.5 h-8 w-8 shrink-0 text-brand-300/70" />
                <div className="min-w-0 flex-1 space-y-2">
                  <p className="text-sm leading-relaxed text-zinc-300">
                    审计会围绕六个维度连续提问（最多 {AUDIT_QUESTION_HARD_LIMIT} 问），
                    并在结束后生成带论据与缺口的评估报告。审计基于当前产品状态（v
                    {expectedProductVersion}）进行。
                  </p>
                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      onClick={() => {
                        void handleStartAudit();
                      }}
                      disabled={!canStart}
                      className="min-h-[44px] rounded-lg border border-brand-500/40 bg-brand-600/20 px-4 text-sm font-medium text-brand-300 transition-colors hover:bg-brand-600/30 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {starting ? "启动中…" : "开始审计"}
                    </button>
                    {sessionLoading ? (
                      <span className="text-[11px] text-zinc-500">正在读取会话…</span>
                    ) : null}
                  </div>
                  {!sessionView ? (
                    <div className="text-[11px] text-amber-300/80">
                      会话状态尚未加载，暂时无法启动审计。
                    </div>
                  ) : null}
                </div>
              </div>
            </Section>
          ) : (
            <Section title="审计信息">
              <div className="flex items-center gap-2 text-xs text-zinc-400">
                <span className="h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" />
                {AUDIT_LOADING_TEXT}
              </div>
            </Section>
          )
        ) : (
          <>
            {finished ? (
              <ReportView
                audit={audit}
                onRestart={canRestart ? handleStartAudit : undefined}
                restarting={starting}
              />
            ) : null}

            {!finished ? (
              <>
                {audit.status === "closing" ? (
                  <div className="space-y-2 rounded-xl border border-amber-700/40 bg-amber-900/20 px-3 py-2 text-xs text-amber-300">
                    <p>{closingRecovery.noticeText}</p>
                    {closingRecovery.showRecovery ? (
                      <div className="flex flex-wrap items-center gap-2 pt-0.5">
                        <button
                          onClick={handleRetryReport}
                          disabled={isStreaming}
                          className="min-h-[36px] rounded-lg border border-amber-600/50 px-3 text-xs font-medium text-amber-200 hover:bg-amber-800/30 disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          重试生成报告
                        </button>
                        <Link
                          href={`/session/${sessionIdParam ?? ""}`}
                          className="inline-flex min-h-[36px] items-center rounded-lg border border-zinc-600/60 px-3 text-xs font-medium text-zinc-200 hover:bg-zinc-700/30"
                        >
                          返回工作台
                        </Link>
                      </div>
                    ) : null}
                  </div>
                ) : null}
                {canSubmitAnswer(audit) ? (
                  <CurrentQuestionCard
                    audit={audit}
                    submitting={submitting || isStreaming}
                    onSubmit={handleAnswer}
                    onRetry={handleRetryContinuation}
                    onEnd={handleEndAudit}
                  />
                ) : canRetryContinuation(audit, false) ? (
                  <ContinuationNotice
                    busy={submitting || isStreaming}
                    onRetry={handleRetryContinuation}
                    onEnd={handleEndAudit}
                  />
                ) : (
                  <Section title="当前问题">
                    <div className="text-sm text-zinc-500">{`审计${auditStatusLabel(
                      audit.status
                    )}，暂无待回答的问题`}</div>
                  </Section>
                )}
              </>
            ) : null}

            <Section title={`问题记录（${audit.questions.length}）`}>
              <QuestionHistory audit={audit} messages={currentMessageViews} />
            </Section>

            {canControlAudit(audit) ? (
              <Section title="审计控制">
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    onClick={() => {
                      void handleControl("end_audit");
                    }}
                    disabled={submitting || isStreaming}
                    className="min-h-[40px] rounded-lg border border-zinc-600/60 px-3 text-xs font-medium text-zinc-200 hover:bg-zinc-700/30 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    结束审计并生成报告
                  </button>
                  {confirmCancel ? (
                    <>
                      <button
                        onClick={() => {
                          void handleControl("cancel_audit");
                        }}
                        disabled={submitting || isStreaming}
                        className="min-h-[40px] rounded-lg border border-red-700/60 bg-red-900/20 px-3 text-xs font-medium text-red-300 hover:bg-red-900/30 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        确认取消（不会生成结论）
                      </button>
                      <button
                        onClick={() => setConfirmCancel(false)}
                        className="min-h-[40px] rounded-lg border border-zinc-700/60 px-3 text-xs text-zinc-400 hover:text-zinc-200"
                      >
                        放弃
                      </button>
                    </>
                  ) : (
                    <button
                      onClick={() => setConfirmCancel(true)}
                      disabled={submitting || isStreaming}
                      className="min-h-[40px] rounded-lg border border-zinc-700/60 px-3 text-xs text-zinc-400 hover:text-red-300 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      取消审计
                    </button>
                  )}
                </div>
                <div className="mt-2 text-[11px] leading-relaxed text-zinc-500">
                  结束 / 取消是控制动作：若当前仍有生成任务在跑，回执状态为「{controlStatusHint(
                    "scheduled"
                  )}」。
                </div>
              </Section>
            ) : null}

            <Section title="审计信息">
              <div className="space-y-1 text-[11px] text-zinc-500">
                <div>{`审计 ID：${audit.id}`}</div>
                <div>{`产品版本：v${audit.productVersion} · 审计版本：${audit.auditVersion}`}</div>
                {audit.endReason ? <div>{`结束原因：${endReasonLabel(audit.endReason)}`}</div> : null}
                {audit.closeRequested ? <div>已请求收尾</div> : null}
              </div>
            </Section>
          </>
        )}
      </main>
    </div>
  );
}

export default function AuditPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center bg-dark-900 bg-mesh">
          <div className="h-10 w-10 animate-spin rounded-full border-2 border-brand-500/30 border-t-brand-500" />
        </div>
      }
    >
      <AuditPageContent />
    </Suspense>
  );
}
