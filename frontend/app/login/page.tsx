"use client";

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useSessionStore } from "@/store/sessionStore";
import { api } from "@/lib/api";

type TabKey = "phone" | "wechat" | "qq";

const QR_PATTERN = [
  1,1,1,0,1,1,1,0,
  1,0,1,0,1,0,1,1,
  1,1,1,0,1,1,1,0,
  0,0,0,1,0,0,0,1,
  1,1,1,0,1,1,1,0,
  1,0,1,1,0,1,0,1,
  1,1,1,0,0,1,1,0,
  0,0,0,1,1,0,0,1,
];

export default function LoginPage() {
  const router = useRouter();
  const [activeTab, setActiveTab] = useState<TabKey>("phone");
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [countdown, setCountdown] = useState(0);
  const [isSending, setIsSending] = useState(false);
  const [isLoggingIn, setIsLoggingIn] = useState(false);
  const [smsStatus, setSmsStatus] = useState<"idle" | "sent" | "failed">("idle");
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
    try {
      const result = await api<{ success: boolean; hint: string }>("/api/auth/sms/send", {
        method: "POST",
        body: JSON.stringify({ phone }),
      });
      if (result.success) {
        setCountdown(60);
        if (!result.hint) {
          setSmsStatus("sent");
        } else {
          setSmsStatus("failed");
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

  const tabs: { key: TabKey; label: string }[] = [
    { key: "phone", label: "手机登录" },
    { key: "wechat", label: "微信（即将上线）" },
    { key: "qq", label: "QQ（即将上线）" },
  ];

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
              </svg>
            </div>
            <h1 className="text-lg font-extrabold text-amber-900 tracking-tight">登录 PM Brainstorm</h1>
            <p className="text-xs text-amber-700 mt-1 font-medium">登录后可保存会话历史与个人数据</p>
          </div>

          <div className="flex border-b border-amber-200">
            {tabs.map((tab) => (
              <button
                key={tab.key}
                onClick={() => {
                  setActiveTab(tab.key);
                  setError(null);
                }}
                className={`flex-1 py-2.5 text-xs font-bold transition-colors relative ${
                  activeTab === tab.key
                    ? "text-amber-900"
                    : "text-amber-400 hover:text-amber-600"
                }`}
              >
                {tab.label}
                {activeTab === tab.key && (
                  <span className="absolute bottom-0 left-1/4 right-1/4 h-0.5 bg-gradient-to-r from-amber-500 to-orange-500 rounded-full" />
                )}
              </button>
            ))}
          </div>

          <div className="p-6">
            {activeTab === "phone" && (
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
                      验证码已发送，请查收短信
                    </div>
                  )}
                  {smsStatus === "failed" && (
                    <div className="mt-2 px-3 py-1.5 bg-amber-50 border border-amber-200 rounded-lg text-xs text-amber-700 font-medium">
                      短信服务暂不可用，请联系管理员
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
            )}

            {activeTab === "wechat" && (
              <div className="space-y-4">
                <div className="flex flex-col items-center py-4">
                  <div className="relative">
                    <div className="w-[160px] h-[160px] rounded-xl bg-white border-2 border-gray-200 p-2">
                      <div className="grid grid-cols-8 grid-rows-8 gap-[2px] h-full">
                        {QR_PATTERN.map((cell, i) => (
                          <div key={i} className={`rounded-[1px] ${cell ? "bg-gray-800" : "bg-gray-100"}`} />
                        ))}
                      </div>
                      <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                        <div className="w-8 h-8 rounded-lg bg-[#07C160] flex items-center justify-center shadow-sm border-2 border-white">
                          <svg width="16" height="16" viewBox="0 0 24 24" fill="white">
                            <path d="M8.691 2.188C3.891 2.188 0 5.476 0 9.53c0 2.212 1.17 4.203 3.002 5.55a.59.59 0 0 1 .213.665l-.39 1.48c-.019.07-.048.141-.048.213 0 .163.13.295.29.295a.326.326 0 0 0 .167-.054l1.903-1.114a.864.864 0 0 1 .717-.098 10.16 10.16 0 0 0 2.837.403c.276 0 .543-.027.811-.05-.857-2.578.157-4.972 1.932-6.446 1.703-1.415 3.882-1.98 5.853-1.838-.576-3.583-4.196-6.348-8.596-6.348zM5.785 5.991c.642 0 1.162.529 1.162 1.18a1.17 1.17 0 0 1-1.162 1.178A1.17 1.17 0 0 1 4.623 7.17c0-.651.52-1.18 1.162-1.18zm5.813 0c.642 0 1.162.529 1.162 1.18a1.17 1.17 0 0 1-1.162 1.178 1.17 1.17 0 0 1-1.162-1.178c0-.651.52-1.18 1.162-1.18zm5.34 2.867c-1.797-.052-3.746.512-5.28 1.786-1.72 1.428-2.687 3.72-1.78 6.22.942 2.453 3.666 4.229 6.884 4.229.826 0 1.622-.12 2.361-.336a.722.722 0 0 1 .598.082l1.584.926a.272.272 0 0 0 .14.047c.134 0 .24-.111.24-.247 0-.06-.023-.12-.038-.177l-.327-1.233a.582.582 0 0 1-.023-.156.49.49 0 0 1 .201-.398C23.024 18.48 24 16.82 24 14.98c0-3.21-2.931-5.837-7.062-6.122zM14.87 13.13c.535 0 .969.44.969.982a.976.976 0 0 1-.969.983.976.976 0 0 1-.969-.983c0-.542.434-.982.97-.982zm4.82 0c.534 0 .968.44.968.982a.976.976 0 0 1-.969.983.976.976 0 0 1-.968-.983c0-.542.434-.982.969-.982z" />
                          </svg>
                        </div>
                      </div>
                    </div>
                    <div className="absolute inset-0 bg-gray-500/60 rounded-xl flex items-center justify-center">
                      <span className="text-white text-xl font-bold">即将上线</span>
                    </div>
                  </div>
                  <p className="text-xs text-gray-500 mt-3 font-medium">请使用微信扫描二维码登录</p>
                </div>
              </div>
            )}

            {activeTab === "qq" && (
              <div className="space-y-4">
                <div className="flex flex-col items-center py-4">
                  <div className="relative">
                    <div className="w-[160px] h-[160px] rounded-xl bg-white border-2 border-gray-200 p-2">
                      <div className="grid grid-cols-8 grid-rows-8 gap-[2px] h-full">
                        {QR_PATTERN.map((cell, i) => (
                          <div key={i} className={`rounded-[1px] ${cell ? "bg-gray-800" : "bg-gray-100"}`} />
                        ))}
                      </div>
                      <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                        <div className="w-8 h-8 rounded-lg bg-[#12B7F5] flex items-center justify-center shadow-sm border-2 border-white">
                          <svg width="16" height="16" viewBox="0 0 24 24" fill="white">
                            <path d="M12.003 2c-2.265 0-6.29 1.364-6.29 7.325v1.195S3.55 14.96 3.55 17.474c0 .665.17 1.025.396 1.025.116 0 .263-.072.42-.216-.156.535-.216 1.043-.216 1.477 0 .88.356 1.366.96 1.366.346 0 .75-.176 1.17-.496-.072.478-.1.906-.1 1.273 0 1.09.506 1.644 1.336 1.644.546 0 1.19-.27 1.822-.746A4.63 4.63 0 0 0 12 24c.637 0 1.276-.2 1.858-.6.633.477 1.277.747 1.823.747.83 0 1.336-.555 1.336-1.644 0-.367-.028-.795-.1-1.273.42.32.824.496 1.17.496.604 0 .96-.487.96-1.366 0-.434-.06-.942-.216-1.477.157.144.304.216.42.216.227 0 .396-.36.396-1.025 0-2.514-2.163-6.954-2.163-6.954V9.325C18.293 3.364 14.268 2 12.003 2zM5.785 5.991c.642 0 1.162.529 1.162 1.18a1.17 1.17 0 0 1-1.162 1.178A1.17 1.17 0 0 1 4.623 7.17c0-.651.52-1.18 1.162-1.18zm5.813 0c.642 0 1.162.529 1.162 1.18a1.17 1.17 0 0 1-1.162 1.178 1.17 1.17 0 0 1-1.162-1.178c0-.651.52-1.18 1.162-1.18z" />
                          </svg>
                        </div>
                      </div>
                    </div>
                    <div className="absolute inset-0 bg-gray-500/60 rounded-xl flex items-center justify-center">
                      <span className="text-white text-xl font-bold">即将上线</span>
                    </div>
                  </div>
                  <p className="text-xs text-gray-500 mt-3 font-medium">请使用QQ扫描二维码登录</p>
                </div>
              </div>
            )}

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
