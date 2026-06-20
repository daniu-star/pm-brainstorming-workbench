"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowRight,
  Settings,
  LogOut,
  User,
  Code,
  Palette,
  TrendingUp,
  Brain,
} from "lucide-react";
import { NavButtons } from "@/components/NavButtons";
import { HistoryDrawer } from "@/components/HistoryDrawer";
import { SettingsModal } from "@/components/SettingsModal";
import { RechargeModal } from "@/components/RechargeModal";
import { OnboardingModal } from "@/components/OnboardingModal";
import { useSessionStore } from "@/store/sessionStore";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

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
    icon: <Code size={20} />,
  },
  {
    name: "设计师",
    color: "#a855f7",
    bg: "#faf5ff",
    border: "#e9d5ff",
    desc: "用户体验",
    icon: <Palette size={20} />,
  },
  {
    name: "运营",
    color: "#22c55e",
    bg: "#f0fdf4",
    border: "#bbf7d0",
    desc: "增长策略",
    icon: <TrendingUp size={20} />,
  },
  {
    name: "用户",
    color: "#f97316",
    bg: "#fff7ed",
    border: "#fed7aa",
    desc: "真实需求",
    icon: <User size={20} />,
  },
];

const FEATURES = [
  { label: "多角色圆桌", sub: "4位专家讨论", dot: "bg-amber-400" },
  { label: "可视化画布", sub: "功能树提取", dot: "bg-purple-400" },
  { label: "压力测试", sub: "AI面试官", dot: "bg-emerald-400" },
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
    <div className="min-h-screen relative overflow-hidden bg-background">
      <div className="landing-blobs" />
      <div className="landing-dots" />

      <nav
        className={`fixed top-0 left-0 right-0 h-14 bg-background/80 backdrop-blur border-b border-border flex items-center justify-between px-6 z-30 transition-all duration-500 ${mounted ? "opacity-100" : "opacity-0"}`}
      >
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-primary flex items-center justify-center shadow-md">
            <Brain className="h-[18px] w-[18px] text-primary-foreground" strokeWidth={2.5} />
          </div>
          <div>
            <span className="text-sm font-bold text-foreground tracking-wide">PM Brainstorm</span>
            <span className="text-xs text-primary ml-2 font-bold">Workbench</span>
          </div>
        </div>
        <div className="flex items-center gap-1">
          {storeIsLoggedIn ? (
            <div className="flex items-center gap-2">
              <span className="text-xs text-primary font-semibold max-w-[80px] truncate">{userNickname || "已登录"}</span>
              <Button
                onClick={storeLogout}
                variant="ghost"
                size="icon"
                className="h-9 w-9 text-primary hover:text-foreground"
                aria-label="退出登录"
              >
                <LogOut className="h-[15px] w-[15px]" />
              </Button>
            </div>
          ) : showLoginEntry ? (
            <Button
              onClick={() => router.push("/login")}
              variant="outline"
              size="sm"
              className="gap-1.5 text-primary"
              aria-label="登录"
            >
              <User className="h-[14px] w-[14px]" />
              登录
            </Button>
          ) : null}
          <Button
            onClick={() => setSettingsOpen(true)}
            variant="ghost"
            size="icon"
            className="h-9 w-9 text-primary hover:text-foreground"
            aria-label="API 设置"
          >
            <Settings className="h-[15px] w-[15px]" />
          </Button>
          <NavButtons currentPage="landing" sessionId={null} onToggleHistory={toggleHistory} />
        </div>
      </nav>

      <HistoryDrawer isOpen={isHistoryOpen} onClose={toggleHistory} />
      <SettingsModal />
      <RechargeModal />
      <OnboardingModal />

      {hasCompletedOnboarding && needsConfig && (
        <div className="fixed top-14 left-0 right-0 z-20 bg-primary/10 border-b border-border">
          <div className="max-w-4xl mx-auto px-4 py-2 flex items-center justify-center gap-3">
            <span className="text-xs text-primary">额度已用尽，请配置 API Key 或充值</span>
            <Button onClick={() => setSettingsOpen(true)} variant="outline" size="sm" className="text-xs h-7 px-2.5">
              配置
            </Button>
          </div>
        </div>
      )}

      <div id="main-content" className="flex flex-col items-center justify-center px-5 min-h-screen relative z-10 pt-14">
        <div className="max-w-lg w-full text-center">

          <div className={`mb-8 transition-all duration-500 ${mounted ? "opacity-100 translate-y-0" : "opacity-0 translate-y-5"}`}>
            <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-card border border-border shadow-sm mb-6">
              <span className="relative flex h-2 w-2">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500" />
              </span>
              <span className="text-[11px] text-primary font-bold tracking-wide">AI 驱动的产品脑暴工作台</span>
            </div>

            <h1 className="text-4xl md:text-5xl font-extrabold leading-[1.25] tracking-tight mb-3">
              <span className="text-primary">
                PM Brainstorm
              </span>
              <br />
              <span className="text-foreground text-xl md:text-2xl font-semibold tracking-wide">Workbench</span>
            </h1>

            <p className="text-foreground text-sm md:text-[15px] leading-relaxed max-w-sm mx-auto font-medium">
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
                  <span className="text-[11px] text-foreground font-bold">{role.name}</span>
                </div>
              ))}
            </div>
          </div>

          <div className={`transition-all duration-500 delay-100 ${mounted ? "opacity-100 translate-y-0" : "opacity-0 translate-y-5"}`}>
            <Card className="border-border p-5 rounded-2xl shadow-md">
              <label className="block text-xs text-foreground mb-3 text-left font-semibold">
                你想探索什么产品方向？
              </label>
              <Textarea
                value={problem}
                onChange={(e) => setProblem(e.target.value)}
                placeholder="例如：我想做一个帮助忙碌父母进行 5 分钟家庭健身的 App..."
                rows={3}
                aria-label="输入你想探索的产品方向"
                className="resize-none text-sm leading-relaxed font-medium"
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    handleCreate();
                  }
                }}
              />
              {!problem.trim() && (
                <div className="mt-2.5 flex flex-wrap gap-1.5">
                  {PROMPT_TEMPLATES.map((tpl) => (
                    <Button
                      key={tpl}
                      variant="outline"
                      size="sm"
                      onMouseDown={() => setProblem(tpl)}
                      className="text-xs h-auto py-1.5 px-2.5 leading-snug text-left whitespace-normal"
                    >
                      {tpl}
                    </Button>
                  ))}
                </div>
              )}
              {error && (
                <div role="alert" className="mt-2.5 px-3 py-2 bg-red-50 border border-red-200 rounded-lg text-red-600 text-xs font-medium">
                  {error}
                </div>
              )}
              <Button
                onClick={handleCreate}
                disabled={!problem.trim() || isCreating}
                size="lg"
                aria-label="开始脑暴"
                className="mt-4 w-full h-11 font-semibold rounded-xl shadow-md disabled:shadow-none"
              >
                {isCreating ? (
                  <>
                    <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                    创建中...
                  </>
                ) : (
                  <>
                    开始脑暴
                    <ArrowRight size={15} />
                  </>
                )}
              </Button>
            </Card>
          </div>

          <div className={`mt-8 transition-all duration-500 delay-200 ${mounted ? "opacity-100 translate-y-0" : "opacity-0 translate-y-5"}`}>
            <div className="flex flex-wrap justify-center gap-2">
              {FEATURES.map((f) => (
                <Badge key={f.label} variant="secondary" className="gap-1.5 px-3 py-1.5 text-xs">
                  <span className={`w-2 h-2 rounded-full ${f.dot}`} />
                  <span className="font-bold">{f.label}</span>
                  <span className="text-muted-foreground font-normal">{f.sub}</span>
                </Badge>
              ))}
            </div>
          </div>

          <div className={`mt-10 text-xs text-muted-foreground font-medium transition-all duration-500 delay-200 ${mounted ? "opacity-100" : "opacity-0"}`}>
            Powered by AI · OpenAI Compatible · BYOK Supported
          </div>
        </div>
      </div>
    </div>
  );
}
