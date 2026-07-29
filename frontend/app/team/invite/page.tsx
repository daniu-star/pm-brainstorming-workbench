"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { CheckCircle2, Loader2, ShieldCheck, UsersRound } from "lucide-react";
import { api } from "@/lib/api";
import { isLoggedIn, rememberAuthReturnPath } from "@/lib/user";
import { Button } from "@/components/ui/button";
import { toast } from "@/components/Toast";

interface InvitationPreview {
  team_name: string;
  email: string;
  role: "admin" | "member";
  expires_at: string;
}

export default function TeamInvitePage() {
  const router = useRouter();
  const [token, setToken] = useState("");
  const [preview, setPreview] = useState<InvitationPreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [accepting, setAccepting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const nextToken = new URLSearchParams(window.location.search).get("token") || "";
    setToken(nextToken);
    if (!nextToken) {
      setError("邀请链接缺少凭证");
      setLoading(false);
      return;
    }
    api<InvitationPreview>(`/api/team/invitations/preview?token=${encodeURIComponent(nextToken)}`)
      .then(setPreview)
      .catch((reason) => setError(reason instanceof Error ? reason.message : "邀请链接无效"))
      .finally(() => setLoading(false));
  }, []);

  const accept = async () => {
    if (!isLoggedIn()) {
      rememberAuthReturnPath(`/team/invite?token=${encodeURIComponent(token)}`);
      router.push("/login");
      return;
    }
    setAccepting(true);
    try {
      await api("/api/team/invitations/accept", { method: "POST", body: JSON.stringify({ token }) });
      toast("success", "你已加入团队空间");
      router.replace("/team");
    } catch (reason) {
      toast("error", reason instanceof Error ? reason.message : "接受邀请失败");
    } finally {
      setAccepting(false);
    }
  };

  return (
    <main className="team-shell flex min-h-screen items-center justify-center px-4">
      <div className="team-aurora" aria-hidden="true" />
      <section className="team-glass-card relative w-full max-w-xl rounded-[2rem] p-7 text-center sm:p-10">
        <span className="mx-auto flex h-16 w-16 items-center justify-center rounded-2xl border border-cyan-200/15 bg-cyan-300/[0.08] text-cyan-200"><UsersRound className="h-8 w-8" /></span>
        {loading ? <div className="py-14"><Loader2 className="mx-auto h-7 w-7 animate-spin text-cyan-300" /></div> : error ? <>
          <h1 className="mt-6 text-2xl font-semibold text-slate-100">这份邀请暂时无法使用</h1>
          <p className="mt-3 text-sm leading-6 text-slate-500">{error}</p>
          <Button asChild variant="outline" className="mt-7"><Link href="/">返回首页</Link></Button>
        </> : preview && <>
          <p className="mt-6 text-xs font-semibold tracking-[0.2em] text-cyan-200/60">SECURE TEAM INVITATION</p>
          <h1 className="mt-2 text-3xl font-semibold text-slate-50">加入 {preview.team_name}</h1>
          <p className="mt-4 text-sm leading-7 text-slate-400">你将以“{preview.role === "admin" ? "管理员" : "协作成员"}”身份加入。加入后可以查看团队明确共享的 PRD、参与评审并在聊天室实时沟通。</p>
          <div className="mt-6 grid gap-2 text-left text-sm text-slate-300">
            <p className="rounded-xl border border-white/[0.06] p-3"><ShieldCheck className="mr-2 inline h-4 w-4 text-emerald-300" />邀请邮箱：{preview.email}</p>
            <p className="rounded-xl border border-white/[0.06] p-3"><CheckCircle2 className="mr-2 inline h-4 w-4 text-cyan-300" />未共享的个人项目不会向团队公开</p>
          </div>
          <Button variant="gradient" className="mt-7 w-full" onClick={accept} disabled={accepting}>{accepting ? <Loader2 className="h-4 w-4 animate-spin" /> : null}{isLoggedIn() ? "接受邀请并进入团队" : "登录后接受邀请"}</Button>
        </>}
      </section>
    </main>
  );
}
