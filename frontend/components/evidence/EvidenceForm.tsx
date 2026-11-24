"use client";

import { useState } from "react";

/** 表单提交值；空串由页面层转成 null，避免后端把空链接当来源。 */
export interface EvidenceFormValues {
  title: string;
  excerpt: string;
  source_date: string;
  source_uri: string;
}

export interface EvidenceFormProps {
  /** 报告里被标未验证的数字（用于提示用户该写什么来源） */
  numbers: string[];
  /** 服务端给的补充建议（含统计口径要求），直接展示 */
  suggestion?: string | null;
  busy?: boolean;
  onCancel: () => void;
  onSubmit: (values: EvidenceFormValues) => void | Promise<void>;
}

const MIN_EXCERPT_CHARS = 8;

/**
 * 「补充来源」表单（第 29 批）。
 *
 * 「数字未验证」徽标原来只能看、不能处理：用户想补来源得自己找入口。这里把
 * 徽标变成可操作入口——表单只收原文摘录（后端 add_evidence 落库后，报告的
 * 数字标注会在读取时按最新证据重算）。
 */
export function EvidenceForm({ numbers, suggestion, busy = false, onCancel, onSubmit }: EvidenceFormProps) {
  const [title, setTitle] = useState("");
  const [excerpt, setExcerpt] = useState("");
  const [sourceDate, setSourceDate] = useState("");
  const [sourceUri, setSourceUri] = useState("");
  const [error, setError] = useState<string | null>(null);

  const numbersText = numbers.length > 0 ? numbers.join("、") : "";

  const submit = () => {
    const trimmed = excerpt.trim();
    if (trimmed.length < MIN_EXCERPT_CHARS) {
      setError(`请至少写 ${MIN_EXCERPT_CHARS} 个字，说明这条证据里这几个数字的实测值与统计口径。`);
      return;
    }
    setError(null);
    void onSubmit({
      title: title.trim(),
      excerpt: trimmed,
      source_date: sourceDate.trim(),
      source_uri: sourceUri.trim(),
    });
  };

  return (
    <div className="rounded-lg border border-orange-500/30 bg-orange-500/5 p-3">
      <div className="text-xs font-medium text-orange-300">
        补充来源{numbersText ? `：${numbersText}` : ""}
      </div>
      {suggestion ? (
        <div className="mt-1 text-[11px] leading-relaxed text-orange-200/70">{suggestion}</div>
      ) : null}
      <div className="mt-2 space-y-2">
        <label className="block text-[11px] text-zinc-400">
          证据标题（可选）
          <input
            aria-label="证据标题"
            className="mt-1 w-full rounded border border-zinc-700/60 bg-dark-900/60 px-2 py-1.5 text-xs text-zinc-200 outline-none focus:border-orange-500/60"
            placeholder="例如：3 家客户 2 周试用的实测工时"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
        </label>
        <label className="block text-[11px] text-zinc-400">
          摘录原文（必填）
          <textarea
            aria-label="证据摘录"
            className="mt-1 min-h-[72px] w-full rounded border border-zinc-700/60 bg-dark-900/60 px-2 py-1.5 text-xs leading-relaxed text-zinc-200 outline-none focus:border-orange-500/60"
            placeholder="把这几个数字对应的原文写进来：谁实测的、多少、统计区间（例如「2026-11 试点 3 家客户，对账从 6 小时降到 2 小时」）"
            value={excerpt}
            onChange={(event) => setExcerpt(event.target.value)}
          />
        </label>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <label className="block text-[11px] text-zinc-400">
            统计日期（可选）
            <input
              aria-label="统计日期"
              type="date"
              className="mt-1 w-full rounded border border-zinc-700/60 bg-dark-900/60 px-2 py-1.5 text-xs text-zinc-200 outline-none focus:border-orange-500/60"
              value={sourceDate}
              onChange={(event) => setSourceDate(event.target.value)}
            />
          </label>
          <label className="block text-[11px] text-zinc-400">
            来源链接（可选）
            <input
              aria-label="来源链接"
              className="mt-1 w-full rounded border border-zinc-700/60 bg-dark-900/60 px-2 py-1.5 text-xs text-zinc-200 outline-none focus:border-orange-500/60"
              placeholder="https://…"
              value={sourceUri}
              onChange={(event) => setSourceUri(event.target.value)}
            />
          </label>
        </div>
      </div>
      {error ? (
        <div className="mt-2 text-[11px] text-red-400" role="alert">
          {error}
        </div>
      ) : null}
      <div className="mt-2 flex items-center gap-2">
        <button
          type="button"
          className="min-h-[40px] rounded bg-orange-600/80 px-3 text-xs font-medium text-white disabled:opacity-50"
          disabled={busy}
          onClick={submit}
        >
          {busy ? "保存中…" : "保存证据"}
        </button>
        <button
          type="button"
          className="min-h-[40px] rounded border border-zinc-700/60 px-3 text-xs text-zinc-300"
          onClick={onCancel}
        >
          取消
        </button>
      </div>
    </div>
  );
}
