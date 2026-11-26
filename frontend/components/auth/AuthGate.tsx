"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { BrainIcon } from "@/components/icons";
import { api, ApiError, errorMessage } from "@/lib/api";
import { toast } from "@/components/Toast";

// /product 为静态营销页，无需登录即可访问（B127）。
const PUBLIC_PATHS = new Set(["/login", "/product"]);

// Module-level verification cache: once verified, route changes skip /api/auth/me.
// Cleared on 401 or explicit logout so the gate re-checks.
let authVerified = false;

export function resetAuthVerification() {
  authVerified = false;
}

export function AuthGate({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [authorized, setAuthorized] = useState(PUBLIC_PATHS.has(pathname) || authVerified);

  useEffect(() => {
    if (PUBLIC_PATHS.has(pathname)) {
      setAuthorized(true);
      return;
    }

    if (authVerified) {
      setAuthorized(true);
      return;
    }

    let cancelled = false;
    setAuthorized(false);
    // 第 42 批：旧实现走无超时的 legacy GET，Space 唤醒时最坏要转 43s；
    // 5s 拿不到结果就先放行（页面自己的请求会再校验），避免冷启动白屏。
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 5_000);
    api<{ authenticated: boolean }>("/api/auth/me", { signal: controller.signal })
      .then(() => {
        authVerified = true;
        if (!cancelled) setAuthorized(true);
      })
      .catch((err) => {
        if (cancelled) return;
        const status = err instanceof ApiError ? err.status : 0;
        if (status === 401) {
          authVerified = false;
          toast.error("登录已过期，请重新登录");
          const next = encodeURIComponent(pathname || "/");
          router.replace(`/login?next=${next}`);
        } else {
          // 网络/服务端问题：先放行，让页面自己的请求暴露真实错误；
          // 但要给用户一条明确提示，否则他们不知道「为什么一进页面就报错」。
          setAuthorized(true);
          toast.error(
            err instanceof Error && err.name === "AbortError"
              ? "登录状态校验超时（网络不稳），已先按已登录继续；若提示未登录请重新登录。"
              : `登录状态暂时无法校验：${errorMessage(err)}`
          );
        }
      })
      .finally(() => clearTimeout(timer));

    return () => {
      cancelled = true;
    };
  }, [pathname, router]);

  if (!authorized) {
    return (
      <div className="flex min-h-dvh items-center justify-center bg-dark-900">
        <div className="text-center">
          <BrainIcon size={42} className="mx-auto animate-pulse text-brand-300" />
          <p className="mt-4 text-sm text-zinc-400">正在验证登录状态…</p>
        </div>
      </div>
    );
  }

  return <>{children}</>;
}
