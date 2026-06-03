"use client";

import { useState, useEffect } from "react";
import { useSessionStore } from "@/store/sessionStore";
import { api } from "@/lib/api";
import { PROVIDERS } from "@/lib/constants";
import { CloseIcon } from "@/components/icons";

export function SettingsModal() {
  const isOpen = useSessionStore((s) => s.isSettingsOpen);
  const setSettingsOpen = useSessionStore((s) => s.setSettingsOpen);
  const userApiKey = useSessionStore((s) => s.userApiKey);
  const userBaseUrl = useSessionStore((s) => s.userBaseUrl);
  const userModel = useSessionStore((s) => s.userModel);
  const setUserApiKey = useSessionStore((s) => s.setUserApiKey);
  const clearUserApiKey = useSessionStore((s) => s.clearUserApiKey);

  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [providerId, setProviderId] = useState("");
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<"ok" | "fail" | null>(null);
  const [testError, setTestError] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen) {
      setApiKey(userApiKey);
      setBaseUrl(userBaseUrl);
      setModel(userModel);
      const detectedProvider = PROVIDERS.find(p => p.baseUrl === userBaseUrl) || PROVIDERS[0];
      setProviderId(detectedProvider.id);
      setTestResult(null);
      setTestError(null);
    }
  }, [isOpen, userApiKey, userBaseUrl, userModel]);

  useEffect(() => {
    if (!isOpen) return;
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSettingsOpen(false);
    };
    document.addEventListener("keydown", handleEscape);
    return () => document.removeEventListener("keydown", handleEscape);
  }, [isOpen, setSettingsOpen]);

  if (!isOpen) return null;

  const handleSave = () => {
    setUserApiKey(apiKey.trim(), baseUrl.trim(), model.trim());
    setSettingsOpen(false);
  };

  const handleClear = () => {
    clearUserApiKey();
    setApiKey("");
    setBaseUrl("");
    setModel("");
    setTestResult(null);
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

  const handleTest = async () => {
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

  return (
    <>
      <div className="fixed inset-0 bg-warm-600/30 backdrop-blur-sm z-40" onClick={() => setSettingsOpen(false)} aria-hidden="true" />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-title"
        className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[480px] max-w-[90vw] bg-white border border-warm-200 rounded-2xl z-50 shadow-xl"
      >
        <div className="p-6">
          <div className="flex items-center justify-between mb-6">
            <h2 id="settings-title" className="text-lg font-semibold text-warm-600">API 设置</h2>
            <button
              onClick={() => setSettingsOpen(false)}
              aria-label="关闭设置"
              className="text-warm-500 hover:text-warm-600 active:text-warm-700 transition-all duration-200 min-h-[44px] min-w-[44px] flex items-center justify-center focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none rounded"
            >
              <CloseIcon size={16} />
            </button>
          </div>

          <p className="text-sm text-warm-500 mb-4">
            填写你自己的 LLM API Key 后，所有请求将使用你的 Key，不消耗平台额度。支持 OpenAI 兼容接口。
          </p>

          <div className="space-y-4">
            <div>
              <label className="block text-xs text-warm-500 mb-1.5">LLM 提供商</label>
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
              <label className="block text-xs text-warm-500 mb-1.5">API Key</label>
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
              <label className="block text-xs text-warm-500 mb-1.5">Base URL</label>
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
              <label className="block text-xs text-warm-500 mb-1.5">Model（可选）</label>
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
            <div className="mt-3 text-sm text-emerald-600">
              ✓ 连接成功，API Key 有效
            </div>
          )}
          {testResult === "fail" && (
            <div className="mt-3 text-sm text-red-600">
              <div><span aria-hidden="true">✗</span> 连接失败</div>
              {testError && <div className="text-xs text-red-500/70 mt-1">{testError}</div>}
            </div>
          )}

          <div className="flex items-center gap-3 mt-6">
            <button
              onClick={handleTest}
              disabled={!apiKey.trim() || testing}
              aria-label="测试 API 连接"
              className="px-4 py-2 text-sm text-amber-600 hover:text-amber-700 active:text-amber-800 bg-amber-50 rounded-lg transition-all duration-200 disabled:opacity-40 min-h-[44px] focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none"
            >
              {testing ? "测试中..." : "测试连接"}
            </button>
            {userApiKey && (
              <button
                onClick={handleClear}
                aria-label="清除 API 配置"
                className="px-4 py-2 text-sm text-red-600 hover:text-red-700 active:text-red-800 bg-red-50 rounded-lg transition-all duration-200 min-h-[44px] focus-visible:ring-2 focus-visible:ring-red-500/50 focus-visible:outline-none"
              >
                清除配置
              </button>
            )}
            <div className="flex-1" />
            <button
              onClick={() => setSettingsOpen(false)}
              aria-label="取消设置"
              className="px-4 py-2 text-sm text-warm-500 hover:text-warm-600 active:text-warm-700 transition-all duration-200 min-h-[44px] focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none"
            >
              取消
            </button>
            <button
              onClick={handleSave}
              aria-label="保存设置"
              className="px-4 py-2 text-sm bg-amber-500 hover:bg-amber-600 active:bg-amber-700 text-white rounded-lg transition-all duration-200 min-h-[44px] focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none"
            >
              保存
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
