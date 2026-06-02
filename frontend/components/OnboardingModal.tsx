"use client";

import { useState, useEffect, useRef } from "react";
import { useSessionStore } from "@/store/sessionStore";
import { api } from "@/lib/api";

const PROVIDERS = [
  { id: "", name: "自动检测", baseUrl: "", model: "" },
  { id: "deepseek", name: "DeepSeek", baseUrl: "https://api.deepseek.com/v1", model: "deepseek-v4-flash" },
  { id: "openai", name: "OpenAI", baseUrl: "https://api.openai.com/v1", model: "gpt-4o" },
  { id: "moonshot", name: "Moonshot (月之暗面)", baseUrl: "https://api.moonshot.cn/v1", model: "moonshot-v1-8k" },
  { id: "qwen", name: "通义千问 (Qwen)", baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-turbo" },
  { id: "glm", name: "智谱 (GLM)", baseUrl: "https://open.bigmodel.cn/api/paas/v4", model: "glm-4" },
  { id: "yi", name: "零一万物 (Yi)", baseUrl: "https://api.lingyiwanwu.com/v1", model: "yi-lightning" },
  { id: "doubao", name: "豆包 (Doubao)", baseUrl: "https://ark.cn-beijing.volces.com/api/v3", model: "doubao-pro-4k" },
  { id: "hunyuan", name: "混元 (Hunyuan)", baseUrl: "https://api.hunyuan.cloud.tencent.com/v1", model: "hunyuan-lite" },
  { id: "minimax", name: "MiniMax", baseUrl: "https://api.minimax.chat/v1", model: "minimax-chat" },
  { id: "custom", name: "自定义", baseUrl: "", model: "" },
];

const STEPS = ["welcome", "choose", "setup", "done"] as const;
type Step = (typeof STEPS)[number];

export function OnboardingModal() {
  const isOpen = useSessionStore((s) => s.isOnboardingOpen);
  const completeOnboarding = useSessionStore((s) => s.completeOnboarding);
  const setSettingsOpen = useSessionStore((s) => s.setSettingsOpen);
  const setUserApiKey = useSessionStore((s) => s.setUserApiKey);
  const setOnboardingOpen = useSessionStore((s) => s.setOnboardingOpen);

  const [step, setStep] = useState<Step>("welcome");
  const [mode, setMode] = useState<"byok" | "platform" | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [providerId, setProviderId] = useState("");
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<"ok" | "fail" | null>(null);
  const [testError, setTestError] = useState<string | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (isOpen) {
      setStep("welcome");
      setMode(null);
      setApiKey("");
      setBaseUrl("");
      setModel("");
      setProviderId("");
      setTestResult(null);
      setTestError(null);
    }
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) return;
    const prev = document.activeElement as HTMLElement | null;
    dialogRef.current?.focus();
    return () => prev?.focus?.();
  }, [isOpen, step]);

  if (!isOpen) return null;

  const stepIndex = STEPS.indexOf(step);

  const handleChooseByok = () => {
    setMode("byok");
    setStep("setup");
  };

  const handleProviderChange = (id: string) => {
    setProviderId(id);
    const provider = PROVIDERS.find(p => p.id === id);
    if (provider && provider.baseUrl) {
      setBaseUrl(provider.baseUrl);
      setModel(provider.model);
    } else {
      setBaseUrl("");
      if (!id) {
        setModel("");
      }
    }
  };

  const handleChoosePlatform = () => {
    setMode("platform");
    setStep("done");
  };

  const handleTestKey = async () => {
    if (!apiKey.trim()) return;
    setTesting(true);
    setTestResult(null);
    setTestError(null);
    try {
      const result = await api<{ status: string; provider?: string; base_url?: string; model?: string }>("/api/user/test-key", {
        method: "POST",
        body: JSON.stringify({ api_key: apiKey.trim(), base_url: baseUrl.trim(), model: model.trim() }),
      });
      setTestResult("ok");
      if (result.base_url) {
        setBaseUrl(result.base_url);
      }
      if (result.model) {
        setModel(result.model);
      }
    } catch (err) {
      setTestResult("fail");
      setTestError(err instanceof Error ? err.message : "连接失败");
    } finally {
      setTesting(false);
    }
  };

  const handleSaveKey = () => {
    if (apiKey.trim()) {
      setUserApiKey(apiKey.trim(), baseUrl.trim(), model.trim());
    }
    setStep("done");
  };

  const handleSkip = () => {
    setStep("done");
  };

  const handleFinish = () => {
    completeOnboarding();
  };

  const handleOpenSettings = () => {
    completeOnboarding();
    setSettingsOpen(true);
  };

  return (
    <>
      <div className="fixed inset-0 bg-warm-600/30 backdrop-blur-sm z-40" aria-hidden="true" />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="onboarding-title"
        tabIndex={-1}
        className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[560px] max-w-[92vw] bg-white border border-warm-200 rounded-2xl z-50 shadow-xl outline-none"
      >
        <div className="p-8">
          <div className="flex items-center gap-2 mb-8">
            {STEPS.map((s, i) => (
              <div
                key={s}
                className={`h-1 flex-1 rounded-full transition-all duration-300 ${
                  i <= stepIndex ? "bg-amber-500" : "bg-warm-200"
                }`}
              />
            ))}
          </div>

          {step === "welcome" && (
            <div className="text-center">
              <div className="w-16 h-16 mx-auto mb-6 rounded-2xl bg-gradient-to-br from-amber-500 to-orange-500 flex items-center justify-center">
                <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M9.5 2A2.5 2.5 0 0 1 12 4.5V18a2 2 0 0 0 2 2h2a2 2 0 0 0 2-2V6.5A2.5 2.5 0 0 1 20.5 4a2.5 2.5 0 0 1 0 5H18M4 10.5V11a3 3 0 0 0 3 3h.5a.5.5 0 0 1 .5.5V16a2 2 0 0 0 2 2h.5M4 10.5A2.5 2.5 0 0 1 6.5 8a2.5 2.5 0 0 1 2.5 2.5V11M4 10.5a2.5 2.5 0 0 0 0 5M16 12a2 2 0 1 0 0-4 2 2 0 0 0 0 4Z" />
                </svg>
              </div>
              <h2 id="onboarding-title" className="text-2xl font-bold text-warm-600 mb-3">
                欢迎使用产品脑暴工作台
              </h2>
              <p className="text-warm-500 text-sm leading-relaxed mb-2 max-w-md mx-auto">
                四位 AI 专家将围绕你的产品想法展开多维度深度讨论，
                AI 面试官帮你压力测试，可视化功能树梳理思路。
              </p>
              <p className="text-amber-700 text-sm leading-relaxed mb-8 max-w-md mx-auto">
                使用本产品需要配置 LLM API。你可以使用自己的 Key，也可以使用平台额度。
              </p>
              <button
                onClick={() => setStep("choose")}
                aria-label="开始配置"
                className="h-12 px-8 bg-gradient-to-r from-amber-500 to-orange-500 hover:from-amber-400 hover:to-orange-400 active:from-amber-600 active:to-orange-600 text-white font-semibold rounded-xl transition-all duration-200 focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none focus-visible:ring-offset-2 focus-visible:ring-offset-white"
              >
                开始配置
              </button>
            </div>
          )}

          {step === "choose" && (
            <div>
              <h2 id="onboarding-title" className="text-xl font-bold text-warm-600 mb-2 text-center">
                选择使用方式
              </h2>
              <p className="text-warm-500 text-sm mb-6 text-center">
                你可以自带 API Key，或使用平台提供的共享额度
              </p>

              <div className="grid grid-cols-2 gap-4 mb-6">
                <button
                  onClick={handleChooseByok}
                  aria-label="选择自带 API Key"
                  className="group p-5 bg-warm-50 border border-warm-200 rounded-xl text-left hover:border-amber-300 hover:bg-amber-50 active:scale-[0.98] transition-all duration-200 focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none"
                >
                  <div className="w-10 h-10 rounded-lg bg-emerald-50 border border-emerald-200 flex items-center justify-center mb-3">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#22c55e" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4" />
                    </svg>
                  </div>
                  <div className="text-sm font-semibold text-warm-600 mb-1">自带 API Key</div>
                  <div className="text-xs text-warm-500 leading-relaxed">
                    填写你自己的 OpenAI 兼容 Key，所有请求走你的额度，不消耗平台资源
                  </div>
                  <div className="mt-3 inline-flex items-center gap-1 text-xs text-emerald-600">
                    <span className="w-1.5 h-1.5 bg-emerald-500 rounded-full" />
                    无限使用
                  </div>
                </button>

                <button
                  onClick={handleChoosePlatform}
                  aria-label="选择使用平台额度"
                  className="group p-5 bg-warm-50 border border-warm-200 rounded-xl text-left hover:border-orange-300 hover:bg-orange-50 active:scale-[0.98] transition-all duration-200 focus-visible:ring-2 focus-visible:ring-orange-500/50 focus-visible:outline-none"
                >
                  <div className="w-10 h-10 rounded-lg bg-orange-50 border border-orange-200 flex items-center justify-center mb-3">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#f97316" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" />
                    </svg>
                  </div>
                  <div className="text-sm font-semibold text-warm-600 mb-1">使用平台额度</div>
                  <div className="text-xs text-warm-500 leading-relaxed">
                    新用户赠送 10 万 tokens 免费额度，用完可充值继续使用
                  </div>
                  <div className="mt-3 inline-flex items-center gap-1 text-xs text-orange-600">
                    <span className="w-1.5 h-1.5 bg-orange-500 rounded-full" />
                    赠送 10 万 tokens
                  </div>
                </button>
              </div>

              <div className="text-center">
                <button
                  onClick={handleSkip}
                  aria-label="稍后配置"
                  className="text-xs text-warm-500 hover:text-warm-600 active:text-warm-700 transition-all duration-200 min-h-[44px] focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none"
                >
                  稍后配置，先看看
                </button>
              </div>
            </div>
          )}

          {step === "setup" && mode === "byok" && (
            <div>
              <h2 id="onboarding-title" className="text-xl font-bold text-warm-600 mb-2 text-center">
                配置你的 API Key
              </h2>
              <p className="text-warm-500 text-sm mb-6 text-center">
                支持 OpenAI 及所有兼容接口（如 DeepSeek、Moonshot、通义千问等）
              </p>

              <div className="space-y-4 mb-4">
                <div>
                  <label className="block text-xs text-warm-500 mb-1.5 font-medium">LLM 提供商</label>
                  <select
                    value={providerId}
                    onChange={(e) => handleProviderChange(e.target.value)}
                    aria-label="选择 LLM 提供商"
                    className="w-full bg-warm-50 border border-warm-200 rounded-lg px-3 py-2.5 text-sm text-warm-600 focus:outline-none focus:border-amber-300 focus:ring-2 focus:ring-amber-500/30 transition-all"
                  >
                    {PROVIDERS.map((p) => (
                      <option key={p.id} value={p.id}>{p.name}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-xs text-warm-500 mb-1.5 font-medium">API Key <span className="text-red-600">*</span></label>
                  <input
                    type="password"
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    placeholder="sk-..."
                    aria-label="API Key"
                    className="w-full bg-warm-50 border border-warm-200 rounded-lg px-3 py-2.5 text-sm text-warm-600 placeholder-warm-400 focus:outline-none focus:border-amber-300 focus:ring-2 focus:ring-amber-500/30 transition-all"
                  />
                </div>
                <div>
                  <label className="block text-xs text-warm-500 mb-1.5 font-medium">Base URL</label>
                  <input
                    type="text"
                    value={baseUrl}
                    onChange={(e) => setBaseUrl(e.target.value)}
                    placeholder="https://api.openai.com/v1"
                    aria-label="Base URL"
                    className="w-full bg-warm-50 border border-warm-200 rounded-lg px-3 py-2.5 text-sm text-warm-600 placeholder-warm-400 focus:outline-none focus:border-amber-300 focus:ring-2 focus:ring-amber-500/30 transition-all"
                  />
                </div>
                <div>
                  <label className="block text-xs text-warm-500 mb-1.5 font-medium">Model（可选）</label>
                  <input
                    type="text"
                    value={model}
                    onChange={(e) => setModel(e.target.value)}
                    placeholder="gpt-4o"
                    aria-label="模型名称"
                    className="w-full bg-warm-50 border border-warm-200 rounded-lg px-3 py-2.5 text-sm text-warm-600 placeholder-warm-400 focus:outline-none focus:border-amber-300 focus:ring-2 focus:ring-amber-500/30 transition-all"
                  />
                </div>
              </div>

              {testResult === "ok" && (
                <div className="mb-4 text-sm text-emerald-600">
                  连接成功，API Key 有效
                </div>
              )}
              {testResult === "fail" && (
                <div className="mb-4 text-sm text-red-600">
                  <div>连接失败</div>
                  {testError && <div className="text-xs text-red-500/70 mt-1">{testError}</div>}
                </div>
              )}

              <div className="flex items-center gap-3">
                <button
                  onClick={handleTestKey}
                  disabled={!apiKey.trim() || testing}
                  aria-label="测试 API 连接"
                  className="px-4 py-2 text-sm text-amber-600 hover:text-amber-700 active:text-amber-800 bg-amber-50 rounded-lg transition-all duration-200 disabled:opacity-40 min-h-[44px] focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none"
                >
                  {testing ? "测试中..." : "测试连接"}
                </button>
                <div className="flex-1" />
                <button
                  onClick={() => setStep("choose")}
                  aria-label="返回上一步"
                  className="px-4 py-2 text-sm text-warm-500 hover:text-warm-600 active:text-warm-700 transition-all duration-200 min-h-[44px] focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none"
                >
                  返回
                </button>
                <button
                  onClick={handleSaveKey}
                  aria-label="保存并继续"
                  className="px-5 py-2 text-sm bg-amber-500 hover:bg-amber-600 active:bg-amber-700 text-white rounded-lg transition-all duration-200 min-h-[44px] focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none"
                >
                  保存并继续
                </button>
              </div>
            </div>
          )}

          {step === "done" && (
            <div className="text-center">
              <div className="w-16 h-16 mx-auto mb-6 rounded-2xl bg-gradient-to-br from-emerald-500 to-teal-500 flex items-center justify-center">
                <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M20 6L9 17l-5-5" />
                </svg>
              </div>
              <h2 id="onboarding-title" className="text-xl font-bold text-warm-600 mb-3">
                {mode === "byok"
                  ? "API Key 配置完成"
                  : mode === "platform"
                    ? "平台额度已激活"
                    : "准备就绪"}
              </h2>
              <p className="text-warm-500 text-sm leading-relaxed mb-8 max-w-sm mx-auto">
                {mode === "byok"
                  ? "你的 API Key 已保存，所有请求将使用你的 Key。随时可在设置中修改。"
                  : mode === "platform"
                    ? "你已获得 10 万 tokens 免费额度。额度不足时可充值，也可随时切换为自带 Key。"
                    : "你可以稍后在设置中配置 API Key 或充值额度。未配置时部分功能可能受限。"}
              </p>
              <div className="flex items-center justify-center gap-3">
                {mode !== "byok" && (
                  <button
                    onClick={handleOpenSettings}
                    aria-label="配置 API Key"
                    className="h-11 px-5 text-sm text-amber-600 hover:text-amber-700 active:text-amber-800 bg-amber-50 border border-amber-200 rounded-xl transition-all duration-200 focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none"
                  >
                    配置 API Key
                  </button>
                )}
                <button
                  onClick={handleFinish}
                  aria-label="开始脑暴"
                  className="h-11 px-8 bg-gradient-to-r from-amber-500 to-orange-500 hover:from-amber-400 hover:to-orange-400 active:from-amber-600 active:to-orange-600 text-white font-semibold rounded-xl transition-all duration-200 focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none"
                >
                  开始脑暴
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
