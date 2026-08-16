"use client";

import { useEffect } from "react";

export default function ErrorPage({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <main className="flex min-h-dvh flex-col items-center justify-center bg-dark-900 bg-mesh px-6 text-center">
      <div className="flex size-14 items-center justify-center rounded-xl border border-red-400/30 bg-red-400/10 text-red-300">
        <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 9v4" />
          <path d="M12 17h.01" />
          <path d="M10.3 3.3 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.3a2 2 0 0 0-3.4 0Z" />
        </svg>
      </div>
      <h1 className="mt-6 text-2xl font-semibold text-zinc-100">出错了</h1>
      <p className="mt-3 max-w-sm text-sm leading-6 text-zinc-500">
        页面加载时发生异常，请重试；若持续失败请返回首页重新进入。
      </p>
      <div className="mt-8 flex items-center gap-3">
        <button
          onClick={reset}
          className="inline-flex h-11 items-center rounded-lg bg-brand-300 px-6 text-sm font-semibold text-dark-900 transition-opacity hover:opacity-90"
        >
          重试
        </button>
        <a
          href="/"
          className="inline-flex h-11 items-center rounded-lg border border-zinc-700 px-6 text-sm font-medium text-zinc-300 transition-colors hover:border-zinc-500 hover:text-zinc-100"
        >
          返回首页
        </a>
      </div>
    </main>
  );
}
