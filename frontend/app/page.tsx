"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowRight,
  Brain,
  ChartNoAxesCombined,
  Code2,
  Layers3,
  LogOut,
  Palette,
  Play,
  Search,
  Settings,
  ShieldCheck,
  User,
} from "lucide-react";
import { NavButtons } from "@/components/NavButtons";
import { HistoryDrawer } from "@/components/HistoryDrawer";
import { SettingsModal } from "@/components/SettingsModal";
import { RechargeModal } from "@/components/RechargeModal";
import { OnboardingModal } from "@/components/OnboardingModal";
import { CosmicBackground } from "@/components/CosmicBackground";
import { useSessionStore } from "@/store/sessionStore";
import { Button } from "@/components/ui/button";

const QUICK_STARTS = [
  "为企业知识库设计一套 AI 搜索与决策系统",
  "重新设计 SaaS 产品的激活与留存路径",
  "验证一款面向产品经理的 AI 工作台",
];

const EXPERTS = [
  { name: "CTO", caption: "技术与可行性", color: "text-sky-300", icon: Code2 },
  { name: "产品设计", caption: "体验与认知", color: "text-cyan-300", icon: Palette },
  { name: "商业运营", caption: "市场与增长", color: "text-emerald-300", icon: ChartNoAxesCombined },
  { name: "目标用户", caption: "需求与采用", color: "text-amber-300", icon: User },
];

const WORKFLOW = [
  { index: "01", title: "定义问题", body: "产品教练用关键追问校准用户、场景、替代方案与成功标准。" },
  { index: "02", title: "多角色推演", body: "四位 AI 专家从技术、体验、商业与真实用户视角进行交叉质询。" },
  { index: "03", title: "结构化沉淀", body: "把对话实时转化为功能、风险、洞察与待验证假设。" },
  { index: "04", title: "专业审计", body: "进入 AI 审计专业通话，在六个维度上压力测试产品方案。" },
];

export default function LandingPage() {
  const router = useRouter();
  const [problem, setProblem] = useState("");
  const [isCreating, setIsCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mounted, setMounted] = useState(false);
  const { isHistoryOpen, toggleHistory } = useSessionStore();
  const setSettingsOpen = useSessionStore((s) => s.setSettingsOpen);
  const setOnboardingOpen = useSessionStore((s) => s.setOnboardingOpen);
  const hasCompletedOnboarding = useSessionStore((s) => s.hasCompletedOnboarding);
  const userApiKey = useSessionStore((s) => s.userApiKey);
  const tokenQuota = useSessionStore((s) => s.tokenQuota);
  const tokensUsed = useSessionStore((s) => s.tokensUsed);
  const storeIsLoggedIn = useSessionStore((s) => s.isLoggedIn);
  const userNickname = useSessionStore((s) => s.userNickname);
  const storeLogout = useSessionStore((s) => s.logout);

  const needsConfig = !userApiKey && tokenQuota - tokensUsed <= 0;

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    if (!mounted || problem) return;
    const pendingProblem = sessionStorage.getItem("pm-brainstorm-pending-problem");
    if (pendingProblem) setProblem(pendingProblem);
  }, [mounted, problem]);

  useEffect(() => {
    if (storeIsLoggedIn && !hasCompletedOnboarding) {
      const timer = setTimeout(() => setOnboardingOpen(true), 800);
      return () => clearTimeout(timer);
    }
  }, [storeIsLoggedIn, hasCompletedOnboarding, setOnboardingOpen]);

  const handleCreate = async () => {
    if (!problem.trim() || isCreating) return;
    if (!storeIsLoggedIn) {
      sessionStorage.setItem("pm-brainstorm-pending-problem", problem.trim());
      router.push("/login");
      return;
    }
    setIsCreating(true);
    setError(null);
    try {
      await useSessionStore.getState().createSession(problem.trim());
      const sessionId = useSessionStore.getState().sessionId;
      if (sessionId) {
        sessionStorage.removeItem("pm-brainstorm-pending-problem");
        router.push(`/session/${sessionId}?problem=${encodeURIComponent(problem.trim())}`);
      }
    } catch (err) {
      setIsCreating(false);
      const message = err instanceof Error ? err.message : "创建会话失败";
      setError(
        message.includes("Failed to fetch") || message.includes("NetworkError") || message.includes("无法连接")
          ? "无法连接到服务器，请检查网络或稍后重试"
          : message,
      );
    }
  };

  return (
    <main id="main-content" className="landing-shell min-h-dvh overflow-hidden text-zinc-100">
      <div className="landing-grid" aria-hidden="true" />
      <div className="landing-vignette" aria-hidden="true" />
      <CosmicBackground density={96} />

      <header className="fixed inset-x-0 top-0 z-30 border-b border-white/10 bg-[#06090e]/90 px-5 backdrop-blur-xl">
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between">
          <Link href="/" className="flex items-center gap-3" aria-label="PM Brainstorm 首页">
            <span className="flex size-9 items-center justify-center rounded-lg border border-cyan-300/30 bg-cyan-300/10 text-cyan-200">
              <Brain size={18} />
            </span>
            <span>
              <span className="block text-sm font-semibold text-white">PM Brainstorm</span>
              <span className="block text-[10px] uppercase text-cyan-200/60">Decision Intelligence</span>
            </span>
          </Link>

          <div className="flex items-center gap-1.5">
            <Link
              href="/product"
              className="frost-action hidden h-10 items-center gap-2 rounded-lg border border-white/10 px-4 text-xs font-medium text-zinc-300 transition-colors hover:border-cyan-300/30 hover:bg-cyan-300/5 hover:text-white sm:flex"
            >
              <Play size={14} />
              产品全景
            </Link>
            {storeIsLoggedIn ? (
              <>
                <span className="hidden max-w-[100px] truncate text-xs font-semibold text-cyan-100 md:inline">
                  {userNickname || "已登录"}
                </span>
                <Button
                  onClick={storeLogout}
                  variant="ghost"
                  size="icon"
                  className="h-9 w-9 text-zinc-400 hover:bg-white/10 hover:text-white"
                  aria-label="退出登录"
                >
                  <LogOut size={15} />
                </Button>
                <Button
                  onClick={() => setSettingsOpen(true)}
                  variant="ghost"
                  size="icon"
                  className="h-9 w-9 text-zinc-400 hover:bg-white/10 hover:text-white"
                  aria-label="API 设置"
                >
                  <Settings size={15} />
                </Button>
                <div className="landing-nav-tools">
                  <NavButtons currentPage="landing" sessionId={null} onToggleHistory={toggleHistory} />
                </div>
              </>
            ) : (
              <Link
                href="/login"
                className="frost-action inline-flex h-10 items-center gap-2 rounded-lg border border-cyan-300/20 bg-cyan-300/10 px-4 text-xs font-semibold text-cyan-100 hover:bg-cyan-300/15"
              >
                <User size={14} />
                登录
              </Link>
            )}
          </div>
        </div>
      </header>

      <HistoryDrawer isOpen={isHistoryOpen} onClose={toggleHistory} />
      <SettingsModal />
      <RechargeModal />
      <OnboardingModal />

      {storeIsLoggedIn && hasCompletedOnboarding && needsConfig && (
        <div className="fixed left-0 right-0 top-16 z-20 border-b border-amber-300/20 bg-amber-300/10 backdrop-blur-xl">
          <div className="mx-auto flex max-w-4xl items-center justify-center gap-3 px-4 py-2">
            <span className="text-xs text-amber-100">额度已用尽，请配置 API Key 或充值</span>
            <button
              type="button"
              onClick={() => setSettingsOpen(true)}
              className="rounded-md border border-amber-200/20 px-2.5 py-1 text-xs font-semibold text-amber-100 hover:bg-amber-200/10"
            >
              配置
            </button>
          </div>
        </div>
      )}

      <section className="relative z-10 mx-auto grid min-h-dvh max-w-7xl items-center gap-14 px-5 pb-20 pt-28 lg:grid-cols-[1.08fr_0.92fr] lg:px-8">
        <div className={`max-w-3xl transition-all duration-700 ${mounted ? "translate-y-0 opacity-100" : "translate-y-5 opacity-0"}`}>
          <div className="mb-7 inline-flex items-center gap-2 rounded-full border border-cyan-300/20 bg-cyan-300/5 px-3 py-1.5 text-[11px] font-medium text-cyan-100">
            <span className="relative flex size-2">
              <span className="absolute inline-flex size-2 animate-ping rounded-full bg-cyan-300 opacity-50" />
              <span className="relative inline-flex size-2 rounded-full bg-cyan-300" />
            </span>
            面向产品经理的 AI 决策推演系统
          </div>
          <p className="mb-4 text-xs font-semibold uppercase text-cyan-200/70">Product Decision Intelligence / 01</p>
          <h1 className="max-w-3xl text-balance text-5xl font-semibold leading-[1.05] text-white md:text-7xl">
            让每一个产品决策，
            <span className="mt-2 block bg-gradient-to-r from-cyan-200 to-sky-400 bg-clip-text text-transparent">
              在上线之前经得住审计。
            </span>
          </h1>
          <p className="mt-7 max-w-2xl text-pretty text-base leading-8 text-zinc-400 md:text-lg">
            把模糊想法交给四位 AI 专家交叉推演，再通过可视化画布与专业审计通话，
            将直觉转化为可解释、可验证、可推进的产品方案。
          </p>
          <div className="mt-9 flex flex-wrap items-center gap-3">
            <a href="#start" className="frost-action frost-action-primary inline-flex h-12 items-center gap-2 rounded-lg bg-cyan-300 px-6 text-sm font-semibold text-[#031014] shadow-lg shadow-cyan-950/40 transition-transform hover:-translate-y-0.5">
              启动产品推演
              <ArrowRight size={16} />
            </a>
            <Link href="/product" className="frost-action inline-flex h-12 items-center gap-2 rounded-lg border border-white/15 bg-white/[0.03] px-6 text-sm font-medium text-zinc-200 hover:border-white/30 hover:bg-white/[0.06]">
              <Play size={16} />
              了解产品如何工作
            </Link>
          </div>
          <dl className="mt-12 grid max-w-2xl grid-cols-3 divide-x divide-white/10 border-y border-white/10 py-5">
            {[["04", "AI 专家角色"], ["06", "专业审计维度"], ["LIVE", "实时结构化画布"]].map(([value, label]) => (
              <div key={label} className="px-4 first:pl-0">
                <dt className="text-[10px] uppercase text-zinc-500">{label}</dt>
                <dd className="mt-1 font-mono text-xl font-semibold text-cyan-200 tabular-nums">{value}</dd>
              </div>
            ))}
          </dl>
        </div>

        <div id="start" className={`command-panel scroll-mt-24 transition-all delay-150 duration-700 ${mounted ? "translate-y-0 opacity-100" : "translate-y-5 opacity-0"}`}>
          <div className="command-scan" aria-hidden="true" />
          <div className="relative z-10">
            <div className="flex items-start justify-between border-b border-white/10 px-5 py-4">
              <div>
                <p className="text-[10px] font-semibold uppercase text-cyan-200/60">Decision Room</p>
                <h2 className="mt-1 text-base font-semibold text-white">创建一次产品推演</h2>
              </div>
              <span className="inline-flex items-center gap-2 rounded-full border border-emerald-300/20 bg-emerald-300/5 px-2.5 py-1 text-[10px] text-emerald-200">
                <span className="size-1.5 rounded-full bg-emerald-300" />
                系统就绪
              </span>
            </div>
            <div className="grid grid-cols-2 gap-px border-b border-white/10 bg-white/10">
              {EXPERTS.map((expert) => {
                const Icon = expert.icon;
                return (
                  <div key={expert.name} className="flex items-center gap-3 bg-[#0b1119] px-4 py-3.5">
                    <span className={`flex size-9 items-center justify-center rounded-lg bg-white/[0.04] ${expert.color}`}>
                      <Icon size={17} />
                    </span>
                    <span>
                      <span className="block text-xs font-semibold text-zinc-100">{expert.name}</span>
                      <span className="block text-[10px] text-zinc-500">{expert.caption}</span>
                    </span>
                  </div>
                );
              })}
            </div>
            <div className="p-5">
              <label htmlFor="product-direction" className="mb-3 block text-xs font-medium text-zinc-300">
                描述你正在思考的产品问题
              </label>
              <textarea
                id="product-direction"
                value={problem}
                onChange={(event) => setProblem(event.target.value)}
                placeholder="例如：我们需要重新设计企业 SaaS 的新用户激活路径，但不确定流失发生在哪个关键环节……"
                rows={5}
                className="w-full resize-none rounded-xl border border-white/10 bg-black/20 px-4 py-3.5 text-sm leading-6 text-zinc-100 outline-none placeholder:text-zinc-600 focus:border-cyan-300/50 focus:ring-2 focus:ring-cyan-300/10"
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    handleCreate();
                  }
                }}
              />
              <div className="mt-3 flex flex-wrap gap-2">
                {QUICK_STARTS.map((item) => (
                  <button key={item} type="button" onClick={() => setProblem(item)} className="frost-chip rounded-md border border-white/10 px-2.5 py-1.5 text-left text-[10px] text-zinc-500 hover:border-cyan-300/30 hover:text-cyan-100">
                    {item}
                  </button>
                ))}
              </div>
              {error && (
                <p role="alert" className="mt-4 rounded-lg border border-red-400/20 bg-red-400/5 px-3 py-2 text-xs text-red-300">
                  {error}
                </p>
              )}
              <button
                type="button"
                onClick={handleCreate}
                disabled={!problem.trim() || isCreating}
                className="frost-action frost-action-primary mt-5 flex h-12 w-full items-center justify-center gap-2 rounded-lg bg-cyan-300 text-sm font-semibold text-[#031014] hover:bg-cyan-200 disabled:cursor-not-allowed disabled:bg-zinc-800 disabled:text-zinc-600"
              >
                {isCreating ? (
                  <><span className="size-4 animate-spin rounded-full border-2 border-current border-t-transparent" />正在建立决策空间</>
                ) : (
                  <>开始脑暴与审计<ArrowRight size={16} /></>
                )}
              </button>
            </div>
          </div>
        </div>
      </section>

      <section className="relative z-10 border-y border-white/10 bg-[#080d14]/90 px-5 py-20 lg:px-8">
        <div className="mx-auto max-w-7xl">
          <div className="flex flex-col justify-between gap-6 md:flex-row md:items-end">
            <div>
              <p className="text-xs font-semibold uppercase text-cyan-200/60">From ambiguity to evidence</p>
              <h2 className="mt-3 max-w-2xl text-balance text-3xl font-semibold text-white md:text-4xl">
                一条为产品经理设计的决策流水线
              </h2>
            </div>
            <Link href="/product" className="inline-flex items-center gap-2 text-sm font-medium text-cyan-200 hover:text-cyan-100">
              查看完整产品介绍<ArrowRight size={15} />
            </Link>
          </div>
          <div className="mt-10 grid gap-px overflow-hidden rounded-xl border border-white/10 bg-white/10 md:grid-cols-4">
            {WORKFLOW.map((item) => (
              <article key={item.index} className="group bg-[#0a1018] p-6 transition-colors hover:bg-[#0d1620]">
                <span className="font-mono text-xs text-cyan-200/50 tabular-nums">{item.index}</span>
                <h3 className="mt-8 text-lg font-semibold text-white">{item.title}</h3>
                <p className="mt-3 text-pretty text-sm leading-6 text-zinc-500">{item.body}</p>
              </article>
            ))}
          </div>
          <div className="mt-10 grid gap-4 md:grid-cols-3">
            {[
              { icon: Layers3, title: "结构化，不止是聊天", body: "功能、风险、问题和洞察持续沉淀到同一张产品画布。" },
              { icon: ShieldCheck, title: "审计式压力测试", body: "六维问题框架主动寻找方案中的盲区、依赖与不可证伪假设。" },
              { icon: Search, title: "为决策保留证据", body: "每一个结论都能回到提出它的角色和上下文，减少拍脑袋决策。" },
            ].map((item) => {
              const Icon = item.icon;
              return (
                <article key={item.title} className="rounded-xl border border-white/10 bg-white/[0.02] p-5">
                  <Icon size={19} className="text-cyan-200" />
                  <h3 className="mt-5 text-sm font-semibold text-white">{item.title}</h3>
                  <p className="mt-2 text-pretty text-xs leading-5 text-zinc-500">{item.body}</p>
                </article>
              );
            })}
          </div>
        </div>
      </section>
      <footer className="relative z-10 px-5 py-8 text-center text-[11px] text-zinc-600">
        PM Brainstorm · Product Decision Intelligence System
      </footer>
    </main>
  );
}
