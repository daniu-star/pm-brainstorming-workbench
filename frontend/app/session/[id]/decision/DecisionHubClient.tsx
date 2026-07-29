"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  BarChart3,
  CheckCircle2,
  ChevronRight,
  CircleDotDashed,
  DatabaseZap,
  ExternalLink,
  FileSearch,
  FlaskConical,
  Gauge,
  Lightbulb,
  Link2,
  Plus,
  ShieldCheck,
  Sparkles,
  Target,
  Trash2,
  UserPlus,
  UsersRound,
} from "lucide-react";
import { api } from "@/lib/api";
import type {
  DecisionEvidence,
  DecisionExperiment,
  DecisionHub,
  DecisionInitiative,
  EvidenceSourceType,
  ExperimentStatus,
} from "@/lib/types";
import { toast } from "@/components/Toast";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

type FormKind = "evidence" | "initiative" | "experiment" | null;

const SOURCE_META: Record<EvidenceSourceType, { label: string; color: string }> = {
  interview: { label: "用户访谈", color: "text-cyan-200 border-cyan-300/20 bg-cyan-300/[0.08]" },
  feedback: { label: "客户反馈", color: "text-violet-200 border-violet-300/20 bg-violet-300/[0.08]" },
  metric: { label: "业务数据", color: "text-emerald-200 border-emerald-300/20 bg-emerald-300/[0.08]" },
  competitor: { label: "竞品情报", color: "text-amber-200 border-amber-300/20 bg-amber-300/[0.08]" },
  document: { label: "内部文档", color: "text-sky-200 border-sky-300/20 bg-sky-300/[0.08]" },
  manual: { label: "人工记录", color: "text-slate-200 border-slate-300/20 bg-slate-300/[0.08]" },
};

const EXPERIMENT_META: Record<ExperimentStatus, { label: string; className: string }> = {
  planned: { label: "待启动", className: "border-slate-300/20 bg-slate-300/[0.08] text-slate-200" },
  running: { label: "进行中", className: "border-cyan-300/20 bg-cyan-300/[0.08] text-cyan-100" },
  validated: { label: "已验证", className: "border-emerald-300/20 bg-emerald-300/[0.08] text-emerald-100" },
  invalidated: { label: "未成立", className: "border-rose-300/20 bg-rose-300/[0.08] text-rose-100" },
};

const EMPTY_HUB: DecisionHub = { evidence: [], initiatives: [], experiments: [], roadmap_items: [], prd_versions: [], review_space: { comments: [], votes: [], approvals: [], audit_log: [], share_token: "", share_enabled: false }, agent_config: { template: "saas", company_knowledge: "", audit_rules: [], agents: [] }, metric_reviews: [], updated_at: "" };

function getSessionId(params: ReturnType<typeof useParams>) {
  const value = params?.id;
  return Array.isArray(value) ? value[0] : value;
}

function dateLabel(value?: string) {
  if (!value) return "刚刚";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "刚刚";
  return new Intl.DateTimeFormat("zh-CN", { month: "short", day: "numeric" }).format(date);
}

function initiativeLabel(initiativeId: string, initiatives: DecisionInitiative[]) {
  return initiatives.find((item) => item.id === initiativeId)?.title || "未关联候选方案";
}

export default function DecisionHubClient() {
  const params = useParams();
  const sessionId = getSessionId(params);
  const [hub, setHub] = useState<DecisionHub>(EMPTY_HUB);
  const [problem, setProblem] = useState("");
  const [teamId, setTeamId] = useState("");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [openForm, setOpenForm] = useState<FormKind>(null);
  const [evidenceIds, setEvidenceIds] = useState<string[]>([]);

  const evidenceById = useMemo(
    () => new Map(hub.evidence.map((evidence) => [evidence.id, evidence])),
    [hub.evidence]
  );
  const activeExperiments = hub.experiments.filter((item) => item.status === "running").length;
  const validatedExperiments = hub.experiments.filter((item) => item.status === "validated").length;

  const refresh = async () => {
    if (!sessionId) return;
    const [nextHub, session] = await Promise.all([
      api<DecisionHub>(`/api/session/${sessionId}/decision-hub`),
      api<{ problem_statement?: string; team_id?: string }>(`/api/session/${sessionId}`),
    ]);
    setHub(nextHub);
    setProblem(session.problem_statement || "产品决策空间");
    setTeamId(session.team_id || "");
  };

  useEffect(() => {
    if (!sessionId) return;
    setLoading(true);
    refresh()
      .catch((error) => toast("error", error instanceof Error ? error.message : "决策中心加载失败"))
      .finally(() => setLoading(false));
  }, [sessionId]);

  const submit = async <T,>(path: string, body: Record<string, unknown>): Promise<T | undefined> => {
    if (!sessionId) return undefined;
    setSubmitting(true);
    try {
      const result = await api<T>(`/api/session/${sessionId}/decision-hub${path}`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      await refresh();
      setOpenForm(null);
      toast("success", "已同步到决策证据链");
      return result;
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "保存失败，请重试");
      return undefined;
    } finally {
      setSubmitting(false);
    }
  };

  const remove = async (path: string, title: string) => {
    if (!sessionId || !window.confirm(`确认删除「${title}」？`)) return;
    try {
      await api(`/api/session/${sessionId}/decision-hub${path}`, { method: "DELETE" });
      await refresh();
      toast("success", "已从决策空间移除");
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "删除失败，请重试");
    }
  };

  const updateExperiment = async (experiment: DecisionExperiment, status: ExperimentStatus) => {
    if (!sessionId) return;
    try {
      await api(`/api/session/${sessionId}/decision-hub/experiments/${experiment.id}`, {
        method: "PATCH",
        body: JSON.stringify({ status, learning: experiment.learning || "" }),
      });
      await refresh();
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "实验状态更新失败");
    }
  };

  const shareWithTeam = async () => {
    if (!sessionId) return;
    try {
      const result = await api<{ team_id: string }>(`/api/team/share-session/${sessionId}`, { method: "POST" });
      setTeamId(result.team_id);
      toast("success", "项目已共享到团队，成员现在可以共同评审");
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "共享失败，请重试");
    }
  };

  if (loading) {
    return (
      <main className="decision-hub-shell flex min-h-screen items-center justify-center">
        <div className="text-center">
          <DatabaseZap className="mx-auto h-11 w-11 animate-pulse text-cyan-300" />
          <p className="mt-4 text-sm text-cyan-100/70">正在装载决策证据链…</p>
        </div>
      </main>
    );
  }

  return (
    <main id="main-content" className="decision-hub-shell min-h-screen">
      <div className="decision-hub-grid" aria-hidden="true" />
      <header className="decision-hub-header sticky top-0 z-30 border-b border-cyan-200/10 px-4 py-3 backdrop-blur-2xl md:px-8">
        <div className="mx-auto flex max-w-7xl items-center justify-between gap-4">
          <div className="flex min-w-0 items-center gap-3">
            <Link
              href={sessionId ? `/session/${sessionId}` : "/"}
              className="decision-icon-button flex h-10 w-10 shrink-0 items-center justify-center rounded-xl"
              aria-label="返回产品脑暴工作台"
            >
              <ArrowLeft className="h-4 w-4" />
            </Link>
            <div className="min-w-0">
              <p className="truncate text-[10px] font-semibold tracking-[0.2em] text-cyan-200/55">DECISION INTELLIGENCE / EVIDENCE LEDGER</p>
              <h1 className="truncate text-sm font-semibold text-slate-100 sm:text-base">{problem || "产品决策中心"}</h1>
            </div>
          </div>
          <div className="hidden items-center gap-2 text-xs text-slate-400 md:flex">
            <Link href={`/session/${sessionId}/roadmap`} className="decision-nav-link">产品路线图</Link>
            <Link href={`/session/${sessionId}/prd`} className="decision-nav-link">PRD 中心</Link>
            <Link href={`/session/${sessionId}/review`} className="decision-nav-link">团队评审</Link>
            <Link href={`/session/${sessionId}/agents`} className="decision-nav-link">Agent 配置</Link>
            <Link href={`/session/${sessionId}/metrics`} className="decision-nav-link">数据复盘</Link>
            <Link href="/team" className="decision-nav-link">团队账号</Link>
            <ShieldCheck className="h-4 w-4 text-emerald-300" />
            结论均可追溯至证据与实验
          </div>
        </div>
      </header>

      <section className="relative mx-auto max-w-7xl px-4 pb-14 pt-10 md:px-8 md:pt-14">
        <div className="max-w-3xl">
          <p className="text-[11px] font-semibold tracking-[0.22em] text-cyan-200/60">FROM OPINION TO EVIDENCE</p>
          <h2 className="mt-3 text-3xl font-semibold tracking-[-0.04em] text-slate-50 sm:text-4xl">把脑暴结论变成可验证的产品决策。</h2>
          <p className="mt-4 max-w-2xl text-sm leading-7 text-slate-400">
            收集证据、比较候选方案、定义实验；每一次优先级判断都保留来源、评分与后续学习，避免产品决策停在聊天记录里。
          </p>
        </div>

        <div className="mt-7 grid gap-3 sm:grid-cols-3">
          <MetricCard icon={<FileSearch />} label="已沉淀证据" value={hub.evidence.length} note="可引用的用户与市场信号" />
          <MetricCard icon={<Target />} label="候选方案" value={hub.initiatives.length} note="按 RICE 逻辑自动排序" />
          <MetricCard icon={<FlaskConical />} label="验证闭环" value={`${validatedExperiments}/${hub.experiments.length}`} note={activeExperiments ? `${activeExperiments} 个实验进行中` : "等待启动第一个实验"} />
        </div>

        <section className="decision-card mt-5 rounded-3xl p-5 sm:p-6">
          <div className="flex flex-col gap-5 xl:flex-row xl:items-center xl:justify-between">
            <div>
              <p className="text-[11px] font-semibold tracking-[0.18em] text-cyan-200/60">COLLABORATION PLAYBOOK</p>
              <h3 className="mt-2 text-lg font-semibold text-slate-100">第一次使用？按这条路径完成团队决策。</h3>
              <div className="mt-4 flex flex-wrap gap-2 text-xs text-slate-300">
                {["1. 收集证据", "2. 比较候选方案", "3. 生成 PRD", "4. 邀请成员评审", "5. 投票并记录结论"].map((step) => (
                  <span key={step} className="rounded-xl border border-cyan-200/10 bg-cyan-300/[0.05] px-3 py-2">{step}</span>
                ))}
              </div>
            </div>
            <div className="flex shrink-0 flex-wrap gap-2">
              <Button asChild variant="outline"><Link href="/team"><UsersRound className="h-4 w-4" />管理成员与聊天</Link></Button>
              <Button asChild variant="outline"><Link href={`/session/${sessionId}/review`}><UserPlus className="h-4 w-4" />进入团队评审</Link></Button>
              {teamId ? (
                <span className="inline-flex items-center gap-2 rounded-xl border border-emerald-300/15 bg-emerald-300/[0.07] px-4 py-2 text-xs font-medium text-emerald-200"><CheckCircle2 className="h-4 w-4" />已共享到团队</span>
              ) : (
                <Button variant="gradient" onClick={shareWithTeam}><UsersRound className="h-4 w-4" />共享当前项目</Button>
              )}
            </div>
          </div>
          <p className="mt-4 text-xs leading-5 text-slate-500">共享后，团队成员能查看并编辑这个项目的证据、路线图、PRD 和评审记录；其他未共享的历史项目仍保持私有。</p>
        </section>

        <div className="mt-9 grid gap-5 xl:grid-cols-[1.04fr_0.96fr]">
          <section className="decision-card overflow-hidden rounded-3xl">
            <SectionHeading
              icon={<FileSearch className="h-4 w-4" />}
              eyebrow="EVIDENCE HUB"
              title="产品证据中心"
              description="把访谈、反馈、数据和竞品信号收进同一条证据链。"
              action={<Button variant="outline" size="sm" onClick={() => setOpenForm("evidence")}><Plus className="h-4 w-4" />记录证据</Button>}
            />
            <div className="space-y-3 p-4 pt-0 sm:p-5 sm:pt-0">
              {hub.evidence.length ? hub.evidence.map((evidence) => (
                <EvidenceCard key={evidence.id} evidence={evidence} onDelete={() => remove(`/evidence/${evidence.id}`, evidence.title)} />
              )) : <EmptyState icon={<DatabaseZap />} text="尚未记录证据。先沉淀一条用户信号或业务数据，让后续判断有据可依。" />}
            </div>
          </section>

          <section className="decision-card overflow-hidden rounded-3xl">
            <SectionHeading
              icon={<Target className="h-4 w-4" />}
              eyebrow="PRIORITY BOARD"
              title="优先级决策台"
              description="使用 Reach × Impact × Confidence ÷ Effort 形成可解释的排序。"
              action={<Button variant="gradient" size="sm" onClick={() => setOpenForm("initiative")}><Plus className="h-4 w-4" />新增方案</Button>}
            />
            <div className="space-y-3 p-4 pt-0 sm:p-5 sm:pt-0">
              {hub.initiatives.length ? hub.initiatives.map((initiative, index) => (
                <InitiativeCard
                  key={initiative.id}
                  initiative={initiative}
                  rank={index + 1}
                  evidence={initiative.evidence_ids.map((id) => evidenceById.get(id)).filter(Boolean) as DecisionEvidence[]}
                  onDelete={() => remove(`/initiatives/${initiative.id}`, initiative.title)}
                />
              )) : <EmptyState icon={<Lightbulb />} text="尚无候选方案。把脑暴中值得推进的方向加入这里，系统会根据评分自动排序。" />}
            </div>
          </section>
        </div>

        <section className="decision-card mt-5 overflow-hidden rounded-3xl">
          <SectionHeading
            icon={<FlaskConical className="h-4 w-4" />}
            eyebrow="VALIDATION LAB"
            title="实验验证室"
            description="把“我觉得可行”改写为可被指标证伪或验证的实验。"
            action={<Button variant="outline" size="sm" onClick={() => setOpenForm("experiment")}><Plus className="h-4 w-4" />设计实验</Button>}
          />
          <div className="grid gap-3 p-4 pt-0 md:grid-cols-2 xl:grid-cols-3 sm:p-5 sm:pt-0">
            {hub.experiments.length ? hub.experiments.map((experiment) => (
              <ExperimentCard
                key={experiment.id}
                experiment={experiment}
                initiative={initiativeLabel(experiment.initiative_id, hub.initiatives)}
                onStatusChange={(status) => updateExperiment(experiment, status)}
              />
            )) : <EmptyState icon={<Gauge />} text="还没有实验。为优先级最高的候选方案定义假设、指标与成功条件。" />}
          </div>
        </section>
      </section>

      <Dialog open={openForm !== null} onOpenChange={(open) => !open && setOpenForm(null)}>
        <DialogContent className="decision-form-dialog max-h-[90vh] w-[calc(100%-2rem)] max-w-2xl overflow-y-auto p-5 sm:p-7">
          {openForm === "evidence" && <EvidenceForm busy={submitting} onSubmit={(payload) => submit("/evidence", payload)} />}
          {openForm === "initiative" && <InitiativeForm busy={submitting} evidence={hub.evidence} selectedIds={evidenceIds} onSelectedIdsChange={setEvidenceIds} onSubmit={(payload) => submit("/initiatives", payload)} />}
          {openForm === "experiment" && <ExperimentForm busy={submitting} initiatives={hub.initiatives} onSubmit={(payload) => submit("/experiments", payload)} />}
        </DialogContent>
      </Dialog>
    </main>
  );
}

function MetricCard({ icon, label, value, note }: { icon: React.ReactNode; label: string; value: number | string; note: string }) {
  return (
    <div className="decision-metric-card rounded-2xl p-4">
      <div className="flex items-center justify-between text-cyan-200/75">
        <span className="text-[10px] font-semibold tracking-[0.18em]">{label}</span>
        <span className="flex h-8 w-8 items-center justify-center rounded-xl border border-cyan-200/10 bg-cyan-300/[0.06]">{icon}</span>
      </div>
      <p className="mt-4 font-mono text-3xl font-semibold tabular-nums text-slate-50">{value}</p>
      <p className="mt-1 text-xs text-slate-500">{note}</p>
    </div>
  );
}

function SectionHeading({ icon, eyebrow, title, description, action }: { icon: React.ReactNode; eyebrow: string; title: string; description: string; action: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-4 p-5 sm:flex-row sm:items-center sm:justify-between sm:p-6">
      <div className="flex items-start gap-3">
        <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-cyan-200/15 bg-cyan-300/[0.06] text-cyan-200">{icon}</span>
        <div>
          <p className="text-[10px] font-semibold tracking-[0.18em] text-cyan-200/55">{eyebrow}</p>
          <h3 className="mt-1 text-lg font-semibold text-slate-100">{title}</h3>
          <p className="mt-1 text-xs leading-5 text-slate-500">{description}</p>
        </div>
      </div>
      <div className="shrink-0">{action}</div>
    </div>
  );
}

function EmptyState({ icon, text }: { icon: React.ReactNode; text: string }) {
  return (
    <div className="flex min-h-44 flex-col items-center justify-center rounded-2xl border border-dashed border-cyan-200/12 bg-slate-950/20 px-7 text-center">
      <span className="flex h-10 w-10 items-center justify-center rounded-xl border border-cyan-200/10 bg-cyan-300/[0.04] text-cyan-200/60">{icon}</span>
      <p className="mt-3 max-w-sm text-sm leading-6 text-slate-500">{text}</p>
    </div>
  );
}

function EvidenceCard({ evidence, onDelete }: { evidence: DecisionEvidence; onDelete: () => void }) {
  const meta = SOURCE_META[evidence.source_type];
  return (
    <article className="decision-item-card rounded-2xl p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`rounded-full border px-2 py-0.5 text-[10px] font-medium ${meta.color}`}>{meta.label}</span>
            <span className="text-[10px] text-slate-500">可信度 {evidence.confidence}% · {dateLabel(evidence.created_at)}</span>
          </div>
          <h4 className="mt-2 text-sm font-semibold text-slate-100">{evidence.title}</h4>
        </div>
        <button type="button" onClick={onDelete} className="decision-icon-button flex h-8 w-8 shrink-0 items-center justify-center rounded-lg" aria-label={`删除证据：${evidence.title}`}><Trash2 className="h-3.5 w-3.5" /></button>
      </div>
      <p className="mt-2 text-xs leading-5 text-slate-400">{evidence.summary}</p>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {evidence.tags.map((tag) => <span key={tag} className="rounded-md bg-white/[0.04] px-2 py-1 text-[10px] text-slate-400">#{tag}</span>)}
        {evidence.source_url && <a href={evidence.source_url} target="_blank" rel="noreferrer" className="ml-auto inline-flex items-center gap-1 text-[10px] text-cyan-200 hover:text-cyan-100"><ExternalLink className="h-3 w-3" />查看来源</a>}
      </div>
    </article>
  );
}

function InitiativeCard({ initiative, rank, evidence, onDelete }: { initiative: DecisionInitiative; rank: number; evidence: DecisionEvidence[]; onDelete: () => void }) {
  return (
    <article className="decision-item-card rounded-2xl p-4">
      <div className="flex items-start gap-3">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border border-cyan-200/15 bg-cyan-300/[0.07] font-mono text-xs font-semibold text-cyan-100">{String(rank).padStart(2, "0")}</span>
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h4 className="text-sm font-semibold text-slate-100">{initiative.title}</h4>
              {initiative.description && <p className="mt-1 text-xs leading-5 text-slate-500">{initiative.description}</p>}
            </div>
            <button type="button" onClick={onDelete} className="decision-icon-button flex h-8 w-8 shrink-0 items-center justify-center rounded-lg" aria-label={`删除方案：${initiative.title}`}><Trash2 className="h-3.5 w-3.5" /></button>
          </div>
          <div className="mt-3 grid grid-cols-5 gap-2 text-center">
            <ScoreCell label="Reach" value={initiative.reach.toLocaleString()} />
            <ScoreCell label="Impact" value={initiative.impact} />
            <ScoreCell label="Confidence" value={`${initiative.confidence}%`} />
            <ScoreCell label="Effort" value={initiative.effort} />
            <ScoreCell label="RICE" value={initiative.priority_score.toLocaleString()} emphasis />
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-2 text-[10px] text-slate-500">
            <span className="inline-flex items-center gap-1"><ShieldCheck className="h-3 w-3 text-cyan-200/70" />风险 {initiative.risk}/5</span>
            {evidence.length ? evidence.map((item) => <span key={item.id} className="inline-flex max-w-40 items-center gap-1 truncate rounded-md bg-cyan-300/[0.05] px-2 py-1 text-cyan-100/80"><Link2 className="h-3 w-3" />{item.title}</span>) : <span>暂无关联证据</span>}
          </div>
        </div>
      </div>
    </article>
  );
}

function ScoreCell({ label, value, emphasis = false }: { label: string; value: string | number; emphasis?: boolean }) {
  return <div className={`rounded-lg border px-1.5 py-2 ${emphasis ? "border-cyan-300/25 bg-cyan-300/[0.08]" : "border-white/[0.06] bg-white/[0.025]"}`}><p className="text-[9px] text-slate-500">{label}</p><p className={`mt-0.5 truncate font-mono text-xs font-semibold ${emphasis ? "text-cyan-100" : "text-slate-200"}`}>{value}</p></div>;
}

function ExperimentCard({ experiment, initiative, onStatusChange }: { experiment: DecisionExperiment; initiative: string; onStatusChange: (status: ExperimentStatus) => void }) {
  const meta = EXPERIMENT_META[experiment.status];
  return (
    <article className="decision-item-card rounded-2xl p-4">
      <div className="flex items-start justify-between gap-3">
        <span className="flex h-9 w-9 items-center justify-center rounded-xl border border-cyan-200/15 bg-cyan-300/[0.06] text-cyan-200"><FlaskConical className="h-4 w-4" /></span>
        <select value={experiment.status} onChange={(event) => onStatusChange(event.target.value as ExperimentStatus)} aria-label={`更新实验状态：${experiment.title}`} className={`decision-select rounded-full border px-2 py-1 text-[10px] ${meta.className}`}>
          {Object.entries(EXPERIMENT_META).map(([value, item]) => <option key={value} value={value}>{item.label}</option>)}
        </select>
      </div>
      <p className="mt-3 text-[10px] font-medium text-cyan-200/60">{initiative}</p>
      <h4 className="mt-1 text-sm font-semibold text-slate-100">{experiment.title}</h4>
      <p className="mt-2 text-xs leading-5 text-slate-400">{experiment.hypothesis}</p>
      <div className="mt-3 rounded-xl border border-white/[0.06] bg-slate-950/30 p-3">
        <p className="text-[9px] font-semibold tracking-[0.14em] text-slate-500">PRIMARY METRIC</p>
        <p className="mt-1 text-xs text-slate-200">{experiment.primary_metric}</p>
        {experiment.success_criteria && <p className="mt-1 text-[11px] leading-5 text-slate-500">成功条件：{experiment.success_criteria}</p>}
      </div>
      {experiment.learning && <div className="mt-3 flex gap-2 rounded-xl border border-emerald-300/10 bg-emerald-300/[0.04] p-3 text-xs leading-5 text-emerald-100/80"><CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0" />{experiment.learning}</div>}
    </article>
  );
}

function EvidenceForm({ busy, onSubmit }: { busy: boolean; onSubmit: (payload: Record<string, unknown>) => void }) {
  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    onSubmit({
      title: data.get("title"), source_type: data.get("source_type"), summary: data.get("summary"), source_url: data.get("source_url"),
      tags: String(data.get("tags") || "").split(/[,，]/).map((tag) => tag.trim()).filter(Boolean), confidence: Number(data.get("confidence") || 70),
    });
  };
  return <form onSubmit={handleSubmit} className="space-y-5"><DialogHeader><p className="text-[10px] font-semibold tracking-[0.18em] text-cyan-200/55">EVIDENCE INTAKE</p><DialogTitle className="text-2xl text-slate-100">记录一条决策证据</DialogTitle><DialogDescription>后续候选方案可直接关联这条来源，避免结论脱离事实。</DialogDescription></DialogHeader><Field label="证据标题"><Input name="title" required placeholder="例如：6/8 位访谈用户在首次使用时放弃" /></Field><div className="grid gap-4 sm:grid-cols-2"><Field label="来源类型"><select name="source_type" className="decision-select decision-form-input" defaultValue="interview">{Object.entries(SOURCE_META).map(([value, meta]) => <option key={value} value={value}>{meta.label}</option>)}</select></Field><Field label="可信度（0–100）"><Input name="confidence" type="number" min="0" max="100" defaultValue="70" required /></Field></div><Field label="关键发现"><Textarea name="summary" required placeholder="记录观察到的行为、原话、数据变化或竞争信号。" className="min-h-28" /></Field><div className="grid gap-4 sm:grid-cols-2"><Field label="来源链接（可选）"><Input name="source_url" type="url" placeholder="https://…" /></Field><Field label="标签（逗号分隔）"><Input name="tags" placeholder="新手引导, 留存, 企业用户" /></Field></div><FormFooter busy={busy} label="保存到证据中心" /></form>;
}

function InitiativeForm({ busy, evidence, selectedIds, onSelectedIdsChange, onSubmit }: { busy: boolean; evidence: DecisionEvidence[]; selectedIds: string[]; onSelectedIdsChange: (ids: string[]) => void; onSubmit: (payload: Record<string, unknown>) => void }) {
  const handleSubmit = (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const data = new FormData(event.currentTarget); onSubmit({ title: data.get("title"), description: data.get("description"), reach: Number(data.get("reach")), impact: Number(data.get("impact")), confidence: Number(data.get("confidence")), effort: Number(data.get("effort")), risk: Number(data.get("risk")), evidence_ids: selectedIds }); };
  const toggleEvidence = (id: string) => onSelectedIdsChange(selectedIds.includes(id) ? selectedIds.filter((item) => item !== id) : [...selectedIds, id]);
  return <form onSubmit={handleSubmit} className="space-y-5"><DialogHeader><p className="text-[10px] font-semibold tracking-[0.18em] text-cyan-200/55">PRIORITY ENGINE</p><DialogTitle className="text-2xl text-slate-100">创建候选方案</DialogTitle><DialogDescription>系统按 Reach × Impact × Confidence ÷ Effort 自动计算优先级得分。</DialogDescription></DialogHeader><Field label="方案名称"><Input name="title" required placeholder="例如：用角色化引导重构首次体验" /></Field><Field label="方案描述（可选）"><Textarea name="description" placeholder="写下为何值得做，以及预期解决的问题。" className="min-h-20" /></Field><div className="grid grid-cols-2 gap-4 sm:grid-cols-4"><Field label="Reach"><Input name="reach" type="number" min="0" defaultValue="100" required /></Field><Field label="Impact"><Input name="impact" type="number" min="0.25" max="5" step="0.25" defaultValue="2" required /></Field><Field label="Confidence %"><Input name="confidence" type="number" min="0" max="100" defaultValue="70" required /></Field><Field label="Effort"><Input name="effort" type="number" min="0.25" step="0.25" defaultValue="2" required /></Field></div><Field label="实施风险（1–5）"><Input name="risk" type="number" min="1" max="5" defaultValue="2" required /></Field><div><p className="mb-2 text-xs font-medium text-slate-300">关联已有证据</p>{evidence.length ? <div className="max-h-44 space-y-2 overflow-y-auto rounded-xl border border-cyan-200/10 bg-slate-950/30 p-3">{evidence.map((item) => <label key={item.id} className="flex cursor-pointer items-start gap-2 rounded-lg p-2 hover:bg-cyan-300/[0.05]"><input type="checkbox" checked={selectedIds.includes(item.id)} onChange={() => toggleEvidence(item.id)} className="mt-0.5 accent-cyan-300" /><span><span className="text-xs text-slate-200">{item.title}</span><span className="mt-0.5 block text-[10px] text-slate-500">{SOURCE_META[item.source_type].label} · 可信度 {item.confidence}%</span></span></label>)}</div> : <p className="rounded-xl border border-dashed border-cyan-200/10 p-3 text-xs leading-5 text-slate-500">建议先记录至少一条证据；你仍可先创建方案，后续再补充。</p>}</div><FormFooter busy={busy} label="计算并加入决策台" /></form>;
}

function ExperimentForm({ busy, initiatives, onSubmit }: { busy: boolean; initiatives: DecisionInitiative[]; onSubmit: (payload: Record<string, unknown>) => void }) {
  const handleSubmit = (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const data = new FormData(event.currentTarget); onSubmit({ title: data.get("title"), hypothesis: data.get("hypothesis"), primary_metric: data.get("primary_metric"), success_criteria: data.get("success_criteria"), initiative_id: data.get("initiative_id") }); };
  return <form onSubmit={handleSubmit} className="space-y-5"><DialogHeader><p className="text-[10px] font-semibold tracking-[0.18em] text-cyan-200/55">VALIDATION LAB</p><DialogTitle className="text-2xl text-slate-100">设计一个验证实验</DialogTitle><DialogDescription>把判断写成可观察的假设和明确的指标，结果会沉淀为下一次决策的证据。</DialogDescription></DialogHeader><Field label="实验名称"><Input name="title" required placeholder="例如：验证角色化引导是否提升首日激活" /></Field><Field label="关联候选方案"><select name="initiative_id" className="decision-select decision-form-input"><option value="">暂不关联</option>{initiatives.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}</select></Field><Field label="假设"><Textarea name="hypothesis" required placeholder="如果我们……，那么……，因为……" className="min-h-24" /></Field><div className="grid gap-4 sm:grid-cols-2"><Field label="主指标"><Input name="primary_metric" required placeholder="例如：首日激活率" /></Field><Field label="成功条件（可选）"><Input name="success_criteria" placeholder="例如：提升 ≥ 15%" /></Field></div><FormFooter busy={busy} label="创建验证实验" /></form>;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) { return <label className="block"><span className="mb-2 block text-xs font-medium text-slate-300">{label}</span>{children}</label>; }
function FormFooter({ busy, label }: { busy: boolean; label: string }) { return <div className="flex justify-end border-t border-cyan-200/10 pt-5"><Button type="submit" variant="gradient" disabled={busy}>{busy ? "正在同步…" : <><Sparkles className="h-4 w-4" />{label}</>}</Button></div>; }
