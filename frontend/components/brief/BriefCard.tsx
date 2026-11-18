"use client";

import { useEffect, useMemo, useState } from "react";
import { useSessionStore } from "@/store/sessionStore";
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
    sendCommand,
    reloadSession,
  } = useSessionStore();

  const [editing, setEditing] = useState(false);
  const [edits, setEdits] = useState<Edits>({});
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const [errorText, setErrorText] = useState<string | null>(null);

  const brief = productState?.brief ?? null;
  const version = productState?.version ?? productVersion;
  const currentSessionVersion = sessionView?.session_version ?? null;

  // 产品版本变化时丢弃本地未提交的编辑（避免把旧版本内容写回新状态）
  useEffect(() => {
    setEdits({});
    setErrorText(null);
  }, [productVersion]);

  const missingRequired = useMemo(() => {
    if (!brief) return [] as string[];
    return BRIEF_REQUIRED_FIELDS.filter((field) => {
      const value = brief[field]?.value;
      return typeof value !== "string" || value.trim().length === 0;
    });
  }, [brief]);

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
  if (!brief && !sessionLoading) return null;
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
  const isSkipped = status === "skipped";

  async function runCommand(
    label: string,
    action: "confirm_brief" | "skip_clarification" | "revise_brief",
    payload: Record<string, unknown>
  ) {
    if (busy) return;
    setPendingAction(label);
    setErrorText(null);
    try {
      await sendCommand(action, payload, {
        expectedSessionVersion: currentSessionVersion,
      });
      if (action === "confirm_brief") toast.success("简报已确认");
      else if (action === "skip_clarification") toast.success("已跳过澄清");
      else toast.success("修订已提交");
      setEdits({});
      setEditing(false);
    } catch (error) {
      if (isApiError(error) && error.code === "VERSION_CONFLICT") {
        // 版本冲突：提示并重新加载当前会话（服务端为准）
        toast.success("内容已在别处更新，已刷新");
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
          disabled={busy || isSkipped}
          className="ml-auto min-h-[32px] px-2 text-[11px] text-zinc-400 hover:text-zinc-200 disabled:opacity-50"
        >
          {editing ? "收起编辑" : "编辑"}
        </button>
      </div>

      {isSkipped ? (
        <div className="px-3 py-3 text-xs text-zinc-400">
          已跳过澄清，直接进入脑暴。七个字段会随着讨论自动补全。
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

      {missingRequired.length > 0 && !isSkipped ? (
        <div className="mx-3 mb-2 px-2.5 py-1.5 rounded-lg border border-amber-500/30 bg-amber-500/10 text-[11px] text-amber-300">
          还缺必填字段：{missingRequired.map((field) => BRIEF_FIELD_LABELS[field] ?? field).join("、")}
        </div>
      ) : null}

      {errorText ? (
        <div className="mx-3 mb-2 px-2.5 py-1.5 rounded-lg border border-red-500/30 bg-red-500/10 text-[11px] text-red-300">
          {errorText}
        </div>
      ) : null}

      {!isSkipped ? (
        <div className="flex items-center gap-2 px-3 pb-3 pt-1">
          <button
            type="button"
            onClick={handleConfirm}
            disabled={busy}
            className="min-h-[40px] px-3 text-xs font-medium rounded-lg border border-brand-500/40 bg-brand-600/20 text-brand-300 hover:bg-brand-600/30 disabled:opacity-50 disabled:cursor-not-allowed inline-flex items-center gap-1.5"
          >
            {pendingAction === "confirm_brief" ? (
              <span className="w-3 h-3 rounded-full border-2 border-current border-t-transparent animate-spin" />
            ) : null}
            确认简报
          </button>
          {editing ? (
            <button
              type="button"
              onClick={handleRevise}
              disabled={busy || Object.keys(changedFields).length === 0}
              className="min-h-[40px] px-3 text-xs font-medium rounded-lg border border-zinc-600/60 bg-dark-800 text-zinc-300 hover:bg-dark-700 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {pendingAction === "revise_brief" ? "提交中…" : "保存修订"}
            </button>
          ) : null}
          <button
            type="button"
            onClick={handleSkip}
            disabled={busy}
            className="min-h-[40px] px-3 text-xs rounded-lg text-zinc-400 hover:text-zinc-200 hover:bg-dark-700/60 disabled:opacity-50"
          >
            {pendingAction === "skip_clarification" ? "跳过中…" : "跳过澄清"}
          </button>
        </div>
      ) : null}
    </div>
  );
}
