"use client";

import { FormEvent, Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ArrowRightIcon, BrainIcon, ShieldIcon } from "@/components/icons";
import { api, ApiError } from "@/lib/api";

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const COOLDOWN_KEY = "pm_cooldown_until";

function readPersistedCooldown(): number {
  try {
    const until = Number(window.sessionStorage.getItem(COOLDOWN_KEY) || 0);
    if (!Number.isFinite(until) || until <= Date.now()) return 0;
    return Math.ceil((until - Date.now()) / 1000);
  } catch {
    return 0;
  }
}

function persistCooldown(seconds: number) {
  try {
    if (seconds > 0) {
      window.sessionStorage.setItem(COOLDOWN_KEY, String(Date.now() + seconds * 1000));
    } else {
      window.sessionStorage.removeItem(COOLDOWN_KEY);
    }
  } catch {
    // sessionStorage unavailable — cooldown just won't persist
  }
}

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [step, setStep] = useState<"email" | "code">("email");
  const [sendingCode, setSendingCode] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cooldown, setCooldown] = useState(0);

  // Restore cooldown from sessionStorage (survives remounts within the tab)
  useEffect(() => {
    const remaining = readPersistedCooldown();
    if (remaining > 0) setCooldown(remaining);
  }, []);

  useEffect(() => {
    persistCooldown(cooldown);
    if (cooldown <= 0) return;
    const timer = window.setInterval(() => setCooldown((value) => Math.max(0, value - 1)), 1000);
    return () => window.clearInterval(timer);
  }, [cooldown]);

  const destination = (() => {
    const next = searchParams.get("next");
    if (!next) return "/";
    // Only same-origin absolute paths; reject protocol-relative and backslash tricks
    return next.startsWith("/") && !next.startsWith("//") && !next.startsWith("/\\")
      ? next
      : "/";
  })();

  const sendCode = async () => {
    if (!email.trim() || sendingCode || verifying || cooldown > 0) return;
    if (!EMAIL_RE.test(email.trim())) {
      setError("请输入有效的邮箱地址");
      return;
    }
    setSendingCode(true);
    setError(null);
    try {
      const result = await api<{ retry_after: number }>("/api/auth/email/code", {
        method: "POST",
        body: JSON.stringify({ email: email.trim() }),
      });
      setStep("code");
      setCooldown(result.retry_after);
    } catch (requestError) {
      // 429 限流：读取后端 retry_after 启动冷却倒计时（B120）。
      if (requestError instanceof ApiError && requestError.status === 429) {
        const detail = requestError.payload?.detail as Record<string, unknown> | undefined;
        const retryAfter = Number(detail?.retry_after ?? requestError.payload?.retry_after);
        if (Number.isFinite(retryAfter) && retryAfter > 0) {
          setCooldown(Math.ceil(retryAfter));
        }
      }
      setError(requestError instanceof Error ? requestError.message : "验证码发送失败");
    } finally {
      setSendingCode(false);
    }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (step === "email") {
      await sendCode();
      return;
    }
    if (code.length !== 6 || sendingCode || verifying) return;

    setVerifying(true);
    setError(null);
    try {
      await api<{ authenticated: boolean }>("/api/auth/email/verify", {
        method: "POST",
        body: JSON.stringify({ email: email.trim(), code }),
      });
      router.replace(destination);
      router.refresh();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "登录失败");
    } finally {
      setVerifying(false);
    }
  };

  const busy = sendingCode || verifying;

  return (
    <main className="relative flex min-h-dvh items-center justify-center overflow-hidden bg-dark-900 px-5 py-12 text-zinc-100">
      <div className="landing-grid" aria-hidden="true" />
      <div className="landing-vignette" aria-hidden="true" />

      <section className="relative z-10 w-full max-w-md overflow-hidden rounded-2xl border border-white/10 bg-dark-800/95 shadow-2xl shadow-black/50 backdrop-blur-xl">
        <div className="border-b border-white/10 px-7 py-6">
          <div className="flex size-11 items-center justify-center rounded-xl border border-cyan-300/30 bg-cyan-300/10 text-cyan-200">
            <BrainIcon size={21} />
          </div>
          <p className="mt-6 text-[10px] font-semibold uppercase tracking-[0.18em] text-cyan-200/60">
            PM Brainstorm · Secure Access
          </p>
          <h1 className="mt-2 text-2xl font-semibold text-white">使用邮箱登录工作台</h1>
          <p className="mt-2 text-sm leading-6 text-zinc-500">
            无需密码，我们会向你的邮箱发送一次性验证码。
          </p>
        </div>

        <form onSubmit={submit} className="px-7 py-7">
          <label htmlFor="login-email" className="mb-2 block text-xs font-medium text-zinc-300">
            邮箱地址
          </label>
          <input
            id="login-email"
            type="email"
            autoComplete="email"
            required
            disabled={step === "code"}
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="name@example.com"
            className="h-12 w-full rounded-xl border border-white/10 bg-black/20 px-4 text-sm text-white outline-none transition-colors placeholder:text-zinc-500 focus:border-cyan-300/45 focus:ring-2 focus:ring-cyan-300/10 disabled:text-zinc-500"
          />

          {step === "code" && (
            <div className="mt-5">
              <div className="mb-2 flex items-center justify-between">
                <label htmlFor="login-code" className="text-xs font-medium text-zinc-300">
                  六位验证码
                </label>
                <button
                  type="button"
                  disabled={cooldown > 0 || sendingCode}
                  onClick={() => void sendCode()}
                  className="text-[10px] text-cyan-200 transition-colors hover:text-cyan-100 disabled:text-zinc-500"
                >
                  {cooldown > 0 ? `${cooldown} 秒后重发` : sendingCode ? "发送中..." : "重新发送"}
                </button>
              </div>
              <input
                id="login-code"
                inputMode="numeric"
                autoComplete="one-time-code"
                required
                maxLength={6}
                value={code}
                onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))}
                placeholder="000000"
                autoFocus
                className="h-14 w-full rounded-xl border border-white/10 bg-black/20 px-4 text-center font-mono text-xl tracking-[0.45em] text-white outline-none transition-colors placeholder:text-zinc-500 focus:border-cyan-300/45 focus:ring-2 focus:ring-cyan-300/10"
              />
            </div>
          )}

          {error && (
            <p role="alert" className="mt-4 rounded-lg border border-red-400/20 bg-red-400/5 px-3 py-2.5 text-xs leading-5 text-red-300">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={busy || !email.trim() || (step === "code" && code.length !== 6)}
            className="mt-6 flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-cyan-300 text-sm font-semibold text-[#031014] transition-colors hover:bg-cyan-200 disabled:cursor-not-allowed disabled:bg-zinc-800 disabled:text-zinc-500"
          >
            {busy ? (
              <span className="size-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
            ) : step === "email" ? (
              <>发送登录验证码 <ArrowRightIcon size={16} /></>
            ) : (
              <>验证并进入工作台 <ArrowRightIcon size={16} /></>
            )}
          </button>

          {step === "code" && (
            <button
              type="button"
              onClick={() => {
                setStep("email");
                setCode("");
                setError(null);
              }}
              className="mt-3 h-10 w-full text-xs text-zinc-500 hover:text-zinc-300"
            >
              更换邮箱
            </button>
          )}
        </form>

        <div className="flex items-center justify-center gap-2 border-t border-white/10 bg-black/15 px-6 py-4 text-[10px] text-zinc-500">
          <ShieldIcon size={12} className="text-emerald-300/70" />
          验证码仅用于登录，不会向第三方公开邮箱
        </div>
      </section>
    </main>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={<div className="min-h-dvh bg-dark-900" />}>
      <LoginForm />
    </Suspense>
  );
}
