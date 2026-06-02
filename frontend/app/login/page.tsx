"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useSessionStore } from "@/store/sessionStore";
import { api } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [countdown, setCountdown] = useState(0);
  const [isSending, setIsSending] = useState(false);
  const [isLoggingIn, setIsLoggingIn] = useState(false);
  const [smsStatus, setSmsStatus] = useState<"idle" | "sent" | "failed">("idle");
  const [displayCode, setDisplayCode] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mounted, setMounted] = useState(false);

  const login = useSessionStore((s) => s.login);

  useEffect(() => { setMounted(true); }, []);

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
        if (result.code) {
          setSmsStatus("failed");
          setDisplayCode(result.code);
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
      router.push("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败");
    } finally {
      setIsLoggingIn(false);
    }
  }, [phone, code, isLoggingIn, login, router]);

  return (
    <div className="min-h-screen relative overflow-hidden bg-gradient-to-br from-orange-50 via-amber-50 to-rose-50 flex items-center justify-center px-4">
      <div className="landing-blobs" />
      <div className="landing-dots" />

      <div className={`w-full max-w-sm relative z-10 transition-all duration-700 ${mounted ? "opacity-100 translate-y-0" : "opacity-0 translate-y-5"}`}>
        <div className="bg-white border border-amber-200 rounded-2xl shadow-lg shadow-amber-100 overflow-hidden">
          <div className="px-6 pt-6 pb-4 text-center">
            <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-amber-400 via-orange-500 to-rose-500 flex items-center justify-center shadow-md shadow-orange-200 mx-auto mb-3">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
                <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                <line x1="12" y1="19" x2="12" y2="22" />
                <line x1="8" y1="22" x2="16" y2="22" />
              </svg>
            </div>
            <h1 className="text-lg font-extrabold text-amber-900 tracking-tight">登录 PM Brainstorm</h1>
            <p className="text-xs text-amber-700 mt-1 font-medium">登录后可保存会话历史与个人数据</p>
          </div>

          <div className="px-6 py-2 bg-amber-50/50 border-y border-amber-100">
            <p className="text-center text-xs text-amber-600 font-semibold">📱 手机号登录</p>
          </div>

          <div className="p-6">
            <div className="space-y-4">
              <div>
                <label className="block text-[11px] text-amber-900 mb-1.5 font-bold tracking-wide uppercase">手机号</label>
                <input
                  type="tel"
                  value={phone}
                  onChange={(e) => { const v = e.target.value.replace(/\D/g, "").slice(0, 11); setPhone(v); }}
                  placeholder="请输入11位手机号"
                  maxLength={11}
                  className="w-full bg-white border border-amber-200 rounded-xl px-4 py-2.5 text-sm text-gray-900 placeholder-amber-400 focus:outline-none focus:border-amber-400 focus:ring-2 focus:ring-amber-100 transition-all font-medium"
                />
              </div>
              <div>
                <label className="block text-[11px] text-amber-900 mb-1.5 font-bold tracking-wide uppercase">验证码</label>
                <div className="flex gap-2">
                  <input
                    type="tel"
                    value={code}
                    onChange={(e) => { const v = e.target.value.replace(/\D/g, "").slice(0, 6); setCode(v); }}
                    placeholder="6位验证码"
                    maxLength={6}
                    className="flex-1 bg-white border border-amber-200 rounded-xl px-4 py-2.5 text-sm text-gray-900 placeholder-amber-400 focus:outline-none focus:border-amber-400 focus:ring-2 focus:ring-amber-100 transition-all font-medium"
                  />
                  <button
                    onClick={handleSendCode}
                    disabled={phone.length !== 11 || countdown > 0 || isSending}
                    className="shrink-0 px-3.5 py-2.5 text-xs font-semibold rounded-xl transition-all duration-200 min-w-[100px] disabled:bg-amber-100 disabled:text-amber-400 bg-amber-50 text-amber-700 hover:bg-amber-100 border border-amber-200"
                  >
                    {isSending ? "发送中..." : countdown > 0 ? `${countdown}s` : "获取验证码"}
                  </button>
                </div>
                {smsStatus === "sent" && (
                  <div className="mt-2 px-3 py-1.5 bg-green-50 border border-green-200 rounded-lg text-xs text-green-700 font-medium">
                    ✅ 验证码已发送，请查收短信
                  </div>
                )}
                {smsStatus === "failed" && displayCode && (
                  <div className="mt-2 px-3 py-2 bg-amber-50 border border-amber-300 rounded-lg text-xs font-medium space-y-1">
                    <p className="text-amber-700">⚠️ 短信服务暂未配置，验证码已生成：</p>
                    <p className="text-amber-900 text-base font-bold tracking-widest text-center py-1 bg-white rounded-md border border-amber-200">
                      {displayCode}
                    </p>
                    <p className="text-amber-600 text-[10px]">请在上方输入框中填入此验证码完成登录</p>
                  </div>
                )}
                {smsStatus === "failed" && !displayCode && (
                  <div className="mt-2 px-3 py-1.5 bg-amber-50 border border-amber-200 rounded-lg text-xs text-amber-700 font-medium">
                    ⚠️ 短信服务暂不可用，请联系管理员
                  </div>
                )}
              </div>
              <button
                onClick={handlePhoneLogin}
                disabled={phone.length !== 11 || code.length !== 6 || isLoggingIn}
                className="w-full h-11 bg-gradient-to-r from-amber-500 to-orange-500 hover:from-amber-600 hover:to-orange-600 active:from-amber-700 active:to-orange-700 disabled:from-amber-200 disabled:to-orange-200 disabled:text-amber-400 text-white font-semibold rounded-xl transition-all duration-200 flex items-center justify-center gap-2 text-sm shadow-md shadow-amber-200 disabled:shadow-none"
              >
                {isLoggingIn ? (
                  <>
                    <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                    登录中...
                  </>
                ) : "登录"}
              </button>
            </div>

            {error && (
              <div role="alert" className="mt-4 px-3 py-2 bg-red-50 border border-red-200 rounded-lg text-red-600 text-xs font-medium">
                {error}
              </div>
            )}
          </div>
        </div>

      </div>
    </div>
  );
}
