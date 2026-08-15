"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Mail, ShieldCheck } from "lucide-react";
import { CosmicBackground } from "@/components/CosmicBackground";
import { useSessionStore } from "@/store/sessionStore";
import { clearJwtToken, consumeAuthReturnPath } from "@/lib/user";

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export default function LoginPage() {
  const router = useRouter();
  const login = useSessionStore((state) => state.login);
  const guestLogin = useSessionStore((state) => state.guestLogin);
  const [mounted, setMounted] = useState(false);
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [countdown, setCountdown] = useState(0);
  const [isSending, setIsSending] = useState(false);
  const [isLoggingIn, setIsLoggingIn] = useState(false);
  const [isGuestLoggingIn, setIsGuestLoggingIn] = useState(false);
  const [codeSent, setCodeSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const normalizedEmail = email.trim().toLowerCase();
  const emailIsValid = EMAIL_PATTERN.test(normalizedEmail);

  useEffect(() => {
    clearJwtToken();
    setMounted(true);
  }, []);

  useEffect(() => {
    if (countdown <= 0) return;
    const timer = window.setTimeout(() => setCountdown((value) => value - 1), 1000);
    return () => window.clearTimeout(timer);
  }, [countdown]);

  const handleSendCode = useCallback(async () => {
    if (!emailIsValid || isSending || countdown > 0) return;
    setIsSending(true);
    setError(null);
    setCodeSent(false);
    try {
      const response = await fetch("/api/auth/email/send", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: normalizedEmail }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.detail || "验证码发送失败，请稍后重试");
      }
      setCodeSent(true);
      setCountdown(Number(data.retry_after) || 60);
    } catch (sendError) {
      setError(sendError instanceof Error ? sendError.message : "验证码发送失败");
    } finally {
      setIsSending(false);
    }
  }, [countdown, emailIsValid, isSending, normalizedEmail]);

  const handleEmailLogin = useCallback(async () => {
    if (!emailIsValid || code.length !== 6 || isLoggingIn) return;
    setIsLoggingIn(true);
    setError(null);
    try {
      await login(normalizedEmail, code);
      router.replace(consumeAuthReturnPath());
    } catch (loginError) {
      setError(loginError instanceof Error ? loginError.message : "登录失败");
    } finally {
      setIsLoggingIn(false);
    }
  }, [code, emailIsValid, isLoggingIn, login, normalizedEmail, router]);

  const handleGuestLogin = useCallback(async () => {
    if (isGuestLoggingIn) return;
    setIsGuestLoggingIn(true);
    setError(null);
    try {
      await guestLogin();
      router.replace(consumeAuthReturnPath());
    } catch (guestError) {
      setError(guestError instanceof Error ? guestError.message : "体验模式登录失败");
    } finally {
      setIsGuestLoggingIn(false);
    }
  }, [guestLogin, isGuestLoggingIn, router]);

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-[radial-gradient(circle_at_top,#10243d_0%,#07111f_42%,#03070d_100%)] px-4 text-zinc-100">
      <CosmicBackground density={54} />
      <div className="pointer-events-none absolute left-1/2 top-0 h-[28rem] w-[44rem] -translate-x-1/2 rounded-full bg-cyan-400/10 blur-[120px]" />
      <div className={`relative z-10 w-full max-w-md transition-all duration-700 ${mounted ? "translate-y-0 opacity-100" : "translate-y-5 opacity-0"}`}>
        <div className="overflow-hidden rounded-3xl border border-white/10 bg-[#07111f]/85 shadow-2xl shadow-cyan-950/50 backdrop-blur-2xl">
          <div className="px-6 pb-4 pt-6 text-center">
            <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-2xl border border-cyan-200/30 bg-gradient-to-br from-cyan-300 via-sky-500 to-indigo-600 shadow-lg shadow-cyan-500/20">
              <Mail className="h-6 w-6 text-white" />
            </div>
            <h1 className="text-xl font-bold tracking-tight text-white">进入产品脑暴工作台</h1>
            <p className="mt-1 text-xs font-medium text-zinc-400">使用邮箱验证码安全登录</p>
          </div>

          <div className="border-y border-white/10 bg-white/[0.03] px-6 py-2">
            <p className="flex items-center justify-center gap-1.5 text-xs font-semibold text-cyan-200">
              <ShieldCheck className="h-3.5 w-3.5" />
              验证码仅用于本次登录
            </p>
          </div>

          <div className="space-y-4 p-6">
            <div>
              <label className="mb-1.5 block text-xs font-semibold text-zinc-300">邮箱地址</label>
              <input
                type="email"
                autoComplete="email"
                value={email}
                onChange={(event) => setEmail(event.target.value.slice(0, 254))}
                placeholder="name@example.com"
                className="w-full rounded-xl border border-white/10 bg-white/[0.05] px-4 py-2.5 text-sm font-medium text-white placeholder-zinc-600 transition-all focus:border-cyan-300/50 focus:outline-none focus:ring-2 focus:ring-cyan-400/15"
              />
            </div>

            <div>
              <label className="mb-1.5 block text-xs font-semibold text-zinc-300">邮箱验证码</label>
              <div className="flex gap-2">
                <input
                  type="text"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  value={code}
                  onChange={(event) => setCode(event.target.value.replace(/\D/g, "").slice(0, 6))}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") void handleEmailLogin();
                  }}
                  placeholder="6 位验证码"
                  maxLength={6}
                  className="min-w-0 flex-1 rounded-xl border border-white/10 bg-white/[0.05] px-4 py-2.5 text-sm font-medium text-white placeholder-zinc-600 transition-all focus:border-cyan-300/50 focus:outline-none focus:ring-2 focus:ring-cyan-400/15"
                />
                <button
                  type="button"
                  onClick={() => void handleSendCode()}
                  disabled={!emailIsValid || countdown > 0 || isSending}
                  className="min-w-[108px] shrink-0 rounded-xl border border-cyan-300/20 bg-cyan-300/10 px-3.5 py-2.5 text-xs font-semibold text-cyan-100 transition-all hover:bg-cyan-300/15 disabled:border-white/5 disabled:bg-white/[0.03] disabled:text-zinc-600"
                >
                  {isSending ? "发送中..." : countdown > 0 ? `${countdown}s 后重发` : "获取验证码"}
                </button>
              </div>
              {codeSent && (
                <p className="mt-2 rounded-lg border border-emerald-300/20 bg-emerald-300/10 px-3 py-1.5 text-xs font-medium text-emerald-200">
                  验证码已发送，请检查收件箱和垃圾邮件。
                </p>
              )}
            </div>

            <button
              type="button"
              onClick={() => void handleEmailLogin()}
              disabled={!emailIsValid || code.length !== 6 || isLoggingIn}
              className="flex h-11 w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-cyan-400 to-sky-500 text-sm font-semibold text-slate-950 shadow-lg shadow-cyan-500/20 transition-all hover:brightness-110 disabled:bg-none disabled:bg-white/[0.05] disabled:text-zinc-600 disabled:shadow-none"
            >
              {isLoggingIn ? "登录中..." : "邮箱登录"}
            </button>

            <div className="flex items-center gap-3 py-1 text-xs text-zinc-600">
              <span className="h-px flex-1 bg-white/10" />
              或
              <span className="h-px flex-1 bg-white/10" />
            </div>

            <button
              type="button"
              onClick={() => void handleGuestLogin()}
              disabled={isGuestLoggingIn}
              className="flex h-11 w-full items-center justify-center rounded-xl border border-white/10 bg-white/[0.05] text-sm font-semibold text-zinc-200 transition-all hover:border-cyan-300/25 hover:bg-cyan-300/[0.08] hover:text-white disabled:opacity-50"
            >
              {isGuestLoggingIn ? "正在创建独立空间..." : "先体验产品"}
            </button>

            {error && (
              <div role="alert" className="rounded-lg border border-red-300/20 bg-red-300/10 px-3 py-2 text-xs font-medium text-red-200">
                {error}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
