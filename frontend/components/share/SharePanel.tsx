"use client";

/**
 * 会话共享面板（只挂在会话页头部，只有所有者能看到管理按钮）。
 *
 * 交互：
 * * 打开时拉 `GET /api/v2/sessions/{id}/shares`；
 * * 填邮箱 + 选角色 → `POST`，列表就地更新；
 * * 每行「移除」→ `DELETE`，成功后从列表删掉。
 * 角色语义见 `frontend/lib/shares.ts` 的 SHARE_ROLE_HINTS。
 */
import { useCallback, useEffect, useState } from "react";
import { errorMessage, isApiError } from "@/lib/api";
import {
  SHARE_ROLE_HINTS,
  SHARE_ROLE_LABELS,
  addShare,
  listShares,
  normalizeShareEmail,
  removeShare,
  withoutSelf,
  type SessionShare,
  type SessionShareRole,
} from "@/lib/shares";

const INPUT_CLASS =
  "w-full min-h-[40px] rounded-lg border border-zinc-700/70 bg-dark-900/70 px-3 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-brand-500/60 focus:outline-none";

export function SharePanel({
  sessionId,
  myEmail,
  onClose,
}: {
  sessionId: string;
  myEmail: string | null;
  onClose: () => void;
}) {
  const [shares, setShares] = useState<SessionShare[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<SessionShareRole>("viewer");
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoadError(null);
    try {
      const page = await listShares(sessionId);
      setShares(withoutSelf(page.items ?? [], myEmail));
    } catch (err) {
      setShares([]);
      setLoadError(errorMessage(err));
    }
  }, [sessionId, myEmail]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const submit = async () => {
    const normalized = normalizeShareEmail(email);
    if (!normalized) {
      setFormError("请输入对方的登录邮箱");
      return;
    }
    setBusy(true);
    setFormError(null);
    try {
      const created = await addShare(sessionId, { email: normalized, role });
      setShares((prev) => {
        const rest = (prev ?? []).filter((item) => item.email !== created.email);
        return [...rest, created];
      });
      setEmail("");
    } catch (err) {
      setFormError(isApiError(err) ? err.message : errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const drop = async (target: string) => {
    setBusy(true);
    setFormError(null);
    try {
      await removeShare(sessionId, target);
      setShares((prev) => (prev ?? []).filter((item) => item.email !== target));
    } catch (err) {
      setFormError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section
      aria-label="会话共享"
      className="shrink-0 border-b border-zinc-800/50 bg-dark-800/60 px-4 py-3"
    >
      <div className="flex items-center gap-2">
        <h2 className="text-xs font-semibold text-zinc-200">共享这个会话</h2>
        <span className="text-[11px] text-zinc-500">只有所有者能管理成员</span>
        <button
          type="button"
          onClick={onClose}
          aria-label="关闭共享面板"
          className="ml-auto min-h-[32px] rounded-lg px-2 text-xs text-zinc-400 hover:text-zinc-200"
        >
          关闭
        </button>
      </div>

      <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center">
        <input
          aria-label="共享对象邮箱"
          className={INPUT_CLASS}
          placeholder="同事的登录邮箱"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          disabled={busy}
        />
        <select
          aria-label="共享角色"
          className={`${INPUT_CLASS} sm:w-[120px]`}
          value={role}
          onChange={(event) => setRole(event.target.value as SessionShareRole)}
          disabled={busy}
        >
          <option value="viewer">只读</option>
          <option value="editor">可操作</option>
        </select>
        <button
          type="button"
          onClick={() => void submit()}
          disabled={busy}
          className="min-h-[40px] shrink-0 rounded-lg border border-brand-500/40 bg-brand-600/20 px-3 text-xs font-medium text-brand-200 hover:bg-brand-600/30 disabled:opacity-50"
        >
          {busy ? "处理中…" : "添加"}
        </button>
      </div>

      <p className="mt-2 text-[11px] text-zinc-500">{SHARE_ROLE_HINTS[role]}</p>
      {formError ? (
        <p role="alert" className="mt-2 text-[11px] text-red-400">
          {formError}
        </p>
      ) : null}

      <div className="mt-3">
        {shares === null ? (
          <p className="text-[11px] text-zinc-500">正在读取共享成员…</p>
        ) : loadError ? (
          <p role="alert" className="text-[11px] text-red-400">
            读取共享成员失败：{loadError}
          </p>
        ) : shares.length === 0 ? (
          <p className="text-[11px] text-zinc-500">还没有共享给任何人。</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {shares.map((item) => (
              <li
                key={item.email}
                className="flex items-center gap-2 rounded-lg border border-zinc-800/60 bg-dark-900/60 px-3 py-1.5"
              >
                <span className="truncate text-xs text-zinc-200">{item.email}</span>
                <span className="shrink-0 rounded-full border border-zinc-700/70 px-2 text-[11px] text-zinc-400">
                  {SHARE_ROLE_LABELS[item.role] ?? item.role}
                </span>
                <button
                  type="button"
                  onClick={() => void drop(item.email)}
                  disabled={busy}
                  aria-label={`移除共享：${item.email}`}
                  className="ml-auto min-h-[32px] shrink-0 rounded-lg px-2 text-[11px] text-zinc-400 hover:text-red-300 disabled:opacity-50"
                >
                  移除
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
