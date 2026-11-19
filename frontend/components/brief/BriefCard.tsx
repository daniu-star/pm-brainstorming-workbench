"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useSessionStore } from "@/store/sessionStore";
import {
  briefActionsVisible,
  briefPhaseNotice,
  briefReconfirmNotice,
  briefReviseAllowed,
  decideBriefDraftOnServerVersion,
  missingRequiredBriefFields,
} from "@/lib/briefDraft";
import {
  BRIEF_FIELDS,
  BRIEF_FIELD_LABELS,
  BRIEF_REQUIRED_FIELDS,
} from "@/lib/contracts.generated";
import type { BriefField, BriefFieldStatus, BriefStatus } from "@/lib/contracts.generated";
import { errorMessage, isApiError } from "@/lib/api";
import { toast } from "@/components/Toast";

const BRIEF_STATUS_LABELS: Record<BriefStatus, string> = {
  draft: "草稿",
  ready: "待确认",
  confirmed: "已确认",
  skipped: "已跳过",
};

const BRIEF_STATUS_CLASSES: Record<BriefStatus, string> = {
  draft: "text-zinc-300 border-zinc-600/50 bg-zinc-700/20",
  ready: "text-amber-300 border-amber-500/40 bg-amber-500/10",
  confirmed: "text-emerald-300 border-emerald-500/40 bg-emerald-500/10",
  skipped: "text-zinc-400 border-zinc-600/50 bg-zinc-700/20",
};

const FIELD_STATUS_LABELS: Record<BriefFieldStatus, string> = {
  unknown: "未提及",
  proposed: "待确认",
  confirmed: "已确认",
};

const FIELD_STATUS_CLASSES: Record<BriefFieldStatus, string> = {
  unknown: "text-zinc-500 border-zinc-700/50",
  proposed: "text-amber-300 border-amber-500/40 bg-amber-500/10",
  confirmed: "text-emerald-300 border-emerald-500/40 bg-emerald-500/10",
};

type Edits = Record<string, string>;

/**
 * 澄清简报卡：七字段 + 每字段 status + 缺失提示。
 * confirm_brief / skip_clarification / revise_brief 走 POST /sessions/{id}/commands，
 * 带 expected_session_version（payload 内额外带 expected_product_version）。
 */
export function BriefCard() {
  const {
    legacyMode,
    productState,
    productVersion,
    sessionView,
    sessionLoading,
    v2Phase,
    isStreaming,
    sendCommand,
    reloadSession,
  } = useSessionStore();

  const [editing, setEditing] = useState(false);
  const [edits, setEdits] = useState<Edits>({});
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const [errorText, setErrorText] = useState<string | null>(null);
  /** 服务端版本更新但本地仍有草稿：提示用户「覆盖 / 丢弃」 */
  const [staleNotice, setStaleNotice] = useState<string | null>(null);

  const brief = productState?.brief ?? null;
  const version = productState?.version ?? productVersion;
  const currentSessionVersion = sessionView?.session_version ?? null;

  // 用 ref 读取最新草稿，避免把 edits 放进 effect 依赖导致每次输入都触发版本判断
  const editsRef = useRef<Edits>({});
  useEffect(() => {
    editsRef.current = edits;
  }, [edits]);

  // 产品版本变化时：有草稿则保留并提示冲突，无草稿才清空（不再静默丢弃用户输入）。
  // 依赖产品状态版本而非全局 productVersion：切会话时全局版本会重置，容易被误判成服务端更新。
  useEffect(() => {
    const decision = decideBriefDraftOnServerVersion(editsRef.current);
    setErrorText(null);
    if (decision.keepEdits) {
      setStaleNotice(decision.notice);
      return;
    }
    setEdits({});
    setStaleNotice(null);
  }, [productState?.version]);

  const missingRequired = useMemo(
    () => missingRequiredBriefFields(brief, edits, BRIEF_REQUIRED_FIELDS),
    [brief, edits]
  );

  const changedFields = useMemo(() => {
    const result: Record<string, { value: string; status: BriefFieldStatus }> = {};
    if (!brief) return result;
    for (const field of BRIEF_FIELDS) {
      const next = edits[field];
      if (typeof next !== "string") continue;
      const current = brief[field]?.value ?? "";
      if (next === current) continue;
      result[field] = { value: next, status: "proposed" };
    }
    return result;
  }, [brief, edits]);

  if (legacyMode) return null;
  if (!brief && !sessionLoading) {
    // 非加载中也可能是产品状态尚未生成：给空态卡，避免整个简报区消失
    return (
      <div className="shrink-0 mx-3 mt-3 rounded-2xl border border-dashed border-zinc-700/50 bg-dark-800/40 px-3 py-3">
        <div className="text-xs text-zinc-400">暂无澄清简报</div>
        <div className="mt-1 text-[11px] leading-relaxed text-zinc-500">
          与产品教练对话后，七个字段会自动汇总到这里。
        </div>
      </div>
    );
  }
  if (!brief) {
    return (
      <div className="shrink-0 mx-3 mt-3 px-3 py-2 rounded-xl border border-zinc-700/40 bg-dark-800/40">
        <span className="text-xs text-zinc-500">正在读取澄清简报…</span>
      </div>
    );
  }

  const status = brief.status ?? "draft";
  const statusLabel = BRIEF_STATUS_LABELS[status] ?? status;
  const statusClass = BRIEF_STATUS_CLASSES[status] ?? BRIEF_STATUS_CLASSES.draft;
  const busy = pendingAction !== null;
  /**
   * Run 流式期间状态机不接受 confirm/skip/revise：按钮必须同步禁用，
   * 否则点击必被拒绝且会暴露内部阶段名（DX-04）。
   */
  const locked = busy || isStreaming;
  const isSkipped = status === "skipped";
  const isConfirmed = status === "confirmed";
  /**
   * UX-D2 阶段门控：confirm_brief / skip_clarification 只在 clarifying 阶段合法
   * （backend/domain/state_machine.py ACTION_PHASES），非澄清阶段渲染只读说明，
   * 避免点击必被状态机拒绝的按钮。
   */
  const phase = v2Phase ?? sessionView?.phase ?? null;
  const canConfirmOrSkip = briefActionsVisible(phase);
  const canRevise = briefReviseAllowed(phase);
  const phaseNotice = briefPhaseNotice(phase);
  const reconfirmNotice = briefReconfirmNotice({ status, phase });

  async function runCommand(
    label: string,
    action: "confirm_brief" | "skip_clarification" | "revise_brief",
    payload: Record<string, unknown>
  ) {
    if (locked) return;
    setPendingAction(label);
    setErrorText(null);
    try {
      await sendCommand(action, payload, {
        expectedSessionVersion: currentSessionVersion,
      });
      if (action === "confirm_brief") toast.success("简报已确认");
      else if (action === "skip_clarification") toast.success("已跳过澄清");
      else if (phase === "audit_completed")
        // 后端 revise_brief 会把 phase 写回 brainstorming（command_service.py），
        // 这是用户可感知的阶段回退，必须显式提示而不是静默。
        toast.success("修订已提交，会话阶段已回退到「脑暴」，需要重新审计");
      else toast.success("修订已提交");
      setEdits({});
      setEditing(false);
    } catch (error) {
      if (isApiError(error) && error.code === "VERSION_CONFLICT") {
        // 版本冲突：提示并重新加载当前会话（服务端为准）
        toast.error("内容已在别处更新，已刷新");
        setEdits({});
        setEditing(false);
        await reloadSession();
        return;
      }
      const message = errorMessage(error);
      setErrorText(message);
      toast.error(message);
    } finally {
      setPendingAction(null);
    }
  }

  function handleConfirm() {
    // 服务端 brief 必填齐全不代表本地编辑后的值齐全：edits 会覆盖服务端字段。
    if (missingRequired.length > 0) {
      setErrorText(
        `还缺必填字段：${missingRequired.map((field) => BRIEF_FIELD_LABELS[field] ?? field).join("、")}`
      );
      return;
    }
    const payload: Record<string, unknown> = { expected_product_version: version };
    if (edits && Object.keys(edits).length > 0) payload.fields = edits;
    void runCommand("confirm_brief", "confirm_brief", payload);
  }

  function handleSkip() {
    void runCommand("skip_clarification", "skip_clarification", {});
  }

  function handleRevise() {
    if (Object.keys(changedFields).length === 0) {
      toast.error("还没有修改任何字段");
      return;
    }
    void runCommand("revise_brief", "revise_brief", {
      expected_product_version: version,
      fields: changedFields,
    });
  }

  return (
    <div className="shrink-0 mx-3 mt-3 rounded-2xl border border-zinc-700/50 bg-dark-800/60 overflow-hidden">
      <div className="flex items-center gap-2 px-3 py-2 border-b border-zinc-800/50">
        <span className="text-sm font-medium text-zinc-200">澄清简报</span>
        <span className={`text-[10px] leading-none px-2 py-1 rounded-full border ${statusClass}`}>
          {statusLabel}
        </span>
        {version >= 1 ? (
          <span className="text-[10px] text-zinc-500 px-1.5 py-0.5 rounded border border-zinc-700/50">
            v{version}
          </span>
        ) : null}
        <button
          type="button"
          onClick={() => setEditing((value) => !value)}
          disabled={busy || isSkipped || !canRevise}
          className="ml-auto min-h-[32px] px-2 text-[11px] text-zinc-400 hover:text-zinc-200 disabled:opacity-50"
        >
          {editing ? "收起编辑" : "编辑"}
        </button>
      </div>

      {isSkipped ? (
        <div className="px-3 py-3 text-xs text-zinc-400">
          已跳过澄清，直接进入脑暴。缺失字段将以假设参与后续讨论，不会回填简报。
        </div>
      ) : (
        <div className="px-3 py-2.5 space-y-1.5 max-h-64 overflow-y-auto">
          {BRIEF_FIELDS.map((field) => {
            const item: BriefField | undefined = brief[field];
            const fieldStatus: BriefFieldStatus = item?.status ?? "unknown";
            const required = BRIEF_REQUIRED_FIELDS.includes(field);
            const value = item?.value ?? "";
            return (
              <div key={field} className="flex items-start gap-2">
                <div className="w-[72px] shrink-0 pt-0.5">
                  <span className="text-[11px] text-zinc-400">
                    {BRIEF_FIELD_LABELS[field] ?? field}
                    {required ? <span className="text-red-400">*</span> : null}
                  </span>
                </div>
                <div className="flex-1 min-w-0">
                  {editing ? (
                    <textarea
                      value={edits[field] ?? value}
                      onChange={(event) =>
                        setEdits((prev) => ({ ...prev, [field]: event.target.value }))
                      }
                      rows={2}
                      placeholder={`补充${BRIEF_FIELD_LABELS[field] ?? field}`}
                      className="w-full resize-y rounded-lg border border-zinc-700/60 bg-dark-900/70 px-2 py-1 text-xs text-zinc-200 placeholder:text-zinc-600 focus:outline-none focus:border-brand-500/60"
                    />
                  ) : (
                    <p
                      className={`text-xs leading-relaxed whitespace-pre-wrap ${
                        value ? "text-zinc-300" : "text-zinc-600"
                      }`}
                    >
                      {value || "还没有内容"}
                    </p>
                  )}
                </div>
                <span
                  className={`shrink-0 text-[10px] leading-none px-1.5 py-1 rounded-full border ${
                    FIELD_STATUS_CLASSES[fieldStatus] ?? FIELD_STATUS_CLASSES.unknown
                  }`}
                >
                  {FIELD_STATUS_LABELS[fieldStatus] ?? fieldStatus}
                </span>
              </div>
            );
          })}
        </div>
      )}

      {missingRequired.length > 0 ? (
        <div className="mx-3 mb-2 px-2.5 py-1.5 rounded-lg border border-amber-500/30 bg-amber-500/10 text-[11px] text-amber-300">
          {isSkipped ? "已按假设推进，缺失字段：" : "还缺必填字段："}
          {missingRequired.map((field) => BRIEF_FIELD_LABELS[field] ?? field).join("、")}
          {isSkipped ? "（不会回填简报）" : null}
        </div>
      ) : null}

      {staleNotice ? (
        <div className="mx-3 mb-2 flex flex-wrap items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-2.5 py-1.5 text-[11px] text-amber-300">
          <span className="min-w-0 flex-1">{staleNotice}</span>
          <button
            type="button"
            onClick={() => setStaleNotice(null)}
            className="min-h-[32px] rounded-md border border-amber-500/40 px-2 text-[11px] font-medium text-amber-200 hover:bg-amber-500/10"
          >
            保留草稿（覆盖）
          </button>
          <button
            type="button"
            onClick={() => {
              setEdits({});
              setStaleNotice(null);
              setEditing(false);
            }}
            className="min-h-[32px] rounded-md px-2 text-[11px] text-amber-300/80 hover:text-amber-200"
          >
            丢弃草稿
          </button>
        </div>
      ) : null}

      {errorText ? (
        <div className="mx-3 mb-2 px-2.5 py-1.5 rounded-lg border border-red-500/30 bg-red-500/10 text-[11px] text-red-300">
          {errorText}
        </div>
      ) : null}

      {reconfirmNotice ? (
        <div className="mx-3 mb-2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-2.5 py-1.5 text-[11px] leading-relaxed text-amber-200">
          {reconfirmNotice}
        </div>
      ) : null}

      {phaseNotice ? (
        <div className="mx-3 mb-2 px-2.5 py-1.5 rounded-lg border border-zinc-600/50 bg-dark-900/60 text-[11px] leading-relaxed text-zinc-400">
          {phaseNotice}
        </div>
      ) : null}

      {!isSkipped && (canConfirmOrSkip || (editing && canRevise)) ? (
        <div className="flex flex-wrap items-center gap-2 px-3 pb-3 pt-1">
          {canConfirmOrSkip && isConfirmed ? (
            <span className="text-[11px] font-medium text-emerald-300">
              {`已确认 v${version}（可编辑）`}
            </span>
          ) : canConfirmOrSkip && missingRequired.length > 0 ? (
            <>
              <button
                type="button"
                onClick={handleSkip}
                disabled={locked}
                className="min-h-[40px] px-3 text-xs font-medium rounded-lg border border-amber-500/40 bg-amber-600/20 text-amber-300 hover:bg-amber-600/30 disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center gap-1.5"
              >
                {pendingAction === "skip_clarification" ? (
                  <span className="w-3 h-3 rounded-full border-2 border-current border-t-transparent animate-spin" />
                ) : null}
                带假设进入脑暴
              </button>
              <p className="w-full text-[11px] leading-relaxed text-zinc-500">
                {"将带入的缺失项/假设："}
                {missingRequired.map((field) => BRIEF_FIELD_LABELS[field] ?? field).join("、")}
                ，以假设参与后续讨论（不会回填简报）。
              </p>
            </>
          ) : canConfirmOrSkip ? (
            <button
              type="button"
              onClick={handleConfirm}
              disabled={locked}
              className="min-h-[40px] px-3 text-xs font-medium rounded-lg border border-brand-500/40 bg-brand-600/20 text-brand-300 hover:bg-brand-600/30 disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center gap-1.5"
            >
              {pendingAction === "confirm_brief" ? (
                <span className="w-3 h-3 rounded-full border-2 border-current border-t-transparent animate-spin" />
              ) : null}
              确认简报
            </button>
          ) : null}
          {editing && canRevise ? (
            <button
              type="button"
              onClick={handleRevise}
              disabled={locked || Object.keys(changedFields).length === 0}
              className="min-h-[40px] px-3 text-xs font-medium rounded-lg border border-zinc-600/60 bg-dark-800 text-zinc-300 hover:bg-dark-700 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {pendingAction === "revise_brief" ? "提交中…" : "保存修订"}
            </button>
          ) : null}
          {canConfirmOrSkip && !isConfirmed && missingRequired.length === 0 ? (
            <button
              type="button"
              onClick={handleSkip}
              disabled={locked}
              className="min-h-[40px] px-3 text-xs rounded-lg text-zinc-400 hover:text-zinc-200 hover:bg-dark-700/60 disabled:opacity-50"
            >
              {pendingAction === "skip_clarification" ? "跳过中…" : "跳过澄清"}
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
