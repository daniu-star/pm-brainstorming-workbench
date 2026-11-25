"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { BrainIcon, ShieldIcon } from "@/components/icons";
import { GATE_ALLOWED_PATHS, gateState, getLlmConfig, type LlmConfigView } from "@/lib/llmConfig";

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

  useEffect(() => {
    if (GATE_ALLOWED_PATHS.includes(pathname)) return;
    let cancelled = false;
    getLlmConfig()
      .then((next) => {
        if (!cancelled) setView(next);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [pathname]);

  const state = gateState({ pathname, view, failed });

  if (state === "bypass" || state === "ready") return <>{children}</>;

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
