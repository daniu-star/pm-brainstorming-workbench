"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import { BrainIcon } from "@/components/icons";
import { api, ApiError } from "@/lib/api";
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
    api<{ authenticated: boolean }>("/api/auth/me")
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
          // Network/server issue — let the page render; protected API calls
          // will surface their own errors.
          setAuthorized(true);
        }
      });

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
