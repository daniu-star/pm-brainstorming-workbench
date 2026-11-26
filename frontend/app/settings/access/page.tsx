"use client";

/**
 * 统一权限页：一眼看清「我共享出去了什么」「别人共享给我什么」「成员组里都有谁」。
 *
 * 数据来自 `GET /api/v2/access`（第 39 批）：一次拿全，权限页本身不写任何权限，
 * 只有「退出共享」这一个动作——它删除的是我自己的 session_shares 行，不会
 * 影响其他成员，也不会影响会话所有者。
 */
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ArrowRightIcon, LayersIcon, ShieldIcon } from "@/components/icons";
import { errorMessage } from "@/lib/api";
import { SHARE_ROLE_LABELS } from "@/lib/shares";
import {
  accessErrorHint,
  accessTotalsText,
  formatUpdated,
  getAccessOverview,
  leaveSharedSession,
  phaseLabel,
  sessionRoleBadge,
  sharesSummary,
  type AccessOverview,
  type IncomingSession,
  type OwnedSession,
} from "@/lib/access";

const CARD_CLASS = "rounded-2xl border border-zinc-800/70 bg-dark-800/60 p-4";

export default function AccessSettingsPage() {
  const [overview, setOverview] = useState<AccessOverview | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoadError(null);
    try {
      setOverview(await getAccessOverview());
    } catch (err) {
      setOverview(null);
      setLoadError(accessErrorHint(err));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const leave = async (item: IncomingSession) => {
    setBusyId(item.session_id);
    setNotice(null);
    try {
      await leaveSharedSession(item.session_id);
      setNotice(`已退出「${item.title}」的共享，访问权限已收回。`);
      await reload();
    } catch (err) {
      setNotice(errorMessage(err));
    } finally {
      setBusyId(null);
    }
  };

  return (
    <main
      id="main-content"
      className="mx-auto flex min-h-dvh w-full max-w-3xl flex-col gap-6 px-4 py-10"
    >
      <header className="flex flex-col gap-2">
        <Link
          href="/"
          className="inline-flex w-fit items-center gap-1 text-xs text-zinc-500 transition-colors hover:text-zinc-300"
        >
          <ArrowRightIcon size={14} className="rotate-180" />
          返回工作台
        </Link>
        <div className="flex items-center gap-2">
          <ShieldIcon size={22} className="text-brand-300" />
          <h1 className="text-xl font-semibold text-zinc-100">权限总览</h1>
        </div>
        <p className="text-sm leading-6 text-zinc-400">
          谁在用你创建的会话、你被加进了哪些会话、成员组里都有谁，都在这一页。
          共享是会话级授权：所有者可以改成员与角色，可操作成员能提交命令，只读成员只能看。
        </p>
        <p role="status" className="text-xs text-zinc-500">
          {accessTotalsText(overview?.totals)}
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => void reload()}
            className="min-h-[36px] rounded-lg border border-zinc-700/70 px-3 text-xs text-zinc-300 hover:bg-zinc-800/60"
          >
            刷新
          </button>
          <Link
            href="/settings/team"
            className="min-h-[36px] inline-flex items-center gap-2 rounded-lg border border-zinc-700/70 px-3 text-xs text-zinc-300 hover:bg-zinc-800/60"
          >
            <LayersIcon size={14} />
            管理成员组
          </Link>
        </div>
      </header>

      {notice ? (
        <p role="status" className="rounded-xl border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
          {notice}
        </p>
      ) : null}

      {loadError ? (
        <p role="alert" className="rounded-xl border border-red-500/40 bg-red-500/10 px-3 py-2 text-xs text-red-300">
          {loadError}
        </p>
      ) : null}

      <section aria-label="我创建的会话" className="flex flex-col gap-3">
        <h2 className="text-xs font-semibold text-zinc-200">
          我创建的会话{overview ? `（${overview.owned.length}）` : ""}
        </h2>
        {overview === null ? (
          <p className="text-xs text-zinc-500">正在读取权限信息…</p>
        ) : overview.owned.length === 0 ? (
          <p className="text-xs text-zinc-500">还没有创建任何会话。</p>
        ) : (
          overview.owned.map((item) => <OwnedCard key={item.session_id} item={item} />)
        )}
      </section>

      <section aria-label="共享给我的会话" className="flex flex-col gap-3">
        <h2 className="text-xs font-semibold text-zinc-200">
          共享给我的会话{overview ? `（${overview.incoming.length}）` : ""}
        </h2>
        {overview === null ? (
          <p className="text-xs text-zinc-500">正在读取权限信息…</p>
        ) : overview.incoming.length === 0 ? (
          <p className="text-xs text-zinc-500">暂时没有别人共享给你的会话。</p>
        ) : (
          overview.incoming.map((item) => {
            const badge = sessionRoleBadge(item.role);
            return (
              <article key={item.session_id} className={CARD_CLASS}>
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="min-w-0 flex-1 truncate text-sm font-medium text-zinc-100">
                    {item.title}
                  </h3>
                  {badge ? (
                    <span className="shrink-0 rounded-full border border-brand-500/40 bg-brand-600/10 px-2 text-[11px] text-brand-200">
                      {badge}
                    </span>
                  ) : null}
                </div>
                <p className="mt-1 text-[11px] text-zinc-500">
                  来自 {item.owner_email} · {phaseLabel(item.phase)} · 更新于{" "}
                  {formatUpdated(item.updated_at)}
                </p>
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <Link
                    href={`/session/${item.session_id}`}
                    className="inline-flex min-h-[32px] items-center rounded-lg border border-zinc-700/70 px-2.5 text-[11px] text-zinc-200 hover:bg-zinc-800/60"
                  >
                    打开会话
                  </Link>
                  <button
                    type="button"
                    onClick={() => void leave(item)}
                    disabled={busyId === item.session_id}
                    aria-label={`退出共享：${item.title}`}
                    className="min-h-[32px] rounded-lg px-2 text-[11px] text-zinc-400 hover:text-red-300 disabled:opacity-50"
                  >
                    {busyId === item.session_id ? "处理中…" : "退出共享"}
                  </button>
                </div>
              </article>
            );
          })
        )}
      </section>

      <section aria-label="我的成员组" className="flex flex-col gap-3">
        <h2 className="text-xs font-semibold text-zinc-200">
          我的成员组{overview ? `（${overview.groups.length}）` : ""}
        </h2>
        {overview === null ? (
          <p className="text-xs text-zinc-500">正在读取权限信息…</p>
        ) : overview.groups.length === 0 ? (
          <p className="text-xs text-zinc-500">
            还没有成员组，去
            <Link href="/settings/team" className="mx-1 text-brand-300 underline-offset-2 hover:underline">
              团队工作区
            </Link>
            建一个，就能在会话里一次性共享给多人。
          </p>
        ) : (
          overview.groups.map((group) => (
            <article key={group.id} className={CARD_CLASS}>
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-sm font-medium text-zinc-100">{group.name}</h3>
                <span className="rounded-full border border-zinc-700/70 px-2 text-[11px] text-zinc-400">
                  {group.members.length} 人
                </span>
              </div>
              <p className="mt-1 text-[11px] text-zinc-500">
                {group.members.length === 0
                  ? "还没有成员"
                  : group.members
                      .map((member) => `${member.email}（${SHARE_ROLE_LABELS[member.role]}）`)
                      .join("、")}
              </p>
            </article>
          ))
        )}
      </section>

      <footer className="text-[11px] leading-5 text-zinc-500">
        安全提醒：「可操作」成员能替你提交命令，额度与模型 Key 仍记在会话所有者名下；
        不再需要的共享请及时移除，退出共享只影响你自己。
      </footer>
    </main>
  );
}

function OwnedCard({ item }: { item: OwnedSession }) {
  return (
    <article className={CARD_CLASS}>
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="min-w-0 flex-1 truncate text-sm font-medium text-zinc-100">{item.title}</h3>
        <span className="shrink-0 rounded-full border border-zinc-700/70 px-2 text-[11px] text-zinc-400">
          {phaseLabel(item.phase)}
        </span>
      </div>
      <p className="mt-1 text-[11px] text-zinc-500">
        {sharesSummary(item.shares)} · 更新于 {formatUpdated(item.updated_at)}
      </p>
      {item.shares.length > 0 ? (
        <ul className="mt-2 flex flex-wrap gap-1.5">
          {item.shares.map((share) => (
            <li
              key={share.email}
              className="rounded-full border border-zinc-700/70 bg-dark-900/60 px-2 py-0.5 text-[11px] text-zinc-300"
            >
              {share.email}
              <span className="ml-1 text-zinc-500">{SHARE_ROLE_LABELS[share.role]}</span>
            </li>
          ))}
        </ul>
      ) : null}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Link
          href={`/session/${item.session_id}`}
          className="inline-flex min-h-[32px] items-center rounded-lg border border-zinc-700/70 px-2.5 text-[11px] text-zinc-200 hover:bg-zinc-800/60"
        >
          打开会话
        </Link>
        <span className="text-[11px] text-zinc-500">改成员/角色：打开会话 →「共享」</span>
      </div>
    </article>
  );
}
