"use client";

import Link from "next/link";
import { ArrowRightIcon, ShieldIcon } from "@/components/icons";
import { LlmConfigForm } from "@/components/llm/LlmConfigForm";

export default function LlmSettingsPage() {
  return (
    <main id="main-content" className="mx-auto flex min-h-dvh w-full max-w-3xl flex-col gap-6 px-4 py-10">
      <header className="flex flex-col gap-2">
        <Link
          href="/"
          className="inline-flex w-fit items-center gap-1 text-xs text-zinc-500 transition-colors hover:text-zinc-300"
        >
          <ArrowRightIcon size={14} className="rotate-180" />
          返回工作台
        </Link>
        <div className="flex items-center gap-2">
          <ShieldIcon size={22} className="text-brand-300" />
          <h1 className="text-xl font-semibold text-zinc-100">模型 API 设置</h1>
        </div>
        <p className="text-sm leading-6 text-zinc-400">
          填写你自己的模型接口与密钥。平台不代付模型费用，因此每个账户都需要各自的 Key；
          密钥在服务端加密保存，接口只回显末 4 位。
        </p>
      </header>

      <section className="rounded-2xl border border-zinc-700/60 bg-dark-800/70 p-5">
        <LlmConfigForm />
      </section>

      <section className="rounded-xl border border-zinc-800/80 bg-dark-900/60 p-4 text-xs leading-6 text-zinc-500">
        <p className="font-medium text-zinc-400">怎么拿到 Key</p>
        <p className="mt-1">
          DeepSeek：登录 platform.deepseek.com 创建 API Key，接口地址填 https://api.deepseek.com/v1，模型填
          deepseek-flash。OpenAI 兼容的其它服务同理，只要接口支持 /chat/completions。
        </p>
        <p className="mt-2">
          如果保存成功但测试提示 401/402，通常是 Key 无效或余额不足；提示 404 一般是 base_url
          少了 /v1 或模型名写错。
        </p>
      </section>
    </main>
  );
}
