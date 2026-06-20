"use client";

import { useState, useEffect } from "react";
import { useSessionStore } from "@/store/sessionStore";
import { api } from "@/lib/api";
import { PROVIDERS } from "@/lib/constants";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Brain, Key, Coins, Check, ArrowLeft } from "lucide-react";

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

  const stepIndex = STEPS.indexOf(step);

  const handleChooseByok = () => {
    setMode("byok");
    setStep("setup");
  };

  const handleProviderChange = (id: string) => {
    setProviderId(id);
    const provider = PROVIDERS.find((p) => p.id === id);
    if (provider && provider.baseUrl) {
      setBaseUrl(provider.baseUrl);
      setModel(provider.model);
    } else {
      setBaseUrl("");
      if (!id) setModel("");
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
      if (result.base_url) setBaseUrl(result.base_url);
      if (result.model) setModel(result.model);
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

  const handleSkip = () => setStep("done");
  const handleFinish = () => completeOnboarding();
  const handleOpenSettings = () => {
    completeOnboarding();
    setSettingsOpen(true);
  };

  return (
    <Dialog open={isOpen} onOpenChange={setOnboardingOpen}>
      <DialogContent className="max-w-[560px]">
        <div className="flex items-center gap-2 mb-4">
          {STEPS.map((s, i) => (
            <div
              key={s}
              className={`h-1 flex-1 rounded-full transition-all duration-300 ${
                i <= stepIndex ? "bg-primary" : "bg-muted"
              }`}
            />
          ))}
        </div>

        {step === "welcome" && (
          <div className="text-center">
            <div className="w-16 h-16 mx-auto mb-6 rounded-2xl bg-gradient-to-br from-amber-500 to-orange-500 flex items-center justify-center">
              <Brain className="h-8 w-8 text-white" />
            </div>
            <DialogTitle className="text-2xl font-bold mb-3">
              欢迎使用产品脑暴工作台
            </DialogTitle>
            <DialogDescription className="text-sm leading-relaxed mb-2 max-w-md mx-auto">
              四位 AI 专家将围绕你的产品想法展开多维度深度讨论，
              AI 面试官帮你压力测试，可视化功能树梳理思路。
            </DialogDescription>
            <p className="text-primary text-sm leading-relaxed mb-8 max-w-md mx-auto">
              使用本产品需要配置 LLM API。你可以使用自己的 Key，也可以使用平台额度。
            </p>
            <Button variant="gradient" size="lg" onClick={() => setStep("choose")}>
              开始配置
            </Button>
          </div>
        )}

        {step === "choose" && (
          <div>
            <DialogHeader>
              <DialogTitle className="text-center">选择使用方式</DialogTitle>
              <DialogDescription className="text-center">
                你可以自带 API Key，或使用平台提供的共享额度
              </DialogDescription>
            </DialogHeader>
            <div className="grid grid-cols-2 gap-4 mt-6 mb-6">
              <button
                onClick={handleChooseByok}
                className="group p-5 bg-card border border-border rounded-xl text-left hover:border-primary/50 hover:bg-accent transition-all"
              >
                <div className="w-10 h-10 rounded-lg bg-emerald-50 border border-emerald-200 flex items-center justify-center mb-3">
                  <Key className="h-5 w-5 text-emerald-600" />
                </div>
                <div className="text-sm font-semibold mb-1">自带 API Key</div>
                <div className="text-xs text-muted-foreground leading-relaxed">
                  填写你自己的 OpenAI 兼容 Key，所有请求走你的额度
                </div>
                <div className="mt-3 inline-flex items-center gap-1 text-xs text-emerald-600">
                  <span className="w-1.5 h-1.5 bg-emerald-500 rounded-full" />
                  无限使用
                </div>
              </button>

              <button
                onClick={handleChoosePlatform}
                className="group p-5 bg-card border border-border rounded-xl text-left hover:border-orange-300 hover:bg-orange-50 transition-all"
              >
                <div className="w-10 h-10 rounded-lg bg-orange-50 border border-orange-200 flex items-center justify-center mb-3">
                  <Coins className="h-5 w-5 text-orange-600" />
                </div>
                <div className="text-sm font-semibold mb-1">使用平台额度</div>
                <div className="text-xs text-muted-foreground leading-relaxed">
                  新用户赠送 10 万 tokens 免费额度
                </div>
                <div className="mt-3 inline-flex items-center gap-1 text-xs text-orange-600">
                  <span className="w-1.5 h-1.5 bg-orange-500 rounded-full" />
                  赠送 10 万 tokens
                </div>
              </button>
            </div>
            <div className="text-center">
              <Button variant="ghost" size="sm" onClick={handleSkip}>
                稍后配置，先看看
              </Button>
            </div>
          </div>
        )}

        {step === "setup" && mode === "byok" && (
          <div>
            <DialogHeader>
              <DialogTitle className="text-center">配置你的 API Key</DialogTitle>
              <DialogDescription className="text-center">
                支持 OpenAI 及所有兼容接口（如 DeepSeek、Moonshot、通义千问等）
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4 mt-6 mb-4">
              <div>
                <Label className="text-xs mb-1.5">LLM 提供商</Label>
                <select
                  value={providerId}
                  onChange={(e) => handleProviderChange(e.target.value)}
                  className="w-full h-10 bg-background border border-input rounded-md px-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                >
                  {PROVIDERS.map((p) => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))}
                </select>
              </div>
              <div>
                <Label className="text-xs mb-1.5">API Key <span className="text-destructive">*</span></Label>
                <Input
                  type="password"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder="sk-..."
                />
              </div>
              <div>
                <Label className="text-xs mb-1.5">Base URL</Label>
                <Input
                  type="text"
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                  placeholder="https://api.openai.com/v1"
                />
              </div>
              <div>
                <Label className="text-xs mb-1.5">Model（可选）</Label>
                <Input
                  type="text"
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  placeholder="gpt-4o"
                />
              </div>
            </div>

            {testResult === "ok" && (
              <div className="mb-4 text-sm text-emerald-600 flex items-center gap-1">
                <Check className="h-4 w-4" /> 连接成功，API Key 有效
              </div>
            )}
            {testResult === "fail" && (
              <div className="mb-4 text-sm text-destructive">
                <div>连接失败</div>
                {testError && <div className="text-xs opacity-70 mt-1">{testError}</div>}
              </div>
            )}

            <div className="flex items-center gap-3">
              <Button variant="outline" onClick={handleTestKey} disabled={!apiKey.trim() || testing}>
                {testing ? "测试中..." : "测试连接"}
              </Button>
              <div className="flex-1" />
              <Button variant="ghost" onClick={() => setStep("choose")}>
                <ArrowLeft className="h-4 w-4 mr-1" /> 返回
              </Button>
              <Button onClick={handleSaveKey}>保存并继续</Button>
            </div>
          </div>
        )}

        {step === "done" && (
          <div className="text-center">
            <div className="w-16 h-16 mx-auto mb-6 rounded-2xl bg-gradient-to-br from-emerald-500 to-teal-500 flex items-center justify-center">
              <Check className="h-8 w-8 text-white" />
            </div>
            <DialogTitle className="text-xl font-bold mb-3">
              {mode === "byok"
                ? "API Key 配置完成"
                : mode === "platform"
                  ? "平台额度已激活"
                  : "准备就绪"}
            </DialogTitle>
            <DialogDescription className="text-sm leading-relaxed mb-8 max-w-sm mx-auto">
              {mode === "byok"
                ? "你的 API Key 已保存，所有请求将使用你的 Key。随时可在设置中修改。"
                : mode === "platform"
                  ? "你已获得 10 万 tokens 免费额度。额度不足时可充值。"
                  : "你可以稍后在设置中配置 API Key 或充值额度。"}
            </DialogDescription>
            <div className="flex items-center justify-center gap-3">
              {mode !== "byok" && (
                <Button variant="outline" onClick={handleOpenSettings}>
                  配置 API Key
                </Button>
              )}
              <Button variant="gradient" size="lg" onClick={handleFinish}>
                开始脑暴
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
