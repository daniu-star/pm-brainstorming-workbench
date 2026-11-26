"use client";

import { useEffect, useState } from "react";
import { toast } from "@/components/Toast";
import { errorMessage, isTransientFailure } from "@/lib/api";
import {
  deleteLlmConfig,
  getLlmConfig,
  saveLlmConfig,
  testLlmConfig,
  type LlmConfigView,
} from "@/lib/llmConfig";

const INPUT_CLASS =
  "w-full rounded-lg border border-zinc-700/70 bg-dark-900/70 px-3 py-2 text-sm text-zinc-100 outline-none transition-colors placeholder:text-zinc-600 focus:border-brand-300/70 focus:ring-2 focus:ring-brand-300/20";

interface LlmConfigFormProps {
  /** 保存 / 清除成功后回调，调用方可以据此关闭强制弹窗。 */
  onSaved?: (view: LlmConfigView) => void;
}

export function LlmConfigForm({ onSaved }: LlmConfigFormProps) {
  const [view, setView] = useState<LlmConfigView | null>(null);
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [disableReasoning, setDisableReasoning] = useState(true);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  // 读取次数：手动重试与「瞬时故障自动重试」都靠它触发下一次 effect。
  const [loadAttempt, setLoadAttempt] = useState(0);

  const retryLoad = (): void => {
    setLoadError(null);
    setBusy(true);
    setLoadAttempt((n) => n + 1);
  };

  useEffect(() => {
    let cancelled = false;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;

    getLlmConfig()
      .then((next) => {
        if (cancelled) return;
        setView(next);
        setBaseUrl(next.base_url || next.defaults.base_url);
        setModel(next.model || "");
        setDisableReasoning(next.disable_reasoning);
        setLoadError(null);
        setBusy(false);
      })
      .catch((err) => {
        if (cancelled) return;
        // 后端唤醒中 / 网关抖动：静默再试两次（2s、4s），都失败才把错误摆到用户眼前。
        if (loadAttempt < 2 && isTransientFailure(err)) {
          setStatus("后端可能正在唤醒，正在自动重试读取配置…");
          retryTimer = setTimeout(() => {
            if (!cancelled) setLoadAttempt((n) => n + 1);
          }, loadAttempt === 0 ? 2_000 : 4_000);
          return;
        }
        setStatus(null);
        setBusy(false);
        setLoadError(errorMessage(err));
      });

    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
    };
  }, [loadAttempt]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (busy) return;
    const trimmedKey = apiKey.trim();
    if (trimmedKey.length < 8) {
      setStatus("请粘贴完整的 API Key（至少 8 个字符）");
      return;
    }
    setBusy(true);
    setStatus("正在保存…");
    try {
      const saved = await saveLlmConfig({
        base_url: baseUrl.trim(),
        model: model.trim(),
        api_key: trimmedKey,
        disable_reasoning: disableReasoning,
      });
      setView(saved);
      setApiKey("");
      onSaved?.(saved);
      setStatus("已保存，正在用一次极小请求测试连通性…");
      const result = await testLlmConfig();
      setView(result.view);
      if (result.ok) {
        setStatus(
          `连通性正常：${result.model || saved.model} 回复「${result.reply || "ok"}」，耗时 ${result.elapsed_ms} ms`,
        );
        toast.success("模型配置已生效");
      } else {
        setStatus(`保存成功，但测试失败：${result.error || "未知错误"}`);
        toast.error("模型接口测试失败");
      }
    } catch (err) {
      setStatus(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const clear = async () => {
    if (busy) return;
    if (!window.confirm("清除后需要重新填写才能继续使用，确定吗？")) return;
    setBusy(true);
    try {
      const next = await deleteLlmConfig();
      setView(next);
      setApiKey("");
      setStatus("已清除模型配置");
      onSaved?.(next);
    } catch (err) {
      setStatus(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  if (loadError) {
    return (
      <div className="flex flex-col gap-3 rounded-lg border border-red-800/40 bg-red-900/20 p-3 text-sm text-red-300">
        <p role="alert">读取模型配置失败：{loadError}</p>
        <p className="text-xs text-red-200/80">
          线上后端（Hugging Face Space）唤醒时偶发连接抖动，点击下面的按钮重试通常即可恢复。
        </p>
        <button
          type="button"
          onClick={retryLoad}
          disabled={busy}
          className="min-h-[44px] w-fit rounded-lg border border-zinc-600/70 px-4 py-2 text-sm text-zinc-200 transition-colors hover:border-brand-400/70 hover:text-brand-200 disabled:opacity-60"
        >
          {busy ? "正在重试…" : "重新读取配置"}
        </button>
      </div>
    );
  }

  const configured = Boolean(view?.configured);

  return (
    <form onSubmit={submit} className="flex flex-col gap-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="flex flex-col gap-1.5 text-xs text-zinc-400">
          接口地址（base_url）
          <input
            aria-label="模型接口地址"
            className={INPUT_CLASS}
            value={baseUrl}
            onChange={(event) => setBaseUrl(event.target.value)}
            placeholder={view?.defaults.base_url ?? "https://api.deepseek.com/v1"}
            autoComplete="off"
            spellCheck={false}
          />
        </label>
        <label className="flex flex-col gap-1.5 text-xs text-zinc-400">
          模型名称（model）
          <input
            aria-label="模型名称"
            className={INPUT_CLASS}
            value={model}
            onChange={(event) => setModel(event.target.value)}
            placeholder={view?.defaults.model ?? "deepseek-flash"}
            autoComplete="off"
            spellCheck={false}
          />
        </label>
      </div>

      <label className="flex flex-col gap-1.5 text-xs text-zinc-400">
        API Key（只存密文，之后只显示末 4 位）
        <input
          aria-label="模型 API Key"
          type="password"
          className={INPUT_CLASS}
          value={apiKey}
          onChange={(event) => setApiKey(event.target.value)}
          placeholder={configured ? `已保存 ${view?.key_hint ?? ""}，如需更换请重新粘贴` : "sk-…"}
          autoComplete="new-password"
          spellCheck={false}
        />
      </label>

      <label className="flex items-center gap-2 text-xs text-zinc-400">
        <input
          aria-label="关闭推理模型的思考过程"
          type="checkbox"
          className="h-4 w-4 rounded border-zinc-600 bg-dark-900"
          checked={disableReasoning}
          onChange={(event) => setDisableReasoning(event.target.checked)}
        />
        关闭推理型模型的思考过程（DeepSeek 等推理模型建议勾选，否则结构化输出可能被思考内容挤断）
      </label>

      <div className="flex flex-wrap items-center gap-2">
        <button
          type="submit"
          disabled={busy}
          className="min-h-[44px] rounded-lg bg-brand-300/90 px-4 py-2 text-sm font-medium text-dark-900 transition-colors hover:bg-brand-300 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {busy ? "处理中…" : "保存并测试"}
        </button>
        {configured && (
          <button
            type="button"
            onClick={() => void clear()}
            disabled={busy}
            className="min-h-[44px] rounded-lg border border-zinc-700/70 px-4 py-2 text-sm text-zinc-400 transition-colors hover:border-red-800/60 hover:text-red-300 disabled:opacity-60"
          >
            清除配置
          </button>
        )}
        <span className="text-xs text-zinc-500">
          {configured
            ? `当前：${view?.model || "未命名模型"} · ${view?.key_hint || "****"}${view?.last_verified_at ? ` · 已验证` : ""}`
            : "还没有配置，配置后才能创建会话并调用模型"}
        </span>
      </div>

      {status && (
        <p role="status" className="rounded-lg border border-zinc-700/60 bg-zinc-900/60 p-3 text-xs text-zinc-300">
          {status}
        </p>
      )}

      {view?.last_error && (
        <p role="alert" className="rounded-lg border border-amber-800/40 bg-amber-900/20 p-3 text-xs text-amber-300">
          最近一次调用失败：{view.last_error}
        </p>
      )}
    </form>
  );
}
