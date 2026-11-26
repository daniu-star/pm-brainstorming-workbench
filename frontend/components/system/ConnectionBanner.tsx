"use client";

import { useEffect, useState } from "react";

import { subscribeRetryNotices, type RetryNotice } from "@/lib/api";

/**
 * 全局「正在自动重试」横幅（第 42 批）。
 *
 * 线上后端在 Hugging Face Space，前置代理偶发 21 秒断开；客户端会自动重试，
 * 但重试期间如果页面毫无反馈，用户只会以为「又报错了」。这条横幅把重试过程
 * 明确说出来，恢复后自动收起。
 */
export function ConnectionBanner(): React.ReactNode {
  const [notice, setNotice] = useState<RetryNotice | null>(null);

  useEffect(() => {
    let hideTimer: ReturnType<typeof setTimeout> | null = null;

    const unsubscribe = subscribeRetryNotices((next) => {
      if (hideTimer) {
        clearTimeout(hideTimer);
        hideTimer = null;
      }
      if (next) {
        setNotice(next);
        return;
      }
      setNotice((current) => {
        if (!current) return current;
        // 恢复后多留 1.5 秒，避免横幅一闪而过让人怀疑自己看错。
        hideTimer = setTimeout(() => setNotice(null), 1_500);
        return current;
      });
    });

    return () => {
      unsubscribe();
      if (hideTimer) clearTimeout(hideTimer);
    };
  }, []);

  if (!notice) return null;

  return (
    <div
      role="status"
      aria-live="polite"
      className="fixed inset-x-0 top-0 z-40 flex items-center justify-center gap-2 border-b border-amber-400/40 bg-amber-500/15 px-4 py-2 text-xs text-amber-100 backdrop-blur"
    >
      <span className="h-2 w-2 animate-pulse rounded-full bg-amber-300" aria-hidden="true" />
      <span>
        连接不稳定（{notice.reason}），正在自动重试 {notice.attempt}/{Math.max(notice.attempts - 1, 1)}
        …
      </span>
    </div>
  );
}
