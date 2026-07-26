import Link from "next/link";
import {
  ArrowRight,
  Brain,
  Code2,
  Layers3,
  Play,
  Radio,
  Search,
  ShieldCheck,
  Video,
} from "lucide-react";
import { CosmicBackground } from "@/components/CosmicBackground";

const CAPABILITIES = [
  {
    code: "01 / DISCOVERY",
    icon: Search,
    title: "产品教练",
    description: "在讨论开始前，澄清目标用户、核心问题、现有替代方案、产品形态与成功标准。",
  },
  {
    code: "02 / SIMULATION",
    icon: Brain,
    title: "多角色圆桌",
    description: "CTO、设计师、运营和目标用户从互相冲突的立场，对方案进行交叉推演。",
  },
  {
    code: "03 / SYNTHESIS",
    icon: Layers3,
    title: "实时产品画布",
    description: "把长对话压缩为可追溯的功能树，标记功能、风险、问题与关键洞察。",
  },
  {
    code: "04 / AUDIT",
    icon: Video,
    title: "AI 审计专业通话",
    description: "用专业语音审计空间承载六维压力测试，让审计过程更专注、更具连续性。",
  },
];

const STEPS = [
  ["输入议题", "用一句话描述你正在推进、怀疑或难以取舍的产品方向。"],
  ["回答澄清", "产品教练通过 3–5 个问题建立完整的决策上下文。"],
  ["观察推演", "四位专家依次给出判断，你可以随时追问任一角色。"],
  ["进入审计", "在专业通话中回答连续质询，最终得到未解决缺口清单。"],
];

export default function ProductPage() {
  return (
    <main id="main-content" className="product-cinema min-h-dvh overflow-hidden text-zinc-100">
      <CosmicBackground density={82} />
      <header className="fixed inset-x-0 top-0 z-30 border-b border-white/10 bg-[#05080c]/90 px-5 backdrop-blur-xl">
        <div className="mx-auto flex h-16 max-w-7xl items-center justify-between">
          <Link href="/" className="flex items-center gap-3">
            <span className="flex size-9 items-center justify-center rounded-lg border border-cyan-300/30 bg-cyan-300/10 text-cyan-200">
              <Brain size={18} />
            </span>
            <span className="text-sm font-semibold text-white">PM Brainstorm</span>
          </Link>
          <Link
            href="/"
            className="frost-action frost-action-primary inline-flex h-10 items-center gap-2 rounded-lg bg-cyan-300 px-4 text-xs font-semibold text-[#031014] transition-colors hover:bg-cyan-200"
          >
            开始使用
            <ArrowRight size={14} />
          </Link>
        </div>
      </header>

      <section className="relative mx-auto flex min-h-dvh max-w-7xl items-center px-5 pb-16 pt-28 lg:px-8">
        <div className="grid w-full items-center gap-14 lg:grid-cols-[0.9fr_1.1fr]">
          <div className="cinema-reveal">
            <div className="mb-7 flex items-center gap-3 text-[10px] font-semibold uppercase text-cyan-200/60">
              <span className="h-px w-12 bg-cyan-200/50" />
              Product Intelligence Film / 2026
            </div>
            <h1 className="text-balance text-5xl font-semibold leading-[1.02] text-white md:text-7xl">
              产品决策，
              <span className="block text-cyan-200">不再依赖孤独思考。</span>
            </h1>
            <p className="mt-7 max-w-xl text-pretty text-base leading-8 text-zinc-400">
              PM Brainstorm 是面向产品经理的 AI 决策推演系统。它不是另一个聊天机器人，
              而是一间随时待命的产品作战室：负责澄清问题、制造观点冲突、沉淀结构，并审计你的方案。
            </p>
            <div className="mt-9 flex flex-wrap gap-3">
              <Link href="/" className="frost-action frost-action-primary inline-flex h-12 items-center gap-2 rounded-lg bg-cyan-300 px-6 text-sm font-semibold text-[#031014] hover:bg-cyan-200">
                进入决策空间
                <ArrowRight size={15} />
              </Link>
              <a href="#how-it-works" className="frost-action inline-flex h-12 items-center gap-2 rounded-lg border border-white/15 px-6 text-sm font-medium text-zinc-300 hover:border-white/30 hover:text-white">
                <Play size={15} />
                观看工作方式
              </a>
            </div>
          </div>

          <div className="film-frame cinema-reveal rounded-2xl p-5 md:p-8">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-xs uppercase text-zinc-400">Decision Signal / Demo</p>
                <h2 className="mt-1 text-sm font-semibold text-white">产品方案审计态势</h2>
              </div>
              <span className="flex items-center gap-2 text-xs text-emerald-200">
                <span className="size-1.5 rounded-full bg-emerald-300" />
                演示数据
              </span>
            </div>
            <div className="film-rule my-6" />
            <div className="grid gap-5 sm:grid-cols-[1.1fr_0.9fr]">
              <div className="rounded-xl border border-white/10 bg-black/20 p-4">
                <div className="flex items-center justify-between">
                  <span className="text-[10px] uppercase text-zinc-500">Risk exposure</span>
                  <span className="font-mono text-xs text-cyan-200 tabular-nums">68 / 100</span>
                </div>
                <svg viewBox="0 0 420 170" className="mt-4 w-full" role="img" aria-label="产品风险趋势图">
                  <defs>
                    <linearGradient id="risk-area" x1="0" x2="0" y1="0" y2="1">
                      <stop offset="0%" stopColor="#67e8f9" stopOpacity="0.28" />
                      <stop offset="100%" stopColor="#67e8f9" stopOpacity="0" />
                    </linearGradient>
                  </defs>
                  {[30, 70, 110, 150].map((y) => (
                    <line key={y} x1="0" y1={y} x2="420" y2={y} stroke="rgba(255,255,255,.08)" />
                  ))}
                  <path d="M0 140 C55 132 70 86 122 101 S205 126 245 72 S330 36 420 49 V170 H0Z" fill="url(#risk-area)" />
                  <path d="M0 140 C55 132 70 86 122 101 S205 126 245 72 S330 36 420 49" fill="none" stroke="#67e8f9" strokeWidth="3" />
                  <circle cx="420" cy="49" r="5" fill="#67e8f9" />
                </svg>
                <div className="mt-3 flex justify-between text-xs text-zinc-400">
                  <span>问题定义</span><span>方案推演</span><span>专业审计</span>
                </div>
              </div>
              <div className="space-y-3">
                {[
                  ["问题证据", 82],
                  ["方案聚焦", 71],
                  ["技术可行", 64],
                  ["商业闭环", 48],
                ].map(([label, score]) => (
                  <div key={label as string} className="rounded-lg border border-white/10 bg-white/[0.02] p-3">
                    <div className="flex items-center justify-between text-xs">
                      <span className="text-zinc-400">{label}</span>
                      <span className="font-mono text-zinc-200 tabular-nums">{score}%</span>
                    </div>
                    <div className="mt-2 h-1 overflow-hidden rounded-full bg-white/10">
                      <div className="h-full rounded-full bg-cyan-300" style={{ width: `${score}%` }} />
                    </div>
                  </div>
                ))}
              </div>
            </div>
            <div className="mt-5 grid grid-cols-3 gap-px overflow-hidden rounded-lg border border-white/10 bg-white/10 text-center">
              {[["17", "关键洞察"], ["09", "显性风险"], ["06", "待验证假设"]].map(([value, label]) => (
                <div key={label} className="bg-[#091018] px-3 py-4">
                  <div className="font-mono text-xl font-semibold text-white tabular-nums">{value}</div>
                  <div className="mt-1 text-xs text-zinc-400">{label}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      <section className="border-y border-white/10 bg-[#080d13] px-5 py-24 lg:px-8">
        <div className="mx-auto max-w-7xl">
          <p className="text-xs font-semibold uppercase text-cyan-200/60">The system</p>
          <h2 className="mt-4 max-w-3xl text-balance text-4xl font-semibold text-white md:text-5xl">
            四个能力模块，组成一条完整决策链
          </h2>
          <div className="mt-12 grid gap-px overflow-hidden rounded-xl border border-white/10 bg-white/10 md:grid-cols-2">
            {CAPABILITIES.map((item) => {
              const Icon = item.icon;
              return (
                <article key={item.title} className="group bg-[#090f16] p-7 transition-colors hover:bg-[#0c151f]">
                  <div className="flex items-start justify-between">
                    <Icon size={22} className="text-cyan-200" />
                    <span className="font-mono text-xs text-zinc-400">{item.code}</span>
                  </div>
                  <h3 className="mt-10 text-xl font-semibold text-white">{item.title}</h3>
                  <p className="mt-3 max-w-lg text-pretty text-sm leading-7 text-zinc-500">{item.description}</p>
                </article>
              );
            })}
          </div>
        </div>
      </section>

      <section id="how-it-works" className="px-5 py-24 lg:px-8">
        <div className="mx-auto grid max-w-7xl gap-16 lg:grid-cols-[0.8fr_1.2fr]">
          <div>
            <p className="text-xs font-semibold uppercase text-cyan-200/60">Field guide</p>
            <h2 className="mt-4 text-balance text-4xl font-semibold text-white">四步完成一次产品审计</h2>
            <p className="mt-5 text-pretty text-sm leading-7 text-zinc-500">
              无需学习复杂框架。系统把成熟的产品方法论放进对话与审计流程，让你专注于判断本身。
            </p>
            <div className="mt-8 flex items-center gap-3 rounded-xl border border-cyan-300/15 bg-cyan-300/5 p-4">
              <ShieldCheck size={20} className="shrink-0 text-cyan-200" />
              <p className="text-xs leading-5 text-cyan-50/70">
                审计覆盖问题有效性、方案有效性、技术风险、商业可行性、用户采用和执行风险。
              </p>
            </div>
          </div>
          <ol className="space-y-3">
            {STEPS.map(([title, body], index) => (
              <li key={title} className="grid grid-cols-[3rem_1fr] gap-4 rounded-xl border border-white/10 bg-white/[0.02] p-5">
                <span className="font-mono text-sm text-cyan-200/60 tabular-nums">0{index + 1}</span>
                <div>
                  <h3 className="text-sm font-semibold text-white">{title}</h3>
                  <p className="mt-2 text-pretty text-xs leading-6 text-zinc-500">{body}</p>
                </div>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section className="border-t border-white/10 px-5 py-24 lg:px-8">
        <div className="mx-auto max-w-5xl text-center">
          <div className="mx-auto flex size-12 items-center justify-center rounded-xl border border-cyan-300/20 bg-cyan-300/10 text-cyan-200">
            <Radio size={21} />
          </div>
          <h2 className="mt-7 text-balance text-4xl font-semibold text-white md:text-5xl">
            下一次重要评审之前，先让 AI 审计一遍。
          </h2>
          <p className="mx-auto mt-5 max-w-2xl text-pretty text-sm leading-7 text-zinc-500">
            用更低的成本暴露错误假设，用更清晰的证据推动组织共识。
          </p>
          <Link href="/" className="frost-action frost-action-primary mt-9 inline-flex h-12 items-center gap-2 rounded-lg bg-cyan-300 px-7 text-sm font-semibold text-[#031014] hover:bg-cyan-200">
            创建第一次产品推演
            <ArrowRight size={15} />
          </Link>
        </div>
      </section>

      <footer className="border-t border-white/10 px-5 py-8">
        <div className="mx-auto flex max-w-7xl items-center justify-between text-[10px] text-zinc-600">
          <span>PM Brainstorm © 2026</span>
          <span className="hidden items-center gap-2 sm:flex"><Code2 size={12} />Built for product decisions</span>
        </div>
      </footer>
    </main>
  );
}
