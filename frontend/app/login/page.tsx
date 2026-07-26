"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useSessionStore } from "@/store/sessionStore";
import { api } from "@/lib/api";
import { CosmicBackground } from "@/components/CosmicBackground";
import { clearJwtToken, consumeAuthReturnPath } from "@/lib/user";

export default function LoginPage() {
  const router = useRouter();
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [countdown, setCountdown] = useState(0);
  const [isSending, setIsSending] = useState(false);
  const [isLoggingIn, setIsLoggingIn] = useState(false);
  const [isGuestLoggingIn, setIsGuestLoggingIn] = useState(false);
  const [smsStatus, setSmsStatus] = useState<"idle" | "sent" | "failed">("idle");
  const [displayCode, setDisplayCode] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mounted, setMounted] = useState(false);

  const login = useSessionStore((s) => s.login);
  const guestLogin = useSessionStore((s) => s.guestLogin);

  useEffect(() => {
    setMounted(true);
    if (new URLSearchParams(window.location.search).get("reason") === "expired") {
      clearJwtToken();
      useSessionStore.setState({ isLoggedIn: false, userNickname: null });
      setError("登录状态已过期，请重新登录或直接进入体验模式");
    }
  }, []);

  useEffect(() => {
    if (countdown <= 0) return;
    const timer = setTimeout(() => setCountdown((c) => c - 1), 1000);
    return () => clearTimeout(timer);
  }, [countdown]);

  useEffect(() => {
    if (countdown === 0) setSmsStatus("idle");
  }, [countdown]);

  const handleSendCode = useCallback(async () => {
    if (phone.length !== 11 || countdown > 0 || isSending) return;
    setIsSending(true);
    setError(null);
    setDisplayCode(null);
    try {
      const result = await api<{ success: boolean; hint?: string; code?: string }>("/api/auth/sms/send", {
        method: "POST",
        body: JSON.stringify({ phone }),
      });
      if (result.success) {
        setCountdown(60);
        if (result.hint === "短信服务暂未配置" || result.code) {
          setSmsStatus("failed");
          setDisplayCode(result.code || null);
        } else {
          setSmsStatus("sent");
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "发送验证码失败");
    } finally {
      setIsSending(false);
    }
  }, [phone, countdown, isSending]);

  const handlePhoneLogin = useCallback(async () => {
    if (phone.length !== 11 || code.length !== 6 || isLoggingIn) return;
    setIsLoggingIn(true);
    setError(null);
    try {
      await login(phone, code);
      router.replace(consumeAuthReturnPath());
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败");
    } finally {
      setIsLoggingIn(false);
    }
  }, [phone, code, isLoggingIn, login, router]);

  const handleGuestLogin = useCallback(async () => {
    if (isGuestLoggingIn) return;
    setIsGuestLoggingIn(true);
    setError(null);
    try {
      await guestLogin();
      router.replace(consumeAuthReturnPath());
    } catch (err) {
      setError(err instanceof Error ? err.message : "体验模式登录失败");
    } finally {
      setIsGuestLoggingIn(false);
    }
  }, [guestLogin, isGuestLoggingIn, router]);

  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-[radial-gradient(circle_at_top,#10243d_0%,#07111f_42%,#03070d_100%)] px-4 text-zinc-100">
      <CosmicBackground density={54} />
      <div className="pointer-events-none absolute left-1/2 top-0 h-[28rem] w-[44rem] -translate-x-1/2 rounded-full bg-cyan-400/10 blur-[120px]" />
      <div className={`w-full max-w-md relative z-10 transition-all duration-700 ${mounted ? "opacity-100 translate-y-0" : "opacity-0 translate-y-5"}`}>
        <div className="overflow-hidden rounded-3xl border border-white/10 bg-[#07111f]/85 shadow-2xl shadow-cyan-950/50 backdrop-blur-2xl">
          <div className="px-6 pt-6 pb-4 text-center">
            <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-2xl border border-cyan-200/30 bg-gradient-to-br from-cyan-300 via-sky-500 to-indigo-600 shadow-lg shadow-cyan-500/20">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
                <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                <line x1="12" y1="19" x2="12" y2="22" />
                <line x1="8" y1="22" x2="16" y2="22" />
              </svg>
            </div>
            <h1 className="text-xl font-bold tracking-tight text-white">进入产品脑暴工作台</h1>
            <p className="mt-1 text-xs font-medium text-zinc-400">独立工作空间 · 决策记录可持续追溯</p>
          </div>

          <div className="border-y border-white/10 bg-white/[0.03] px-6 py-2">
            <p className="text-center text-xs font-semibold text-cyan-200">手机号安全登录</p>
          </div>

          <div className="p-6">
            <div className="space-y-4">
              <div>
                <label className="mb-1.5 block text-xs font-semibold text-zinc-300">手机号</label>
                <input
                  type="tel"
                  value={phone}
                  onChange={(e) => { const v = e.target.value.replace(/\D/g, "").slice(0, 11); setPhone(v); }}
                  placeholder="请输入11位手机号"
                  maxLength={11}
                  className="w-full rounded-xl border border-white/10 bg-white/[0.05] px-4 py-2.5 text-sm font-medium text-white placeholder-zinc-600 transition-all focus:border-cyan-300/50 focus:outline-none focus:ring-2 focus:ring-cyan-400/15"
                />
              </div>
              <div>
                <label className="mb-1.5 block text-xs font-semibold text-zinc-300">验证码</label>
                <div className="flex gap-2">
                  <input
                    type="tel"
                    value={code}
                    onChange={(e) => { const v = e.target.value.replace(/\D/g, "").slice(0, 6); setCode(v); }}
                    placeholder="6位验证码"
                    maxLength={6}
                    className="flex-1 rounded-xl border border-white/10 bg-white/[0.05] px-4 py-2.5 text-sm font-medium text-white placeholder-zinc-600 transition-all focus:border-cyan-300/50 focus:outline-none focus:ring-2 focus:ring-cyan-400/15"
                  />
                  <button
                    onClick={handleSendCode}
                    disabled={phone.length !== 11 || countdown > 0 || isSending}
                    className="min-w-[100px] shrink-0 rounded-xl border border-cyan-300/20 bg-cyan-300/10 px-3.5 py-2.5 text-xs font-semibold text-cyan-100 transition-all duration-200 hover:bg-cyan-300/15 disabled:border-white/5 disabled:bg-white/[0.03] disabled:text-zinc-600"
                  >
                    {isSending ? "发送中..." : countdown > 0 ? `${countdown}s` : "获取验证码"}
                  </button>
                </div>
                {smsStatus === "sent" && (
                  <div className="mt-2 rounded-lg border border-emerald-300/20 bg-emerald-300/10 px-3 py-1.5 text-xs font-medium text-emerald-200">
                    ✅ 验证码已发送，请查收短信
                  </div>
                )}
                {smsStatus === "failed" && displayCode && (
                  <div className="mt-2 space-y-1 rounded-lg border border-amber-300/20 bg-amber-300/10 px-3 py-2 text-xs font-medium">
                    <p className="text-amber-200">短信服务暂未配置，验证码已生成：</p>
                    <p className="rounded-md border border-white/10 bg-black/20 py-1.5 text-center font-mono text-2xl font-bold tracking-[0.3em] text-white">
                      {displayCode}
                    </p>
                    <p className="text-xs text-amber-200/70">请在上方输入框中填入此验证码完成登录</p>
                  </div>
                )}
                {smsStatus === "failed" && !displayCode && (
                  <div className="mt-2 rounded-lg border border-amber-300/20 bg-amber-300/10 px-3 py-1.5 text-xs font-medium text-amber-200">
                    短信服务暂不可用，可先使用下方体验模式
                  </div>
                )}
              </div>
              <button
                onClick={handlePhoneLogin}
                disabled={phone.length !== 11 || code.length !== 6 || isLoggingIn}
                className="flex h-11 w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-cyan-400 to-sky-500 text-sm font-semibold text-slate-950 shadow-lg shadow-cyan-500/20 transition-all duration-200 hover:brightness-110 disabled:bg-none disabled:bg-white/[0.05] disabled:text-zinc-600 disabled:shadow-none"
              >
                {isLoggingIn ? (
                  <>
                    <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                    登录中...
                  </>
                ) : "登录"}
              </button>
              <div className="flex items-center gap-3 py-1 text-xs text-zinc-600">
                <span className="h-px flex-1 bg-white/10" />
                或
                <span className="h-px flex-1 bg-white/10" />
              </div>
              <button
                onClick={handleGuestLogin}
                disabled={isGuestLoggingIn}
                className="flex h-11 w-full items-center justify-center rounded-xl border border-white/10 bg-white/[0.05] text-sm font-semibold text-zinc-200 transition-all hover:border-cyan-300/25 hover:bg-cyan-300/[0.08] hover:text-white disabled:opacity-50"
              >
                {isGuestLoggingIn ? "正在创建独立空间..." : "先体验产品"}
              </button>
              <p className="text-center text-xs leading-5 text-zinc-500">
                体验模式使用浏览器专属签名身份，不共享其他用户的数据
              </p>
            </div>

            {error && (
              <div role="alert" className="mt-4 rounded-lg border border-red-300/20 bg-red-300/10 px-3 py-2 text-xs font-medium text-red-200">
                {error}
              </div>
            )}
          </div>
        </div>

      </div>
    </div>
  );
}
