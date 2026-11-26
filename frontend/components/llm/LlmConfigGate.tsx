"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { BrainIcon, ShieldIcon } from "@/components/icons";
import { GATE_ALLOWED_PATHS, gateState, getLlmConfig, type LlmConfigView } from "@/lib/llmConfig";
import { errorMessage } from "@/lib/api";

/**
 * 强制「自带模型密钥」闸门：登录后只要账户还没配置自己的模型 API，
 * 所有业务页面都会被这块面板挡住，直到用户在 /settings/llm 里保存成功。
 *
 * 判定逻辑在 `lib/llmConfig.ts::gateState`（纯函数，有单测），这里只负责渲染。
 */
export function LlmConfigGate({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [view, setView] = useState<LlmConfigView | null>(null);
  const [failed, setFailed] = useState(false);
  const [bypassNotice, setBypassNotice] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);

  useEffect(() => {
    if (GATE_ALLOWED_PATHS.includes(pathname)) return;
    let cancelled = false;
    getLlmConfig()
      .then((next) => {
        if (cancelled) return;
        setView(next);
        setFailed(false);
        setBypassNotice(null);
      })
      .catch((err) => {
        if (cancelled) return;
        // 读失败不能静默放行：必须告诉用户「应用可能不可用」并给出重试入口，
        // 否则用户进入后每次操作都失败，却以为是自己配置错了（第 42 批）。
        setFailed(true);
        setBypassNotice(errorMessage(err));
      });
    return () => {
      cancelled = true;
    };
  }, [pathname]);

  const retry = (): void => {
    if (retrying) return;
    setRetrying(true);
    setFailed(false);
    setView(null);
    getLlmConfig()
      .then((next) => {
        setView(next);
        setBypassNotice(null);
      })
      .catch((err) => {
        setFailed(true);
        setBypassNotice(errorMessage(err));
      })
      .finally(() => setRetrying(false));
  };

  const state = gateState({ pathname, view, failed });

  if (state === "ready") return <>{children}</>;

  if (state === "bypass") {
    return (
      <>
        <div
          role="status"
          className="sticky top-0 z-40 flex flex-wrap items-center justify-center gap-2 bg-amber-500/15 px-4 py-2 text-center text-xs text-amber-200"
        >
          <span>
            模型配置读取失败（网络不稳），功能可能不可用：{bypassNotice ?? "未知原因"}
          </span>
          <button
            type="button"
            onClick={retry}
            className="min-h-[32px] rounded-md border border-amber-400/60 px-2 py-1 font-medium text-amber-100 hover:bg-amber-400/20"
          >
            {retrying ? "重试中…" : "重试读取"}
          </button>
        </div>
        {children}
      </>
    );
  }

  if (state === "loading") {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-dark-900">
        <div className="text-center">
          <BrainIcon size={38} className="mx-auto animate-pulse text-brand-300" />
          <p className="mt-4 text-sm text-zinc-400">正在检查模型配置…</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-dvh items-center justify-center bg-dark-900 px-4 py-10">
      <div className="w-full max-w-lg rounded-2xl border border-zinc-700/60 bg-dark-800/80 p-6 shadow-2xl">
        <div className="flex items-center gap-2 text-brand-300">
          <ShieldIcon size={22} />
          <h1 className="text-lg font-semibold text-zinc-100">先配置你自己的模型 API</h1>
        </div>
        <p className="mt-3 text-sm leading-6 text-zinc-400">
          平台前期免费使用，但模型调用由你自己的 API Key 支付，所以需要先填写接口地址、模型名称与密钥。
          密钥加密后保存在服务端，之后只显示末 4 位，任何时候都不会回显完整内容。
        </p>
        <ul className="mt-3 space-y-1.5 text-xs text-zinc-500">
          <li>· 任何 OpenAI 兼容接口都可以：DeepSeek、OpenAI、自建代理或中转站</li>
          <li>· 建议模型名与实际接口一致，例如 deepseek-flash、gpt-4o-mini</li>
          <li>· 保存后会用一次极小请求测试连通性，失败会给出上游原始原因</li>
        </ul>
        <Link
          href="/settings/llm"
          className="mt-5 flex min-h-[44px] w-full items-center justify-center rounded-lg bg-brand-300/90 px-4 py-2 text-sm font-medium text-dark-900 transition-colors hover:bg-brand-300"
        >
          去填写模型配置
        </Link>
        <p className="mt-3 text-center text-[11px] text-zinc-600">
          配置完成后回到任意页面即可继续，无需重新登录
        </p>
      </div>
    </div>
  );
}
