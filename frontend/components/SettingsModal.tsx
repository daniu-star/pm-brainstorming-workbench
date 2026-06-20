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
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Settings, Check, AlertCircle, Loader2 } from "lucide-react";

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
    <Dialog open={isOpen} onOpenChange={setSettingsOpen}>
      <DialogContent className="max-w-[480px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Settings className="h-5 w-5" />
            API 设置
          </DialogTitle>
          <DialogDescription>
            填写你自己的 LLM API Key 后，所有请求将使用你的 Key，不消耗平台额度。支持 OpenAI 兼容接口。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label className="text-xs text-muted-foreground">LLM 提供商</Label>
            <select
              value={providerId}
              onChange={(e) => handleProviderChange(e.target.value)}
              aria-label="选择 LLM 提供商"
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            >
              {PROVIDERS.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs text-muted-foreground">API Key</Label>
            <Input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="sk-..."
              aria-label="API Key"
            />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs text-muted-foreground">Base URL</Label>
            <Input
              type="text"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="https://api.openai.com/v1"
              aria-label="Base URL"
            />
          </div>
          <div className="space-y-1.5">
            <Label className="text-xs text-muted-foreground">Model（可选）</Label>
            <Input
              type="text"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              placeholder="gpt-4o"
              aria-label="模型名称"
            />
          </div>
        </div>

        {testResult === "ok" && (
          <div className="flex items-center gap-1.5 text-sm text-emerald-600">
            <Check className="h-4 w-4" />
            连接成功，API Key 有效
          </div>
        )}
        {testResult === "fail" && (
          <div className="text-sm text-destructive">
            <div className="flex items-center gap-1.5">
              <AlertCircle className="h-4 w-4" />
              连接失败
            </div>
            {testError && <div className="text-xs text-destructive/70 mt-1">{testError}</div>}
          </div>
        )}

        <DialogFooter>
          <Button
            onClick={handleTest}
            disabled={!apiKey.trim() || testing}
            variant="outline"
          >
            {testing ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                测试中...
              </>
            ) : (
              "测试连接"
            )}
          </Button>
          {userApiKey && (
            <Button
              onClick={handleClear}
              variant="destructive"
            >
              清除配置
            </Button>
          )}
          <div className="flex-1" />
          <Button
            onClick={() => setSettingsOpen(false)}
            variant="ghost"
          >
            取消
          </Button>
          <Button onClick={handleSave}>
            保存
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
