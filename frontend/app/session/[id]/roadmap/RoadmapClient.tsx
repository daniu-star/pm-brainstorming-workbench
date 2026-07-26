"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { FormEvent, useEffect, useMemo, useState } from "react";
import { AlertTriangle, ArrowLeft, CalendarRange, CheckCircle2, CircleDotDashed, Compass, Plus, Target, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import type { DecisionHub, DecisionInitiative, RoadmapHorizon, RoadmapItem, RoadmapStatus } from "@/lib/types";
import { toast } from "@/components/Toast";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";

const EMPTY_HUB: DecisionHub = { evidence: [], initiatives: [], experiments: [], roadmap_items: [], prd_versions: [], updated_at: "" };
const HORIZONS: { value: RoadmapHorizon; label: string; helper: string }[] = [
  { value: "now", label: "NOW", helper: "本周期必须推动" },
  { value: "next", label: "NEXT", helper: "下一阶段重点验证" },
  { value: "later", label: "LATER", helper: "方向已确认，等待条件成熟" },
];
const STATUS: Record<RoadmapStatus, { label: string; className: string }> = {
  planned: { label: "待排期", className: "border-slate-300/20 bg-slate-300/[0.08] text-slate-200" },
  in_progress: { label: "进行中", className: "border-cyan-300/20 bg-cyan-300/[0.08] text-cyan-100" },
  at_risk: { label: "有风险", className: "border-amber-300/20 bg-amber-300/[0.08] text-amber-100" },
  done: { label: "已完成", className: "border-emerald-300/20 bg-emerald-300/[0.08] text-emerald-100" },
};

function sessionIdFrom(params: ReturnType<typeof useParams>) { const value = params?.id; return Array.isArray(value) ? value[0] : value; }
function initiativeTitle(id: string, initiatives: DecisionInitiative[]) { return initiatives.find((item) => item.id === id)?.title || "未关联候选方案"; }

export default function RoadmapClient() {
  const params = useParams();
  const sessionId = sessionIdFrom(params);
  const [hub, setHub] = useState<DecisionHub>(EMPTY_HUB);
  const [problem, setProblem] = useState("");
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  const refresh = async () => {
    if (!sessionId) return;
    try {
      const [decision, session] = await Promise.all([api<DecisionHub>(`/api/session/${sessionId}/decision-hub`), api<{ problem_statement?: string }>(`/api/session/${sessionId}`)]);
      setHub({ ...EMPTY_HUB, ...decision });
      setProblem(session.problem_statement || "产品路线图");
    } catch (error) { toast("error", error instanceof Error ? error.message : "路线图加载失败"); }
    finally { setLoading(false); }
  };
  useEffect(() => { refresh(); }, [sessionId]);

  const groups = useMemo(() => Object.fromEntries(HORIZONS.map(({ value }) => [value, hub.roadmap_items.filter((item) => item.horizon === value)])) as Record<RoadmapHorizon, RoadmapItem[]>, [hub.roadmap_items]);
  const submit = async (payload: Record<string, unknown>) => {
    if (!sessionId) return;
    setBusy(true);
    try { await api(`/api/session/${sessionId}/decision-hub/roadmap`, { method: "POST", body: JSON.stringify(payload) }); setOpen(false); toast("success", "路线图事项已加入"); await refresh(); }
    catch (error) { toast("error", error instanceof Error ? error.message : "保存失败"); }
    finally { setBusy(false); }
  };
  const updateStatus = async (item: RoadmapItem, status: RoadmapStatus) => {
    if (!sessionId) return;
    try { await api(`/api/session/${sessionId}/decision-hub/roadmap/${item.id}`, { method: "PATCH", body: JSON.stringify({ status, progress: status === "done" ? 100 : item.progress }) }); toast("success", "状态已更新"); await refresh(); }
    catch (error) { toast("error", error instanceof Error ? error.message : "更新失败"); }
  };
  const remove = async (item: RoadmapItem) => {
    if (!sessionId || !window.confirm(`删除“${item.title}”吗？`)) return;
    try { await api(`/api/session/${sessionId}/decision-hub/roadmap/${item.id}`, { method: "DELETE" }); toast("success", "路线图事项已删除"); await refresh(); }
    catch (error) { toast("error", error instanceof Error ? error.message : "删除失败"); }
  };

  if (loading) return <div className="decision-hub-shell flex min-h-screen items-center justify-center text-sm text-cyan-100/70">正在构建路线图…</div>;
  return <main id="main-content" className="decision-hub-shell min-h-screen px-4 pb-16 pt-5 sm:px-7 lg:px-10">
    <div className="mx-auto max-w-[1500px]">
      <header className="decision-hub-header mb-8 rounded-3xl p-5 sm:p-7">
        <div className="flex flex-col justify-between gap-6 lg:flex-row lg:items-start">
          <div className="min-w-0"><Link href={`/session/${sessionId}`} className="inline-flex items-center gap-2 text-xs text-slate-400 transition hover:text-cyan-100"><ArrowLeft className="h-3.5 w-3.5" />返回产品脑暴工作台</Link><p className="mt-5 text-[10px] font-semibold tracking-[0.24em] text-cyan-200/65">PRODUCT DELIVERY / HORIZON MAP</p><h1 className="mt-2 max-w-3xl text-2xl font-semibold tracking-tight text-slate-50 sm:text-3xl">让策略进入有节奏的交付。</h1><p className="mt-2 max-w-2xl text-sm leading-6 text-slate-400">围绕“{problem}”把优先级判断分配到 Now、Next、Later；每一项都能看到季度、目标、风险和推进状态。</p></div>
          <div className="flex flex-wrap gap-2"><Link href={`/session/${sessionId}/decision`} className="decision-nav-link">决策中心</Link><Link href={`/session/${sessionId}/prd`} className="decision-nav-link">PRD 中心</Link><Button variant="gradient" onClick={() => setOpen(true)}><Plus className="h-4 w-4" />加入路线图</Button></div>
        </div>
      </header>
      <section className="mb-6 grid gap-3 sm:grid-cols-3"><Metric label="已排期事项" value={hub.roadmap_items.length} hint="从决策到交付的全景" icon={<CalendarRange className="h-4 w-4" />} /><Metric label="正在推进" value={hub.roadmap_items.filter((item) => item.status === "in_progress").length} hint="本周期重点事项" icon={<CircleDotDashed className="h-4 w-4" />} /><Metric label="风险信号" value={hub.roadmap_items.filter((item) => item.status === "at_risk").length} hint="需要尽快处理的阻塞" icon={<AlertTriangle className="h-4 w-4" />} /></section>
      <section className="grid gap-5 xl:grid-cols-3">{HORIZONS.map((horizon) => <div key={horizon.value} className="roadmap-column rounded-3xl p-4 sm:p-5"><div className="mb-5 flex items-start justify-between gap-3"><div><p className="text-[10px] font-semibold tracking-[0.2em] text-cyan-200/60">{horizon.label}</p><h2 className="mt-1 text-lg font-semibold text-slate-100">{horizon.helper}</h2></div><span className="rounded-xl border border-cyan-200/12 bg-cyan-300/[0.05] px-2.5 py-1 text-xs text-cyan-100">{groups[horizon.value].length}</span></div><div className="space-y-3">{groups[horizon.value].length ? groups[horizon.value].map((item) => <RoadmapCard key={item.id} item={item} initiatives={hub.initiatives} onStatusChange={(status) => updateStatus(item, status)} onDelete={() => remove(item)} />) : <div className="flex min-h-48 flex-col items-center justify-center rounded-2xl border border-dashed border-cyan-200/10 px-6 text-center"><Compass className="h-5 w-5 text-cyan-200/35" /><p className="mt-3 text-xs leading-5 text-slate-500">还没有该时间范围的事项。把已验证的优先级结论放进合适的节奏。</p></div>}</div></div>)}</section>
    </div>
    <Dialog open={open} onOpenChange={setOpen}><DialogContent className="decision-dialog max-h-[90vh] overflow-y-auto"><RoadmapForm busy={busy} initiatives={hub.initiatives} onSubmit={submit} /></DialogContent></Dialog>
  </main>;
}

function Metric({ label, value, hint, icon }: { label: string; value: number; hint: string; icon: React.ReactNode }) { return <article className="decision-metric-card rounded-2xl p-4"><div className="flex items-center justify-between"><p className="text-xs font-medium text-cyan-100/75">{label}</p><span className="text-cyan-200/70">{icon}</span></div><p className="mt-3 font-mono text-3xl font-semibold text-slate-100">{value}</p><p className="mt-1 text-[11px] text-slate-500">{hint}</p></article>; }
function RoadmapCard({ item, initiatives, onStatusChange, onDelete }: { item: RoadmapItem; initiatives: DecisionInitiative[]; onStatusChange: (status: RoadmapStatus) => void; onDelete: () => void }) { const status = STATUS[item.status]; return <article className="decision-item-card rounded-2xl p-4"><div className="flex items-start justify-between gap-3"><div><div className="flex flex-wrap items-center gap-2"><span className={`rounded-full border px-2 py-0.5 text-[10px] font-medium ${status.className}`}>{status.label}</span>{item.quarter && <span className="text-[10px] text-slate-500">{item.quarter}</span>}</div><h3 className="mt-2 text-sm font-semibold text-slate-100">{item.title}</h3></div><button type="button" onClick={onDelete} className="decision-icon-button flex h-8 w-8 items-center justify-center rounded-lg" aria-label={`删除路线图事项：${item.title}`}><Trash2 className="h-3.5 w-3.5" /></button></div>{item.objective && <p className="mt-2 text-xs leading-5 text-slate-400">{item.objective}</p>}<div className="mt-3"><div className="flex items-center justify-between text-[10px] text-slate-500"><span>版本进度</span><span className="font-mono text-cyan-100">{item.progress}%</span></div><div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-white/[0.06]"><div className="h-full rounded-full bg-gradient-to-r from-cyan-300 to-violet-400" style={{ width: `${item.progress}%` }} /></div></div><div className="mt-3 flex flex-wrap items-center gap-2"><span className="inline-flex max-w-full items-center gap-1 truncate rounded-lg bg-cyan-300/[0.05] px-2 py-1 text-[10px] text-cyan-100/80"><Target className="h-3 w-3" />{initiativeTitle(item.initiative_id, initiatives)}</span><select value={item.status} onChange={(event) => onStatusChange(event.target.value as RoadmapStatus)} className="decision-select ml-auto rounded-lg border border-white/[0.08] bg-slate-950/50 px-2 py-1 text-[10px] text-slate-300" aria-label={`更新${item.title}状态`}>{Object.entries(STATUS).map(([value, meta]) => <option key={value} value={value}>{meta.label}</option>)}</select></div>{item.risk_note && <p className="mt-3 flex gap-1.5 rounded-lg border border-amber-300/10 bg-amber-300/[0.04] p-2 text-[11px] leading-5 text-amber-100/80"><AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />{item.risk_note}</p>}</article>; }
function RoadmapForm({ busy, initiatives, onSubmit }: { busy: boolean; initiatives: DecisionInitiative[]; onSubmit: (payload: Record<string, unknown>) => void }) { const handleSubmit = (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const data = new FormData(event.currentTarget); onSubmit({ title: data.get("title"), horizon: data.get("horizon"), quarter: data.get("quarter"), objective: data.get("objective"), status: data.get("status"), progress: Number(data.get("progress") || 0), initiative_id: data.get("initiative_id"), risk_note: data.get("risk_note") }); }; return <form onSubmit={handleSubmit} className="space-y-5"><DialogHeader><p className="text-[10px] font-semibold tracking-[0.18em] text-cyan-200/55">PRODUCT DELIVERY</p><DialogTitle className="text-2xl text-slate-100">加入一项路线图工作</DialogTitle><DialogDescription>把优先级结论转成可推进的时间窗口、目标和风险承诺。</DialogDescription></DialogHeader><Field label="事项名称"><Input name="title" required placeholder="例如：重构首次价值引导" /></Field><div className="grid gap-4 sm:grid-cols-2"><Field label="时间范围"><select name="horizon" className="decision-select decision-form-input" defaultValue="next">{HORIZONS.map((item) => <option key={item.value} value={item.value}>{item.label} · {item.helper}</option>)}</select></Field><Field label="季度 / 版本"><Input name="quarter" placeholder="例如：2026 Q3" /></Field></div><Field label="关联候选方案"><select name="initiative_id" className="decision-select decision-form-input"><option value="">暂不关联</option>{initiatives.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}</select></Field><Field label="交付目标"><Textarea name="objective" className="min-h-20" placeholder="本阶段要达成的可观察成果" /></Field><div className="grid gap-4 sm:grid-cols-3"><Field label="状态"><select name="status" className="decision-select decision-form-input" defaultValue="planned">{Object.entries(STATUS).map(([value, item]) => <option key={value} value={value}>{item.label}</option>)}</select></Field><Field label="初始进度"><Input name="progress" type="number" min="0" max="100" defaultValue="0" /></Field><Field label="风险提示"><Input name="risk_note" placeholder="可选" /></Field></div><div className="flex justify-end border-t border-cyan-200/10 pt-5"><Button type="submit" variant="gradient" disabled={busy}>{busy ? "正在同步…" : <><CheckCircle2 className="h-4 w-4" />加入路线图</>}</Button></div></form>; }
function Field({ label, children }: { label: string; children: React.ReactNode }) { return <label className="block"><span className="mb-2 block text-xs font-medium text-slate-300">{label}</span>{children}</label>; }
