"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";
import {
  ArrowLeft,
  BarChart3,
  CheckCircle2,
  Copy,
  FileText,
  Loader2,
  Mail,
  MessageSquareText,
  Send,
  Settings2,
  ShieldCheck,
  Trash2,
  UserPlus,
  UsersRound,
  WalletCards,
} from "lucide-react";
import { api, apiUrl } from "@/lib/api";
import { getUserHeaders } from "@/lib/user";
import type { TeamChatMessage, TeamSummary } from "@/lib/team-types";
import { toast } from "@/components/Toast";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

const ROLE_LABEL = { owner: "团队负责人", admin: "管理员", member: "协作成员" };

function compactNumber(value: number) {
  return new Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

function formatTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toLocaleString("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export default function TeamAccountPage() {
  const [team, setTeam] = useState<TeamSummary | null>(null);
  const [messages, setMessages] = useState<TeamChatMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<"admin" | "member">("member");
  const [sendingInvite, setSendingInvite] = useState(false);
  const [lastInviteUrl, setLastInviteUrl] = useState("");
  const [chatText, setChatText] = useState("");
  const [sendingChat, setSendingChat] = useState(false);
  const canManage = team?.current_user_role === "owner" || team?.current_user_role === "admin";

  const refresh = useCallback(async () => {
    const [nextTeam, nextMessages] = await Promise.all([
      api<TeamSummary>("/api/team/current"),
      api<TeamChatMessage[]>("/api/team/chat/messages?limit=100"),
    ]);
    setTeam(nextTeam);
    setMessages(nextMessages);
  }, []);

  useEffect(() => {
    refresh()
      .catch((error) => toast("error", error instanceof Error ? error.message : "团队空间加载失败"))
      .finally(() => setLoading(false));
    const quotaTimer = window.setInterval(() => refresh().catch(() => undefined), 30_000);
    return () => window.clearInterval(quotaTimer);
  }, [refresh]);

  useEffect(() => {
    if (!team?.id) return;
    const controller = new AbortController();
    let retryTimer: ReturnType<typeof setTimeout> | undefined;

    const connect = async () => {
      try {
        const response = await fetch(apiUrl("/api/team/chat/stream"), {
          method: "POST",
          headers: getUserHeaders(),
          signal: controller.signal,
        });
        if (!response.ok || !response.body) return;
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (!controller.signal.aborted) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const blocks = buffer.split("\n\n");
          buffer = blocks.pop() || "";
          for (const block of blocks) {
            const data = block.split("\n").find((line) => line.startsWith("data: "))?.slice(6);
            if (!data) continue;
            const event = JSON.parse(data);
            if (event.type === "chat_message") {
              const incoming = event.payload as TeamChatMessage;
              setMessages((current) => current.some((item) => item.id === incoming.id) ? current : [...current, incoming]);
            } else if (event.type !== "connected") {
              refresh().catch(() => undefined);
            }
          }
        }
      } catch {
        if (!controller.signal.aborted) retryTimer = setTimeout(connect, 2500);
      }
    };
    connect();
    return () => {
      controller.abort();
      if (retryTimer) clearTimeout(retryTimer);
    };
  }, [team?.id, refresh]);

  const invite = async (event: FormEvent) => {
    event.preventDefault();
    if (!email.trim()) return;
    setSendingInvite(true);
    try {
      const result = await api<{ email_sent: boolean; warning: string; invitation_url: string }>("/api/team/invitations", {
        method: "POST",
        body: JSON.stringify({ email: email.trim(), role }),
      });
      setLastInviteUrl(result.invitation_url);
      setEmail("");
      toast(result.email_sent ? "success" : "info", result.email_sent ? "邀请邮件已发送" : "SMTP 尚未就绪，已生成可复制邀请链接");
      await refresh();
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "邀请发送失败");
    } finally {
      setSendingInvite(false);
    }
  };

  const sendMessage = async (event: FormEvent) => {
    event.preventDefault();
    const content = chatText.trim();
    if (!content) return;
    setSendingChat(true);
    try {
      const sent = await api<TeamChatMessage>("/api/team/chat/messages", { method: "POST", body: JSON.stringify({ content }) });
      setMessages((current) => current.some((item) => item.id === sent.id) ? current : [...current, sent]);
      setChatText("");
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "消息发送失败");
    } finally {
      setSendingChat(false);
    }
  };

  const removeMember = async (memberId: string, name: string) => {
    if (!window.confirm(`确认将「${name}」移出团队？`)) return;
    await api(`/api/team/members/${memberId}`, { method: "DELETE" });
    toast("success", "成员已移出团队");
    await refresh();
  };

  if (loading || !team) {
    return <main className="team-shell flex min-h-screen items-center justify-center"><Loader2 className="h-9 w-9 animate-spin text-cyan-300" /></main>;
  }

  const quotaRate = team.quota.total ? Math.min(100, Math.round((team.quota.used / team.quota.total) * 100)) : 0;

  return (
    <main id="main-content" className="team-shell min-h-screen px-4 pb-16 pt-5 sm:px-7 lg:px-10">
      <div className="team-aurora" aria-hidden="true" />
      <div className="relative mx-auto max-w-[1440px]">
        <header className="team-glass-card rounded-3xl p-5 sm:p-7">
          <div className="flex flex-col justify-between gap-6 lg:flex-row lg:items-start">
            <div>
              <Link href="/" className="inline-flex items-center gap-2 text-xs text-slate-400 transition hover:text-cyan-100"><ArrowLeft className="h-3.5 w-3.5" />返回产品脑暴工作台</Link>
              <p className="mt-5 text-xs font-semibold tracking-[0.2em] text-cyan-200/65">TEAM ACCOUNT / COLLABORATION COMMAND</p>
              <h1 className="mt-2 text-3xl font-semibold tracking-tight text-slate-50">{team.name}</h1>
              <p className="mt-3 max-w-2xl text-sm leading-7 text-slate-400">管理账号、额度、PRD 资产与团队讨论。成员加入后可在同一证据链里评审决策，而不会看到未明确共享的历史项目。</p>
            </div>
            <div className="flex items-center gap-2">
              <span className="rounded-xl border border-emerald-300/15 bg-emerald-300/[0.07] px-3 py-2 text-xs text-emerald-200"><ShieldCheck className="mr-1.5 inline h-4 w-4" />{ROLE_LABEL[team.current_user_role]}</span>
              <Button variant="outline" onClick={() => refresh()}><Settings2 className="h-4 w-4" />刷新数据</Button>
            </div>
          </div>
        </header>

        <section className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <Metric icon={<WalletCards />} label="团队剩余额度" value={compactNumber(team.quota.remaining)} note={`已使用 ${compactNumber(team.quota.used)} / ${compactNumber(team.quota.total)}`} />
          <Metric icon={<FileText />} label="PRD 文档总数" value={team.prd_document_count} note={`来自 ${team.prd_project_count} 个共享项目`} />
          <Metric icon={<UsersRound />} label="有效团队成员" value={team.members.length} note={`${team.invitations.filter((item) => item.status === "pending").length} 个邀请待接受`} />
          <Metric icon={<BarChart3 />} label="团队项目空间" value={team.session_count} note="仅统计已明确共享的项目" />
        </section>

        <div className="mt-3 h-2 overflow-hidden rounded-full bg-slate-950/70" aria-label={`团队额度已使用 ${quotaRate}%`}>
          <div className="h-full rounded-full bg-gradient-to-r from-cyan-400 via-sky-400 to-violet-400 transition-all" style={{ width: `${quotaRate}%` }} />
        </div>

        <section className="mt-6 grid gap-5 xl:grid-cols-[0.88fr_1.12fr]">
          <div className="space-y-5">
            <article className="team-glass-card rounded-3xl p-5 sm:p-6">
              <p className="text-xs font-semibold tracking-[0.18em] text-cyan-200/60">QUICK START</p>
              <h2 className="mt-2 text-xl font-semibold text-slate-100">三步开启团队决策协作</h2>
              <div className="mt-5 space-y-3">
                <Guide index="01" title="邀请成员" text="输入邮箱并设置成员或管理员；对方通过邮件链接加入。" />
                <Guide index="02" title="共享项目" text="在某个项目的“决策中心”点击共享，历史项目不会自动公开。" />
                <Guide index="03" title="共同评审" text="成员进入 PRD、路线图与团队评审，评论、投票、审批都会留下记录。" />
              </div>
            </article>

            <article className="team-glass-card rounded-3xl p-5 sm:p-6">
              <div className="flex items-center justify-between gap-3">
                <div><p className="text-xs font-semibold tracking-[0.18em] text-cyan-200/60">MEMBER DIRECTORY</p><h2 className="mt-1 text-lg font-semibold text-slate-100">成员与子账号</h2></div>
                <UsersRound className="h-5 w-5 text-cyan-300" />
              </div>
              <div className="mt-4 space-y-2">
                {team.members.map((member) => (
                  <div key={member.id} className="flex items-center gap-3 rounded-2xl border border-white/[0.06] bg-slate-950/25 p-3">
                    <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-cyan-400/20 to-violet-400/20 text-sm font-bold text-cyan-100">{member.nickname.slice(0, 1).toUpperCase()}</span>
                    <div className="min-w-0 flex-1"><p className="truncate text-sm font-medium text-slate-100">{member.nickname}</p><p className="truncate text-xs text-slate-500">{member.email || ROLE_LABEL[member.role]} · 剩余 {compactNumber(member.quota.remaining)}</p></div>
                    <span className="text-xs text-cyan-200/65">{ROLE_LABEL[member.role]}</span>
                    {canManage && member.role !== "owner" && <button onClick={() => removeMember(member.id, member.nickname)} className="rounded-lg p-2 text-slate-500 hover:bg-rose-300/10 hover:text-rose-300" aria-label={`移除 ${member.nickname}`}><Trash2 className="h-4 w-4" /></button>}
                  </div>
                ))}
              </div>
            </article>
          </div>

          <div className="space-y-5">
            <article className="team-glass-card rounded-3xl p-5 sm:p-6">
              <div className="flex items-center justify-between gap-3"><div><p className="text-xs font-semibold tracking-[0.18em] text-cyan-200/60">INVITATION CONTROL</p><h2 className="mt-1 text-lg font-semibold text-slate-100">邀请子账号</h2></div><UserPlus className="h-5 w-5 text-cyan-300" /></div>
              {canManage ? (
                <form onSubmit={invite} className="mt-5 grid gap-3 md:grid-cols-[1fr_150px_auto]">
                  <Input type="email" required value={email} onChange={(event) => setEmail(event.target.value)} placeholder="member@company.com" aria-label="成员邮箱" className="border-cyan-200/15 bg-slate-950/55 text-slate-100 placeholder:text-slate-600 focus-visible:ring-cyan-300/40" />
                  <select value={role} onChange={(event) => setRole(event.target.value as "admin" | "member")} className="decision-select decision-form-input" aria-label="成员权限"><option value="member">协作成员</option><option value="admin">管理员</option></select>
                  <Button variant="gradient" disabled={sendingInvite}>{sendingInvite ? <Loader2 className="h-4 w-4 animate-spin" /> : <Mail className="h-4 w-4" />}发送邀请</Button>
                </form>
              ) : <p className="mt-4 text-sm text-slate-500">只有负责人和管理员可以邀请新成员。</p>}
              {!team.smtp_configured && <div className="mt-4 rounded-2xl border border-amber-300/15 bg-amber-300/[0.06] p-3 text-xs leading-5 text-amber-100/75">SMTP 尚未配置。系统仍会生成安全邀请链接，负责人可以复制后通过企业微信等渠道发送。</div>}
              {lastInviteUrl && <div className="mt-3 flex gap-2 rounded-2xl border border-cyan-300/15 bg-cyan-300/[0.05] p-3"><p className="min-w-0 flex-1 truncate text-xs text-cyan-100/70">{lastInviteUrl}</p><button onClick={() => navigator.clipboard.writeText(lastInviteUrl).then(() => toast("success", "邀请链接已复制"))} className="text-cyan-200" aria-label="复制邀请链接"><Copy className="h-4 w-4" /></button></div>}
              <div className="mt-4 space-y-2">{team.invitations.filter((item) => item.status === "pending").slice(0, 5).map((item) => <div key={item.id} className="flex items-center justify-between rounded-xl border border-white/[0.05] px-3 py-2 text-xs"><span className="truncate text-slate-300">{item.email}</span><span className={item.delivery_status === "sent" ? "text-emerald-300" : "text-amber-300"}>{item.delivery_status === "sent" ? "邮件已发送" : "等待转发链接"}</span></div>)}</div>
            </article>

            <article className="team-glass-card flex min-h-[520px] flex-col rounded-3xl p-5 sm:p-6">
              <div className="flex items-center justify-between gap-3"><div><p className="text-xs font-semibold tracking-[0.18em] text-cyan-200/60">TEAM LIVE CHANNEL</p><h2 className="mt-1 text-lg font-semibold text-slate-100">团队实时聊天室</h2></div><span className="flex items-center gap-2 text-xs text-emerald-300"><span className="h-2 w-2 animate-pulse rounded-full bg-emerald-400" />实时同步</span></div>
              <div className="mt-5 flex-1 space-y-3 overflow-y-auto pr-1">
                {messages.length ? messages.map((message) => <div key={message.id} className="rounded-2xl border border-white/[0.06] bg-slate-950/25 p-3"><div className="flex items-center justify-between gap-3"><span className="text-xs font-semibold text-cyan-100">{message.author_name}</span><span className="text-[11px] text-slate-600">{formatTime(message.created_at)}</span></div><p className="mt-2 whitespace-pre-wrap break-words text-sm leading-6 text-slate-300">{message.content}</p></div>) : <div className="flex min-h-60 flex-col items-center justify-center text-center"><MessageSquareText className="h-8 w-8 text-cyan-300/50" /><p className="mt-3 text-sm text-slate-500">还没有团队消息。可以先同步本轮评审目标。</p></div>}
              </div>
              <form onSubmit={sendMessage} className="mt-4 flex gap-2"><Input value={chatText} onChange={(event) => setChatText(event.target.value)} maxLength={2000} placeholder="同步进展、@同事或发起评审…" aria-label="团队消息" className="border-cyan-200/15 bg-slate-950/55 text-slate-100 placeholder:text-slate-600 focus-visible:ring-cyan-300/40" /><Button type="submit" variant="gradient" disabled={sendingChat || !chatText.trim()}>{sendingChat ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}</Button></form>
            </article>
          </div>
        </section>

        <section className="team-glass-card mt-5 rounded-3xl p-5 sm:p-6">
          <div className="flex items-center justify-between gap-3"><div><p className="text-xs font-semibold tracking-[0.18em] text-cyan-200/60">SHARED DECISION SPACES</p><h2 className="mt-1 text-lg font-semibold text-slate-100">团队 PRD 与决策项目</h2></div><FileText className="h-5 w-5 text-cyan-300" /></div>
          <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {team.recent_sessions.length ? team.recent_sessions.map((session) => <Link key={session.id} href={`/session/${session.id}/decision`} className="group rounded-2xl border border-white/[0.07] bg-slate-950/25 p-4 transition hover:-translate-y-0.5 hover:border-cyan-300/25"><div className="flex items-start justify-between gap-3"><p className="line-clamp-2 text-sm font-medium leading-6 text-slate-100">{session.problem_statement || "未命名产品项目"}</p><CheckCircle2 className="h-4 w-4 shrink-0 text-cyan-300/60" /></div><p className="mt-3 text-xs text-slate-500">{session.prd_count} 份 PRD · {formatTime(session.updated_at)}</p></Link>) : <div className="col-span-full rounded-2xl border border-dashed border-cyan-200/10 p-8 text-center text-sm text-slate-500">暂时没有共享项目。进入任意项目的决策中心，点击“共享到团队”。</div>}
          </div>
        </section>
      </div>
    </main>
  );
}

function Metric({ icon, label, value, note }: { icon: React.ReactNode; label: string; value: string | number; note: string }) {
  return <article className="team-glass-card rounded-2xl p-4"><div className="flex items-center justify-between text-cyan-200/65"><span className="text-xs font-semibold tracking-[0.12em]">{label}</span><span className="flex h-9 w-9 items-center justify-center rounded-xl border border-cyan-200/10 bg-cyan-300/[0.06]">{icon}</span></div><p className="mt-4 text-3xl font-semibold tabular-nums text-slate-50">{value}</p><p className="mt-1 text-xs text-slate-500">{note}</p></article>;
}

function Guide({ index, title, text }: { index: string; title: string; text: string }) {
  return <div className="flex gap-3 rounded-2xl border border-white/[0.06] bg-slate-950/20 p-3"><span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-cyan-300/[0.08] text-xs font-bold text-cyan-200">{index}</span><div><p className="text-sm font-medium text-slate-200">{title}</p><p className="mt-1 text-xs leading-5 text-slate-500">{text}</p></div></div>;
}
