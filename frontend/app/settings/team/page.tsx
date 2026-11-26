"use client";

/**
 * 团队工作区：管理「成员组」名单（建组 / 加人 / 改角色 / 移除 / 删组）。
 *
 * 这里刻意不授予任何权限：组只是常用协作名单，真正授权发生在会话页的
 * 「共享」面板（把组一次性应用到会话，逐条写成 session_shares）。
 * 删除组不会回收已授予的访问，撤销访问必须在会话的共享列表里移除那个人。
 */
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ArrowRightIcon, LayersIcon } from "@/components/icons";
import { errorMessage } from "@/lib/api";
import { SHARE_ROLE_LABELS } from "@/lib/shares";
import {
  MAX_GROUP_NAME,
  MAX_MEMBERS_PER_GROUP,
  addTeamGroupMember,
  createTeamGroup,
  deleteTeamGroup,
  groupErrorHint,
  groupSummary,
  listTeamGroups,
  normalizeGroupName,
  removeTeamGroupMember,
  type TeamGroup,
  type TeamGroupRole,
} from "@/lib/teamGroups";

const INPUT_CLASS =
  "w-full min-h-[40px] rounded-lg border border-zinc-700/70 bg-dark-900/70 px-3 text-sm text-zinc-100 placeholder:text-zinc-600 focus:border-brand-500/60 focus:outline-none";

export default function TeamSettingsPage() {
  const [groups, setGroups] = useState<TeamGroup[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  // 每个组各自的「新增成员」输入，避免整页共用一个状态导致串行操作时分不清
  const [memberEmail, setMemberEmail] = useState<Record<string, string>>({});
  const [memberRole, setMemberRole] = useState<Record<string, TeamGroupRole>>({});
  const [notice, setNotice] = useState<Record<string, string>>({});

  const reload = useCallback(async () => {
    setLoadError(null);
    try {
      const page = await listTeamGroups();
      setGroups(page.items ?? []);
    } catch (err) {
      setGroups([]);
      setLoadError(errorMessage(err));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const create = async () => {
    const normalized = normalizeGroupName(name);
    if (!normalized) {
      setFormError(`成员组名称不能为空，且不超过 ${MAX_GROUP_NAME} 个字符`);
      return;
    }
    setBusy(true);
    setFormError(null);
    try {
      const created = await createTeamGroup(normalized);
      setGroups((prev) => [...(prev ?? []).filter((g) => g.id !== created.id), created]);
      setName("");
    } catch (err) {
      setFormError(groupErrorHint(err));
    } finally {
      setBusy(false);
    }
  };

  const addMember = async (group: TeamGroup) => {
    const email = (memberEmail[group.id] ?? "").trim();
    if (!email) {
      setNotice((prev) => ({ ...prev, [group.id]: "请输入对方的登录邮箱" }));
      return;
    }
    setBusy(true);
    setNotice((prev) => ({ ...prev, [group.id]: "" }));
    try {
      const member = await addTeamGroupMember(group.id, {
        email,
        role: memberRole[group.id] ?? "viewer",
      });
      setGroups((prev) =>
        (prev ?? []).map((item) =>
          item.id !== group.id
            ? item
            : {
                ...item,
                members: [...item.members.filter((m) => m.email !== member.email), member],
              }
        )
      );
      setMemberEmail((prev) => ({ ...prev, [group.id]: "" }));
    } catch (err) {
      setNotice((prev) => ({ ...prev, [group.id]: groupErrorHint(err) }));
    } finally {
      setBusy(false);
    }
  };

  const dropMember = async (group: TeamGroup, email: string) => {
    setBusy(true);
    try {
      await removeTeamGroupMember(group.id, email);
      setGroups((prev) =>
        (prev ?? []).map((item) =>
          item.id !== group.id
            ? item
            : { ...item, members: item.members.filter((m) => m.email !== email) }
        )
      );
    } catch (err) {
      setNotice((prev) => ({ ...prev, [group.id]: groupErrorHint(err) }));
    } finally {
      setBusy(false);
    }
  };

  const dropGroup = async (group: TeamGroup) => {
    setBusy(true);
    try {
      await deleteTeamGroup(group.id);
      setGroups((prev) => (prev ?? []).filter((item) => item.id !== group.id));
    } catch (err) {
      setNotice((prev) => ({ ...prev, [group.id]: groupErrorHint(err) }));
    } finally {
      setBusy(false);
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
          <LayersIcon size={22} className="text-brand-300" />
          <h1 className="text-xl font-semibold text-zinc-100">团队工作区</h1>
        </div>
        <p className="text-sm leading-6 text-zinc-400">
          把常一起评审的同事存成成员组，之后在会话的「共享」面板里一次加进去。
          成员组只保存名单，不授予任何权限：授权发生在会话级共享，删除成员组不会回收已经给出的访问权限。
        </p>
      </header>

      <section
        aria-label="创建成员组"
        className="rounded-2xl border border-zinc-700/60 bg-dark-800/70 p-5"
      >
        <h2 className="text-xs font-semibold text-zinc-200">新建成员组</h2>
        <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center">
          <input
            aria-label="成员组名称"
            className={INPUT_CLASS}
            placeholder="例如：核心评审团"
            maxLength={MAX_GROUP_NAME}
            value={name}
            onChange={(event) => setName(event.target.value)}
            disabled={busy}
          />
          <button
            type="button"
            onClick={() => void create()}
            disabled={busy}
            className="min-h-[40px] shrink-0 rounded-lg border border-brand-500/40 bg-brand-600/20 px-3 text-xs font-medium text-brand-200 hover:bg-brand-600/30 disabled:opacity-50"
          >
            {busy ? "处理中…" : "创建成员组"}
          </button>
        </div>
        <p className="mt-2 text-[11px] text-zinc-500">
          名称在你自己名下唯一，最多 {MAX_MEMBERS_PER_GROUP} 人一组。
        </p>
        {formError ? (
          <p role="alert" className="mt-2 text-[11px] text-red-400">
            {formError}
          </p>
        ) : null}
      </section>

      <section aria-label="成员组列表" className="flex flex-col gap-3">
        {groups === null ? (
          <p className="text-xs text-zinc-500">正在读取成员组…</p>
        ) : loadError ? (
          <p role="alert" className="text-xs text-red-400">
            读取成员组失败：{loadError}
          </p>
        ) : groups.length === 0 ? (
          <p className="text-xs text-zinc-500">还没有成员组，先在上方创建一个。</p>
        ) : (
          groups.map((group) => {
            const role = memberRole[group.id] ?? "viewer";
            return (
              <article
                key={group.id}
                className="rounded-2xl border border-zinc-800/70 bg-dark-800/60 p-4"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="text-sm font-semibold text-zinc-100">{group.name}</h3>
                  <span className="rounded-full border border-zinc-700/70 px-2 text-[11px] text-zinc-400">
                    {groupSummary(group)}
                  </span>
                  <button
                    type="button"
                    onClick={() => void dropGroup(group)}
                    disabled={busy}
                    aria-label={`删除成员组：${group.name}`}
                    className="ml-auto min-h-[32px] rounded-lg px-2 text-[11px] text-zinc-400 hover:text-red-300 disabled:opacity-50"
                  >
                    删除
                  </button>
                </div>

                {group.members.length === 0 ? (
                  <p className="mt-2 text-[11px] text-zinc-500">还没有成员。</p>
                ) : (
                  <ul className="mt-2 flex flex-col gap-1">
                    {group.members.map((member) => (
                      <li
                        key={member.email}
                        className="flex items-center gap-2 rounded-lg border border-zinc-800/60 bg-dark-900/60 px-3 py-1.5"
                      >
                        <span className="truncate text-xs text-zinc-200">{member.email}</span>
                        <span className="shrink-0 rounded-full border border-zinc-700/70 px-2 text-[11px] text-zinc-400">
                          {SHARE_ROLE_LABELS[member.role] ?? member.role}
                        </span>
                        <button
                          type="button"
                          onClick={() => void dropMember(group, member.email)}
                          disabled={busy}
                          aria-label={`从成员组移除：${member.email}`}
                          className="ml-auto min-h-[32px] shrink-0 rounded-lg px-2 text-[11px] text-zinc-400 hover:text-red-300 disabled:opacity-50"
                        >
                          移除
                        </button>
                      </li>
                    ))}
                  </ul>
                )}

                <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center">
                  <input
                    aria-label={`添加成员邮箱：${group.name}`}
                    className={INPUT_CLASS}
                    placeholder="同事的登录邮箱"
                    value={memberEmail[group.id] ?? ""}
                    onChange={(event) =>
                      setMemberEmail((prev) => ({ ...prev, [group.id]: event.target.value }))
                    }
                    disabled={busy}
                  />
                  <select
                    aria-label={`成员角色：${group.name}`}
                    className={`${INPUT_CLASS} sm:w-[120px]`}
                    value={role}
                    onChange={(event) =>
                      setMemberRole((prev) => ({
                        ...prev,
                        [group.id]: event.target.value as TeamGroupRole,
                      }))
                    }
                    disabled={busy}
                  >
                    <option value="viewer">只读</option>
                    <option value="editor">可操作</option>
                  </select>
                  <button
                    type="button"
                    onClick={() => void addMember(group)}
                    disabled={busy}
                    className="min-h-[40px] shrink-0 rounded-lg border border-zinc-600/70 bg-zinc-800/40 px-3 text-xs text-zinc-200 hover:bg-zinc-800/70 disabled:opacity-50"
                  >
                    加入组
                  </button>
                </div>
                {notice[group.id] ? (
                  <p role="alert" className="mt-2 text-[11px] text-amber-300">
                    {notice[group.id]}
                  </p>
                ) : null}
              </article>
            );
          })
        )}
      </section>

      <section className="rounded-xl border border-zinc-800/80 bg-dark-900/60 p-4 text-xs leading-6 text-zinc-500">
        <p className="font-medium text-zinc-400">下一步</p>
        <p className="mt-1">
          进入某个会话页，点右上「共享」→ 选成员组 → 「应用到这个会话」，组内成员会按各自的
          只读 / 可操作角色加入共享列表；已在列表里的人会被跳过，重复应用是安全的。
        </p>
      </section>
    </main>
  );
}
