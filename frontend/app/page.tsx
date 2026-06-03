"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { ArrowRightIcon } from "@/components/icons";
import { NavButtons } from "@/components/NavButtons";
import { HistoryDrawer } from "@/components/HistoryDrawer";
import { SettingsModal } from "@/components/SettingsModal";
import { RechargeModal } from "@/components/RechargeModal";
import { OnboardingModal } from "@/components/OnboardingModal";
import { useSessionStore } from "@/store/sessionStore";
import { isLoggedIn as checkIsLoggedIn } from "@/lib/user";

const PROMPT_TEMPLATES = [
  "我想做一个帮助忙碌父母进行 5 分钟家庭健身的 App",
  "优化现有产品的用户留存率，提高日活",
  "设计一个面向 Z 世代的社交学习平台",
  "构建企业级项目管理工具，替代 Jira",
];

const ROLES_DATA = [
  {
    name: "CTO",
    color: "#3b82f6",
    bg: "#eff6ff",
    border: "#bfdbfe",
    desc: "技术可行性",
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <polyline points="16 18 22 12 16 6" /><polyline points="8 6 2 12 8 18" />
      </svg>
    ),
  },
  {
    name: "设计师",
    color: "#a855f7",
    bg: "#faf5ff",
    border: "#e9d5ff",
    desc: "用户体验",
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="13.5" cy="6.5" r="2.5" /><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94" />
        <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19" />
      </svg>
    ),
  },
  {
    name: "运营",
    color: "#22c55e",
    bg: "#f0fdf4",
    border: "#bbf7d0",
    desc: "增长策略",
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
      </svg>
    ),
  },
  {
    name: "用户",
    color: "#f97316",
    bg: "#fff7ed",
    border: "#fed7aa",
    desc: "真实需求",
    icon: (
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" /><circle cx="12" cy="7" r="4" />
      </svg>
    ),
  },
];

export default function LandingPage() {
  const router = useRouter();
  const [problem, setProblem] = useState("");
  const [isCreating, setIsCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isFocused, setIsFocused] = useState(false);
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

  const isByok = !!userApiKey;
  const remaining = tokenQuota - tokensUsed;
  const needsConfig = !isByok && remaining <= 0;
  const showLoginEntry = !storeIsLoggedIn;

  useEffect(() => { setMounted(true); }, []);

  useEffect(() => {
    if (mounted && !storeIsLoggedIn) {
      router.replace("/login");
    }
  }, [mounted, storeIsLoggedIn, router]);

  useEffect(() => {
    if (!hasCompletedOnboarding) {
      const timer = setTimeout(() => setOnboardingOpen(true), 800);
      return () => clearTimeout(timer);
    }
  }, [hasCompletedOnboarding, setOnboardingOpen]);

  const handleCreate = async () => {
    if (!problem.trim()) return;
    setIsCreating(true);
    setError(null);
    try {
      await useSessionStore.getState().createSession(problem.trim());
      const sessionId = useSessionStore.getState().sessionId;
      if (sessionId) {
        router.push(`/session/${sessionId}?problem=${encodeURIComponent(problem.trim())}`);
      }
    } catch (err) {
      setIsCreating(false);
      const msg = err instanceof Error ? err.message : "创建会话失败";
      if (msg.includes("Failed to fetch") || msg.includes("NetworkError") || msg.includes("无法连接")) {
        setError("无法连接到服务器，请检查网络或稍后重试");
      } else {
        setError(msg);
      }
    }
  };

  return (
    <div className="min-h-screen relative overflow-hidden bg-gradient-to-br from-orange-50 via-amber-50 to-rose-50">
      <div className="landing-blobs" />
      <div className="landing-dots" />

      <nav
        className={`fixed top-0 left-0 right-0 h-14 bg-warm-50/80 backdrop-blur border-b border-warm-200 flex items-center justify-between px-6 z-30 transition-all duration-500 ${mounted ? "opacity-100" : "opacity-0"}`}
      >
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-amber-400 via-orange-500 to-rose-500 flex items-center justify-center shadow-md shadow-orange-200">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
              <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
            </svg>
          </div>
          <div>
            <span className="text-sm font-bold text-amber-900 tracking-wide">PM Brainstorm</span>
            <span className="text-xs text-amber-700 ml-2 font-bold">Workbench</span>
          </div>
        </div>
        <div className="flex items-center gap-1">
          {storeIsLoggedIn ? (
            <div className="flex items-center gap-2">
              <span className="text-xs text-amber-800 font-semibold max-w-[80px] truncate">{userNickname || "已登录"}</span>
              <button
                onClick={storeLogout}
                className="text-amber-700 hover:text-amber-900 transition-colors h-9 w-9 flex items-center justify-center rounded-lg hover:bg-amber-50"
                aria-label="退出登录"
              >
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" /><polyline points="16 17 21 12 16 7" /><line x1="21" y1="12" x2="9" y2="12" />
                </svg>
              </button>
            </div>
          ) : showLoginEntry ? (
            <button
              onClick={() => router.push("/login")}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-amber-700 hover:text-amber-900 bg-amber-50 hover:bg-amber-100 border border-amber-200 rounded-lg transition-colors"
              aria-label="登录"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" /><circle cx="12" cy="7" r="4" />
              </svg>
              登录
            </button>
          ) : null}
          <button
            onClick={() => setSettingsOpen(true)}
            className="text-amber-700 hover:text-amber-900 transition-colors h-9 w-9 flex items-center justify-center rounded-lg hover:bg-amber-50"
            aria-label="API 设置"
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" />
              <circle cx="12" cy="12" r="3" />
            </svg>
          </button>
          <NavButtons currentPage="landing" sessionId={null} onToggleHistory={toggleHistory} />
        </div>
      </nav>

      <HistoryDrawer isOpen={isHistoryOpen} onClose={toggleHistory} />
      <SettingsModal />
      <RechargeModal />
      <OnboardingModal />

      {hasCompletedOnboarding && needsConfig && (
        <div className="fixed top-14 left-0 right-0 z-20 bg-amber-50 border-b border-amber-200">
          <div className="max-w-4xl mx-auto px-4 py-2 flex items-center justify-center gap-3">
            <span className="text-xs text-amber-700">额度已用尽，请配置 API Key 或充值</span>
            <button onClick={() => setSettingsOpen(true)} className="text-[11px] font-semibold text-amber-700 hover:text-amber-900 bg-amber-100 hover:bg-amber-200 px-2.5 py-1 rounded-md transition-colors">配置</button>
          </div>
        </div>
      )}

      <div id="main-content" className="flex flex-col items-center justify-center px-5 min-h-screen relative z-10 pt-14">
        <div className="max-w-lg w-full text-center">

          <div className={`mb-8 transition-all duration-500 ${mounted ? "opacity-100 translate-y-0" : "opacity-0 translate-y-5"}`}>
            <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-white border border-amber-200 shadow-sm mb-6">
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
              </span>
              <span className="text-[11px] text-amber-800 font-bold tracking-wide">AI 驱动的产品脑暴工作台</span>
            </div>

            <h1 className="text-4xl md:text-5xl font-extrabold leading-[1.25] tracking-tight mb-3">
              <span className="bg-gradient-to-r from-amber-600 via-orange-500 to-rose-500 bg-clip-text text-transparent">
                PM Brainstorm
              </span>
              <br />
              <span className="text-amber-900 text-xl md:text-2xl font-semibold tracking-wide">Workbench</span>
            </h1>

            <p className="text-amber-900 text-sm md:text-[15px] leading-relaxed max-w-sm mx-auto font-medium">
              四位 AI 专家围绕你的产品想法
              <br />
              进行多维度深度讨论与压力测试
            </p>
          </div>

          <div className={`mb-6 transition-all duration-500 delay-100 ${mounted ? "opacity-100 translate-y-0" : "opacity-0 translate-y-5"}`}>
            <div className="flex justify-center gap-3" role="group" aria-label="AI 专家角色">
              {ROLES_DATA.map((role) => (
                <div key={role.name} className="flex flex-col items-center gap-1.5 group cursor-default">
                  <div
                    className="w-11 h-11 rounded-xl flex items-center justify-center transition-all duration-300 group-hover:scale-110 group-hover:shadow-md"
                    style={{
                      background: role.bg,
                      border: `1.5px solid ${role.border}`,
                      color: role.color,
                    }}
                  >
                    {role.icon}
                  </div>
                  <span className="text-[11px] text-amber-900 font-bold">{role.name}</span>
                </div>
              ))}
            </div>
          </div>

          <div className={`transition-all duration-500 delay-100 ${mounted ? "opacity-100 translate-y-0" : "opacity-0 translate-y-5"}`}>
            <div className="bg-white border border-warm-200 rounded-2xl p-5 shadow-md">
              <label className="block text-xs text-amber-900 mb-3 text-left font-semibold">
                你想探索什么产品方向？
              </label>
              <textarea
                value={problem}
                onChange={(e) => setProblem(e.target.value)}
                onFocus={() => setIsFocused(true)}
                onBlur={() => setTimeout(() => setIsFocused(false), 200)}
                placeholder="例如：我想做一个帮助忙碌父母进行 5 分钟家庭健身的 App..."
                rows={3}
                aria-label="输入你想探索的产品方向"
                className="w-full bg-white border border-amber-200 rounded-xl px-4 py-3 text-gray-900 placeholder-amber-400 resize-none focus:outline-none focus:border-amber-400 focus:ring-2 focus:ring-amber-100 transition-all text-sm leading-relaxed font-medium"
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    handleCreate();
                  }
                }}
              />
              {isFocused && !problem.trim() && (
                <div className="mt-2.5 flex flex-wrap gap-1.5">
                  {PROMPT_TEMPLATES.map((tpl) => (
                    <button
                      key={tpl}
                      onClick={() => setProblem(tpl)}
                      className="text-xs text-amber-800 hover:text-amber-900 bg-amber-50 hover:bg-amber-100 border border-amber-200 rounded-lg px-2.5 py-1.5 transition-colors text-left leading-snug font-medium"
                    >
                      {tpl}
                    </button>
                  ))}
                </div>
              )}
              {error && (
                <div role="alert" className="mt-2.5 px-3 py-2 bg-red-50 border border-red-200 rounded-lg text-red-600 text-xs font-medium">
                  {error}
                </div>
              )}
              <button
                onClick={handleCreate}
                disabled={!problem.trim() || isCreating}
                aria-label="开始脑暴"
                className="mt-4 w-full h-11 bg-amber-600 hover:bg-amber-700 active:bg-amber-800 disabled:bg-warm-300 disabled:text-warm-400 text-white font-semibold rounded-xl transition-colors duration-200 flex items-center justify-center gap-2 text-sm shadow-md disabled:shadow-none"
              >
                {isCreating ? (
                  <>
                    <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                    创建中...
                  </>
                ) : (
                  <>
                    开始脑暴
                    <ArrowRightIcon size={15} />
                  </>
                )}
              </button>
            </div>
          </div>

          <div className={`mt-8 transition-all duration-500 delay-200 ${mounted ? "opacity-100 translate-y-0" : "opacity-0 translate-y-5"}`}>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              {[
                { label: "多角色圆桌", sub: "4位专家讨论", bg: "bg-amber-50", border: "border-amber-200", dot: "bg-amber-400", text: "text-amber-900" },
                { label: "可视化画布", sub: "功能树提取", bg: "bg-purple-50", border: "border-purple-200", dot: "bg-purple-400", text: "text-purple-900" },
                { label: "压力测试", sub: "AI面试官", bg: "bg-emerald-50", border: "border-emerald-200", dot: "bg-emerald-400", text: "text-emerald-900" },
              ].map((f) => (
                <div
                  key={f.label}
                  className={`${f.bg} border ${f.border} rounded-xl px-3 py-3.5 text-center`}
                >
                  <div className={`w-2 h-2 rounded-full ${f.dot} mx-auto mb-2`} />
                  <div className={`text-xs font-bold ${f.text}`}>{f.label}</div>
                  <div className="text-xs text-warm-500 mt-0.5 font-medium">{f.sub}</div>
                </div>
              ))}
            </div>
          </div>

          <div className={`mt-10 text-xs text-warm-500 font-medium transition-all duration-500 delay-200 ${mounted ? "opacity-100" : "opacity-0"}`}>
            Powered by AI · OpenAI Compatible · BYOK Supported
          </div>
        </div>
      </div>
    </div>
  );
}
