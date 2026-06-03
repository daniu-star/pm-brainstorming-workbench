"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { useSessionStore } from "@/store/sessionStore";
import { api } from "@/lib/api";
import { toast } from "@/components/Toast";
import { WalletIcon, CloseIcon, SpinnerIcon } from "@/components/icons";

type TierKey = "standard" | "professional" | "flagship";

interface RechargeResult {
  id: string;
  verify_code: string;
  tier_name: string;
  tokens: number;
  price: number;
  status: "pending" | "pending_review" | "approved" | "rejected" | "cancelled";
}

type Step = 1 | 2 | 3;

const TIERS = [
  {
    name: "标准版",
    tierKey: "standard" as TierKey,
    price: "10",
    priceValue: 10,
    tokens: "200,000",
    tokensNum: 200000,
    desc: "深度研讨，更多可能",
    features: [
      "基础版全部功能",
      "深度研讨模式 — AI 回复更详尽",
      "讨论轮次上限提升至 20 轮",
      "功能树自动导出 Markdown",
    ],
    highlight: false,
    badge: "",
  },
  {
    name: "专业版",
    tierKey: "professional" as TierKey,
    price: "30",
    priceValue: 30,
    tokens: "500,000",
    tokensNum: 500000,
    desc: "专业分析，全面覆盖",
    features: [
      "标准版全部功能",
      "高级角色解锁 — 数据分析师、增长黑客",
      "竞品对比分析报告",
      "讨论记录导出 PDF",
      "优先响应速度",
    ],
    highlight: true,
    badge: "推荐",
  },
  {
    name: "旗舰版",
    tierKey: "flagship" as TierKey,
    price: "50",
    priceValue: 50,
    tokens: "1,000,000",
    tokensNum: 1000000,
    desc: "极致体验，无限探索",
    features: [
      "专业版全部功能",
      "自定义角色 — 创建专属 AI 专家",
      "产品 PRD 一键生成",
      "多方案对比推演",
      "专属客服支持",
    ],
    highlight: false,
    badge: "",
  },
];

const STATUS_LABEL: Record<RechargeResult["status"], string> = {
  pending: "待付款",
  pending_review: "待审核",
  approved: "已通过",
  rejected: "已拒绝",
  cancelled: "已取消",
};

const STATUS_STYLE: Record<RechargeResult["status"], string> = {
  pending: "text-amber-600 bg-amber-50 border-amber-200",
  pending_review: "text-amber-600 bg-amber-50 border-amber-200",
  approved: "text-emerald-600 bg-emerald-50 border-emerald-200",
  rejected: "text-red-600 bg-red-50 border-red-200",
  cancelled: "text-warm-400 bg-warm-50 border-warm-200",
};

export function RechargeModal() {
  const isOpen = useSessionStore((s) => s.isRechargeOpen);
  const setRechargeOpen = useSessionStore((s) => s.setRechargeOpen);
  const setSettingsOpen = useSessionStore((s) => s.setSettingsOpen);
  const tokenQuota = useSessionStore((s) => s.tokenQuota);
  const tokensUsed = useSessionStore((s) => s.tokensUsed);
  const userApiKey = useSessionStore((s) => s.userApiKey);
  const refreshAfterRecharge = useSessionStore((s) => s.refreshAfterRecharge);

  const [step, setStep] = useState<Step>(1);
  const [selectedTier, setSelectedTier] = useState<TierKey | null>(null);
  const [rechargeResult, setRechargeResult] = useState<RechargeResult | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [checking, setChecking] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [history, setHistory] = useState<RechargeResult[]>([]);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const closeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const rechargeResultRef = useRef<RechargeResult | null>(null);

  const [confirming, setConfirming] = useState(false);
  const [qrError, setQrError] = useState(false);

  const remaining = (tokenQuota ?? 0) - (tokensUsed ?? 0);
  const isByok = !!userApiKey;

  useEffect(() => {
    rechargeResultRef.current = rechargeResult;
  }, [rechargeResult]);

  const cleanup = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
    if (closeTimerRef.current) {
      clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
  }, []);

  const resetState = useCallback(() => {
    setStep(1);
    setSelectedTier(null);
    setRechargeResult(null);
    setSubmitting(false);
    setCancelling(false);
    setChecking(false);
    cleanup();
  }, [cleanup]);

  useEffect(() => {
    if (!isOpen) {
      resetState();
    }
  }, [isOpen, resetState]);

  useEffect(() => {
    if (!isOpen) return;
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === "Escape") setRechargeOpen(false);
    };
    document.addEventListener("keydown", handleEscape);
    return () => document.removeEventListener("keydown", handleEscape);
  }, [isOpen, setRechargeOpen]);

  useEffect(() => {
    return cleanup;
  }, [cleanup]);

  useEffect(() => {
    if (!isOpen) return;
    let cancelled = false;
    setRestoring(true);
    (async () => {
      try {
        const data = await api<{ recharge: RechargeResult | null }>("/api/recharge/latest");
        const latest = data.recharge;
        if (cancelled) return;
        if (latest && (latest.status === "pending" || latest.status === "pending_review")) {
          setRechargeResult(latest);
          setStep(3);
          rechargeResultRef.current = latest;
        }
      } catch {
        // silent
      } finally {
        if (!cancelled) setRestoring(false);
      }
      try {
        const data = await api<{ recharges: RechargeResult[] }>("/api/recharge/status");
        if (!cancelled) {
          setHistory(data.recharges.slice(0, 3));
        }
      } catch {
        // silent
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [isOpen]);

  const checkStatus = useCallback(async () => {
    const current = rechargeResultRef.current;
    if (!current || (current.status !== "pending" && current.status !== "pending_review")) return;
    setChecking(true);
    try {
      const data = await api<{ recharges: RechargeResult[] }>("/api/recharge/status");
      const match = data.recharges.find((r) => r.id === current.id);
      if (match) {
        setRechargeResult(match);
        if (match.status === "approved") {
          await refreshAfterRecharge();
          closeTimerRef.current = setTimeout(() => {
            setRechargeOpen(false);
          }, 2000);
        }
      }
      setHistory(data.recharges.slice(0, 3));
    } catch {
      // silent
    } finally {
      setChecking(false);
    }
  }, [refreshAfterRecharge, setRechargeOpen]);

  useEffect(() => {
    if (rechargeResult?.status === "pending" || rechargeResult?.status === "pending_review") {
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = setInterval(checkStatus, 5000);
      return () => {
        if (pollRef.current) {
          clearInterval(pollRef.current);
          pollRef.current = null;
        }
      };
    }
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, [rechargeResult?.status, checkStatus]);

  const handleSubmit = async (tierKey: TierKey) => {
    setSelectedTier(tierKey);
    setSubmitting(true);
    try {
      const result = await api<RechargeResult>("/api/recharge/submit", {
        method: "POST",
        body: JSON.stringify({ tier: tierKey }),
      });
      setRechargeResult(result);
      setStep(2);
      toast("info", "请扫码付款并在备注中填写验证码");
    } catch (err) {
      toast("error", `提交失败：${err instanceof Error ? err.message : "请稍后重试"}`);
    } finally {
      setSubmitting(false);
    }
  };

  const handlePaid = async () => {
    if (!rechargeResult) return;
    setConfirming(true);
    try {
      await api(`/api/recharge/confirm/${rechargeResult.id}`, { method: "POST" });
      setRechargeResult({ ...rechargeResult, status: "pending_review" });
      rechargeResultRef.current = { ...rechargeResult, status: "pending_review" };
      setStep(3);
      toast("info", "已确认付款，等待管理员审核");
    } catch (err) {
      setStep(3);
      toast("error", `确认失败：${err instanceof Error ? err.message : "请稍后重试"}`);
    } finally {
      setConfirming(false);
    }
  };

  const handleCancel = async () => {
    if (!rechargeResult) return;
    setCancelling(true);
    try {
      await api(`/api/recharge/cancel/${rechargeResult.id}`, { method: "POST" });
      toast("info", "充值已取消");
      resetState();
    } catch (err) {
      toast("error", `取消失败：${err instanceof Error ? err.message : "请稍后重试"}`);
    } finally {
      setCancelling(false);
    }
  };

  const handleCopyCode = () => {
    if (!rechargeResult?.verify_code) return;
    navigator.clipboard.writeText(rechargeResult.verify_code).then(
      () => toast("info", "验证码已复制"),
      () => toast("error", "复制失败，请手动选择复制"),
    );
  };

  const handleNewRecharge = () => {
    setRechargeResult(null);
    setStep(1);
    setSelectedTier(null);
    cleanup();
  };

  if (!isOpen) return null;

  const TERMINAL_STATUSES: RechargeResult["status"][] = ["approved", "rejected", "cancelled"];
  const isTerminal = !!rechargeResult?.status && TERMINAL_STATUSES.includes(rechargeResult.status);

  return (
    <>
      <div className="fixed inset-0 bg-warm-600/30 backdrop-blur-sm z-40" onClick={() => setRechargeOpen(false)} aria-hidden="true" />
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="recharge-title"
        className="fixed top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[560px] max-w-[95vw] max-h-[90vh] bg-white border border-warm-200 rounded-2xl z-50 shadow-xl overflow-y-auto"
      >
        <div className="p-6">
          <div className="flex items-center justify-between mb-5">
            <div className="flex items-center gap-2">
              <WalletIcon size={24} className="text-amber-500" />
              <div>
                <h2 id="recharge-title" className="text-lg font-semibold text-warm-600">充值额度</h2>
                <p className="text-xs text-warm-500 mt-1">选择适合你的方案，解锁更多深度功能</p>
              </div>
            </div>
            <button
              onClick={() => setRechargeOpen(false)}
              aria-label="关闭充值"
              className="text-warm-500 hover:text-warm-600 active:text-warm-700 transition-all duration-200 min-h-[44px] min-w-[44px] flex items-center justify-center focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none rounded"
            >
              <CloseIcon size={16} />
            </button>
          </div>

          <div className="mb-5 p-4 bg-warm-50 rounded-xl border border-warm-200 flex items-center justify-between">
            <div>
              <div className="text-sm text-warm-500 mb-0.5">当前剩余额度</div>
              <div className="text-2xl font-bold text-warm-600">
                {isByok ? "无限" : remaining.toLocaleString()} <span className="text-sm font-normal text-warm-500">tokens</span>
              </div>
            </div>
            {isByok && (
              <span className="text-xs text-emerald-600 flex items-center gap-1.5 px-3 py-1.5 bg-emerald-50 rounded-full border border-emerald-200">
                <span className="w-1.5 h-1.5 bg-emerald-500 rounded-full" />
                自带 Key 模式
              </span>
            )}
          </div>

          <div className="flex items-center justify-center gap-2 mb-6">
            {([1, 2, 3] as const).map((s, i) => (
              <div key={s} className="flex items-center">
                <div
                  className={`w-8 h-8 rounded-full flex items-center justify-center text-xs font-semibold transition-all duration-300 ${
                    step > s
                      ? "bg-amber-500 text-white"
                      : step === s
                      ? "bg-amber-500 text-white ring-4 ring-amber-500/20"
                      : "bg-warm-100 text-warm-400"
                  }`}
                >
                  {step > s ? (
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><path d="M20 6L9 17l-5-5" /></svg>
                  ) : (
                    s
                  )}
                </div>
                {i < 2 && (
                  <div className={`w-12 h-0.5 mx-1 transition-colors duration-300 ${step > s ? "bg-amber-500" : "bg-warm-200"}`} />
                )}
              </div>
            ))}
          </div>

          <div className="flex items-center justify-center gap-4 mb-6 text-xs">
            <span className={step >= 1 ? "text-amber-600 font-medium" : "text-warm-500"}>选择套餐</span>
            <span className={step >= 2 ? "text-amber-600 font-medium" : "text-warm-500"}>扫码付款</span>
            <span className={step >= 3 ? "text-amber-600 font-medium" : "text-warm-500"}>等待确认</span>
          </div>

          {restoring ? (
            <div className="flex items-center justify-center py-12 text-sm text-warm-500">
              <SpinnerIcon size={20} className="text-amber-500 mr-2" />
              恢复充值状态...
            </div>
          ) : (
            <>
              {step === 1 && (
                <div className="grid grid-cols-3 gap-3 mb-6">
                  {TIERS.map((tier) => (
                    <div
                      key={tier.tierKey}
                      onClick={() => setSelectedTier(tier.tierKey)}
                      role="button"
                      tabIndex={0}
                      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setSelectedTier(tier.tierKey); } }}
                      className={`relative p-4 rounded-xl border transition-all duration-200 cursor-pointer ${
                        selectedTier === tier.tierKey
                          ? "bg-amber-50 border-amber-400 shadow-md scale-[1.02]"
                          : tier.highlight
                          ? "bg-amber-50/50 border-amber-300 hover:border-amber-400"
                          : "bg-warm-50 border-warm-200 hover:border-warm-300"
                      }`}
                    >
                      {tier.badge && (
                        <div className="absolute -top-2.5 right-4 px-2.5 py-0.5 btn-gradient text-xs font-semibold rounded-full">
                          {tier.badge}
                        </div>
                      )}
                      <div className="flex items-baseline gap-0.5 mb-1">
                        <span className="text-sm font-bold text-amber-700">¥</span>
                        <span className="text-lg font-bold text-warm-600">{tier.price}</span>
                        <span className="text-xs text-warm-500">/次</span>
                      </div>
                      <div className="text-xs text-warm-500 mb-0.5">{tier.name}</div>
                      <div className="text-xs text-amber-700 font-medium mb-3">{tier.tokens} tokens · {tier.desc}</div>
                      <ul className="space-y-1.5">
                        {tier.features.map((f) => (
                          <li key={f} className="flex items-start gap-2 text-xs text-warm-600">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke={selectedTier === tier.tierKey ? "#f59e0b" : tier.highlight ? "#f59e0b" : "#8b6f47"} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="shrink-0 mt-0.5">
                              <path d="M20 6L9 17l-5-5" />
                            </svg>
                            {f}
                          </li>
                        ))}
                      </ul>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleSubmit(tier.tierKey);
                        }}
                        disabled={submitting}
                        className="mt-3 w-full py-2 text-xs btn-gradient disabled:cursor-not-allowed focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none"
                      >
                        {submitting ? "提交中..." : "选择此套餐"}
                      </button>
                    </div>
                  ))}
                </div>
              )}

              {step === 2 && rechargeResult && (
                <div className="mb-6 p-4 bg-warm-50 rounded-xl border border-warm-200">
                  <div className="flex items-start gap-4">
                    <div className="w-36 h-36 shrink-0">
                      {qrError ? (
                        <div className="w-full h-full rounded-lg border border-warm-200 bg-warm-50 flex items-center justify-center text-sm text-warm-500">
                          请扫码付款
                        </div>
                      ) : (
                        <img
                          src="/qrcode.svg"
                          alt="微信收款码"
                          className="w-full h-full object-cover rounded-lg border border-warm-200"
                          onError={() => setQrError(true)}
                        />
                      )}
                    </div>
                    <div className="flex-1">
                      <div className="text-sm text-warm-600 font-medium mb-2">扫码付款</div>
                      <div className="mb-3 p-3 bg-white rounded-lg border-2 border-amber-300 shadow-sm">
                        <div className="text-xs text-warm-500 mb-1">验证码（请填写在付款备注中）</div>
                        <div className="flex items-center gap-2">
                          <div className="text-3xl font-bold font-mono text-amber-600 tracking-widest select-all">
                            {rechargeResult.verify_code}
                          </div>
                          <button
                            onClick={handleCopyCode}
                            className="shrink-0 p-1.5 rounded-md bg-amber-50 border border-amber-200 text-amber-600 hover:bg-amber-100 active:bg-amber-200 transition-all duration-200 min-h-[44px] min-w-[44px] flex items-center justify-center focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none"
                            aria-label="复制验证码"
                          >
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                              <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                              <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1" />
                            </svg>
                          </button>
                        </div>
                      </div>
                      <div className="text-xs text-warm-500 mb-3">
                        套餐：{rechargeResult.tier_name} · {(rechargeResult.tokens ?? 0).toLocaleString()} tokens · ¥{rechargeResult.price ?? 0}
                      </div>
                      <div className="p-2.5 bg-amber-50 border border-amber-200 rounded-lg text-xs text-amber-600 mb-3">
                        请使用微信扫码付款，务必在付款备注中填写上方验证码
                      </div>
                      <button
                        onClick={handlePaid}
                        disabled={confirming}
                        className="w-full py-2.5 text-sm btn-gradient disabled:cursor-not-allowed focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none"
                      >
                        {confirming ? "确认中..." : "我已付款"}
                      </button>
                    </div>
                  </div>
                </div>
              )}

              {step === 3 && rechargeResult && (
                <div className="mb-6 p-4 bg-warm-50 rounded-xl border border-warm-200">
                  <div className="flex items-start gap-4">
                    <div className="w-36 h-36 shrink-0">
                      {qrError ? (
                        <div className="w-full h-full rounded-lg border border-warm-200 bg-warm-50 flex items-center justify-center text-sm text-warm-500">
                          请扫码付款
                        </div>
                      ) : (
                        <img
                          src="/qrcode.svg"
                          alt="微信收款码"
                          className="w-full h-full object-cover rounded-lg border border-warm-200"
                          onError={() => setQrError(true)}
                        />
                      )}
                    </div>
                    <div className="flex-1">
                      <div className="mb-3">
                        <div className="text-sm text-warm-600 font-medium mb-2">充值状态</div>
                        <div className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-full border text-xs font-medium ${STATUS_STYLE[rechargeResult.status]}`}>
                          {rechargeResult.status === "pending" && (
                            <SpinnerIcon size={16} className="text-amber-500" />
                          )}
                          {rechargeResult.status === "pending_review" && (
                            <SpinnerIcon size={16} className="text-blue-500" />
                          )}
                          {rechargeResult.status === "approved" && (
                            <svg className="w-4 h-4 text-emerald-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                              <path d="M20 6L9 17l-5-5" />
                            </svg>
                          )}
                          {rechargeResult.status === "rejected" && (
                            <svg className="w-4 h-4 text-red-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                              <path d="M18 6L6 18M6 6l12 12" />
                            </svg>
                          )}
                          {rechargeResult.status === "cancelled" && (
                            <svg className="w-4 h-4 text-warm-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                              <circle cx="12" cy="12" r="10" />
                              <path d="M8 12h8" />
                            </svg>
                          )}
                          {STATUS_LABEL[rechargeResult.status]}
                        </div>
                      </div>

                      <div className="mb-3 p-3 bg-white rounded-lg border-2 border-amber-300 shadow-sm">
                        <div className="text-xs text-warm-500 mb-1">验证码</div>
                        <div className="flex items-center gap-2">
                          <div className="text-3xl font-bold font-mono text-amber-600 tracking-widest select-all">
                            {rechargeResult.verify_code}
                          </div>
                          <button
                            onClick={handleCopyCode}
                            className="shrink-0 p-1.5 rounded-md bg-amber-50 border border-amber-200 text-amber-600 hover:bg-amber-100 active:bg-amber-200 transition-all duration-200 min-h-[44px] min-w-[44px] flex items-center justify-center focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none"
                            aria-label="复制验证码"
                          >
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                              <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                              <path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1" />
                            </svg>
                          </button>
                        </div>
                      </div>

                      <div className="text-xs text-warm-500 mb-3">
                        套餐：{rechargeResult.tier_name} · {(rechargeResult.tokens ?? 0).toLocaleString()} tokens · ¥{rechargeResult.price ?? 0}
                      </div>

                      {rechargeResult.status === "pending" && (
                        <div className="flex items-center gap-2">
                          <button
                            onClick={checkStatus}
                            disabled={checking}
                            className="px-4 py-2 text-xs font-medium rounded-lg bg-amber-50 text-amber-600 border border-amber-200 hover:bg-amber-100 active:bg-amber-200 transition-all duration-200 disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none"
                          >
                            {checking ? "查询中..." : "查询状态"}
                          </button>
                          <button
                            onClick={handleCancel}
                            disabled={cancelling}
                            className="px-4 py-2 text-xs font-medium rounded-lg bg-warm-50 text-warm-500 border border-warm-200 hover:bg-warm-100 hover:text-warm-600 active:bg-warm-200 transition-all duration-200 disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-warm-500/50 focus-visible:outline-none"
                          >
                            {cancelling ? "取消中..." : "取消充值"}
                          </button>
                        </div>
                      )}

                      {rechargeResult.status === "pending_review" && (
                        <div className="space-y-2">
                          <div className="p-2.5 bg-amber-50 border border-amber-200 rounded-lg text-xs text-amber-600 flex items-center gap-2">
                            <SpinnerIcon size={16} className="text-amber-500" />
                            等待审核中...
                          </div>
                          <button
                            onClick={checkStatus}
                            disabled={checking}
                            className="px-4 py-2 text-xs font-medium rounded-lg bg-amber-50 text-amber-600 border border-amber-200 hover:bg-amber-100 active:bg-amber-200 transition-all duration-200 disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none"
                          >
                            {checking ? "查询中..." : "查询审核状态"}
                          </button>
                        </div>
                      )}

                      {rechargeResult.status === "approved" && (
                        <div className="p-2.5 bg-emerald-50 border border-emerald-200 rounded-lg text-xs text-emerald-600">
                          充值成功！额度已到账，窗口将自动关闭
                        </div>
                      )}

                      {rechargeResult.status === "rejected" && (
                        <div className="space-y-2">
                          <div className="p-2.5 bg-red-50 border border-red-200 rounded-lg text-xs text-red-600">
                            充值被拒绝，请确认付款金额与套餐一致后重新提交
                          </div>
                          <button
                            onClick={handleNewRecharge}
                            className="px-4 py-2 text-xs btn-gradient focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none"
                          >
                            重新充值
                          </button>
                        </div>
                      )}

                      {rechargeResult.status === "cancelled" && (
                        <div className="space-y-2">
                          <div className="p-2.5 bg-warm-50 border border-warm-200 rounded-lg text-xs text-warm-500">
                            充值已取消
                          </div>
                          <button
                            onClick={handleNewRecharge}
                            className="px-4 py-2 text-xs btn-gradient focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none"
                          >
                            重新充值
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              )}
            </>
          )}

          {history.length > 0 && (
            <div className="mb-5">
              <div className="text-xs text-warm-500 font-medium mb-2">最近充值记录</div>
              <div className="space-y-1.5">
                {history.map((r) => (
                  <div
                    key={r.id}
                    className="flex items-center justify-between px-3 py-2 bg-warm-50 rounded-lg border border-warm-200 text-xs"
                  >
                    <div className="flex items-center gap-3 text-warm-600">
                      <span className="font-medium">{r.tier_name}</span>
                      <span className="text-warm-500">{(r.tokens ?? 0).toLocaleString()} tokens</span>
                      <span className="text-warm-500">¥{r.price ?? 0}</span>
                    </div>
                    <div className="flex items-center gap-3">
                      <span className={`inline-flex items-center px-2 py-0.5 rounded-full border text-xs font-medium ${STATUS_STYLE[r.status]}`}>
                        {STATUS_LABEL[r.status]}
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="flex items-center justify-center gap-2 text-xs text-warm-500">
            <span>不想充值？</span>
            <button
              onClick={() => {
                setRechargeOpen(false);
                setSettingsOpen(true);
              }}
              aria-label="配置自己的 API Key"
              className="text-amber-600 hover:text-amber-700 active:text-amber-800 transition-all duration-200 underline underline-offset-2 focus-visible:ring-2 focus-visible:ring-amber-500/50 focus-visible:outline-none rounded"
            >
              配置自己的 API Key
            </button>
            <span>即可无限使用</span>
          </div>
        </div>
      </div>
    </>
  );
}
