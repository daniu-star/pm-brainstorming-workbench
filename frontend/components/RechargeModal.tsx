"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { useSessionStore } from "@/store/sessionStore";
import { api } from "@/lib/api";
import { toast } from "@/components/Toast";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Wallet, Check, Loader2, Copy, RefreshCw } from "lucide-react";

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
  pending: "text-primary bg-primary/10 border-primary/30",
  pending_review: "text-primary bg-primary/10 border-primary/30",
  approved: "text-emerald-600 bg-emerald-50 border-emerald-200",
  rejected: "text-destructive bg-destructive/10 border-destructive/30",
  cancelled: "text-muted-foreground bg-muted border-border",
};

const STATUS_BADGE_VARIANT: Record<RechargeResult["status"], "default" | "secondary" | "destructive"> = {
  pending: "secondary",
  pending_review: "secondary",
  approved: "default",
  rejected: "destructive",
  cancelled: "secondary",
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

  const TERMINAL_STATUSES: RechargeResult["status"][] = ["approved", "rejected", "cancelled"];
  const isTerminal = !!rechargeResult?.status && TERMINAL_STATUSES.includes(rechargeResult.status);

  return (
    <Dialog open={isOpen} onOpenChange={setRechargeOpen}>
      <DialogContent className="max-w-[560px] max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Wallet className="h-5 w-5 text-primary" />
            充值额度
          </DialogTitle>
          <DialogDescription>
            选择适合你的方案，解锁更多深度功能
          </DialogDescription>
        </DialogHeader>

        <div className="p-4 bg-muted/50 rounded-xl border border-border flex items-center justify-between">
          <div>
            <div className="text-sm text-muted-foreground mb-0.5">当前剩余额度</div>
            <div className="text-2xl font-bold text-foreground">
              {isByok ? "无限" : remaining.toLocaleString()} <span className="text-sm font-normal text-muted-foreground">tokens</span>
            </div>
          </div>
          {isByok && (
            <span className="text-xs text-emerald-600 flex items-center gap-1.5 px-3 py-1.5 bg-emerald-50 rounded-full border border-emerald-200">
              <span className="w-1.5 h-1.5 bg-emerald-500 rounded-full" />
              自带 Key 模式
            </span>
          )}
        </div>

        <div className="flex items-center justify-center gap-2">
          {([1, 2, 3] as const).map((s, i) => (
            <div key={s} className="flex items-center">
              <div
                className={`w-8 h-8 rounded-full flex items-center justify-center text-xs font-semibold transition-all duration-300 ${
                  step > s
                    ? "bg-primary text-primary-foreground"
                    : step === s
                    ? "bg-primary text-primary-foreground ring-4 ring-primary/20"
                    : "bg-muted text-muted-foreground"
                }`}
              >
                {step > s ? (
                  <Check className="h-3.5 w-3.5" strokeWidth={3} />
                ) : (
                  s
                )}
              </div>
              {i < 2 && (
                <div className={`w-12 h-0.5 mx-1 transition-colors duration-300 ${step > s ? "bg-primary" : "bg-border"}`} />
              )}
            </div>
          ))}
        </div>

        <div className="flex items-center justify-center gap-4 text-xs">
          <span className={step >= 1 ? "text-primary font-medium" : "text-muted-foreground"}>选择套餐</span>
          <span className={step >= 2 ? "text-primary font-medium" : "text-muted-foreground"}>扫码付款</span>
          <span className={step >= 3 ? "text-primary font-medium" : "text-muted-foreground"}>等待确认</span>
        </div>

        {restoring ? (
          <div className="flex items-center justify-center py-12 text-sm text-muted-foreground">
            <Loader2 className="h-5 w-5 text-primary mr-2 animate-spin" />
            恢复充值状态...
          </div>
        ) : (
          <>
            {step === 1 && (
              <div className="grid grid-cols-3 gap-3">
                {TIERS.map((tier) => (
                  <Card
                    key={tier.tierKey}
                    onClick={() => setSelectedTier(tier.tierKey)}
                    role="button"
                    tabIndex={0}
                    onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setSelectedTier(tier.tierKey); } }}
                    className={`relative p-4 cursor-pointer transition-all duration-200 shadow-none ${
                      selectedTier === tier.tierKey
                        ? "bg-primary/10 border-primary shadow-md scale-[1.02]"
                        : tier.highlight
                        ? "bg-primary/5 border-primary/40 hover:border-primary"
                        : "bg-muted/50 border-border hover:border-primary/40"
                    }`}
                  >
                    <CardContent className="p-0">
                      {tier.badge && (
                        <div className="absolute -top-2.5 right-4">
                          <Badge variant="default" className="text-xs">{tier.badge}</Badge>
                        </div>
                      )}
                      <div className="flex items-baseline gap-0.5 mb-1">
                        <span className="text-sm font-bold text-primary">¥</span>
                        <span className="text-lg font-bold text-foreground">{tier.price}</span>
                        <span className="text-xs text-muted-foreground">/次</span>
                      </div>
                      <div className="text-xs text-muted-foreground mb-0.5">{tier.name}</div>
                      <div className="text-xs text-primary font-medium mb-3">{tier.tokens} tokens · {tier.desc}</div>
                      <ul className="space-y-1.5">
                        {tier.features.map((f) => (
                          <li key={f} className="flex items-start gap-2 text-xs text-foreground">
                            <Check
                              className="h-3.5 w-3.5 shrink-0 mt-0.5"
                              strokeWidth={2}
                            />
                            {f}
                          </li>
                        ))}
                      </ul>
                      <Button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleSubmit(tier.tierKey);
                        }}
                        disabled={submitting}
                        size="sm"
                        className="mt-3 w-full"
                      >
                        {submitting ? "提交中..." : "选择此套餐"}
                      </Button>
                    </CardContent>
                  </Card>
                ))}
              </div>
            )}

            {step === 2 && rechargeResult && (
              <div className="p-4 bg-muted/50 rounded-xl border border-border">
                <div className="flex items-start gap-4">
                  <div className="w-36 h-36 shrink-0">
                    {qrError ? (
                      <div className="w-full h-full rounded-lg border border-border bg-muted/50 flex items-center justify-center text-sm text-muted-foreground">
                        请扫码付款
                      </div>
                    ) : (
                      <img
                        src="/qrcode.svg"
                        alt="微信收款码"
                        className="w-full h-full object-cover rounded-lg border border-border"
                        onError={() => setQrError(true)}
                      />
                    )}
                  </div>
                  <div className="flex-1">
                    <div className="text-sm text-foreground font-medium mb-2">扫码付款</div>
                    <div className="mb-3 p-3 bg-background rounded-lg border-2 border-primary/40 shadow-sm">
                      <div className="text-xs text-muted-foreground mb-1">验证码（请填写在付款备注中）</div>
                      <div className="flex items-center gap-2">
                        <div className="text-3xl font-bold font-mono text-primary tracking-widest select-all">
                          {rechargeResult.verify_code}
                        </div>
                        <Button
                          onClick={handleCopyCode}
                          size="icon"
                          variant="outline"
                          className="shrink-0 h-9 w-9"
                          aria-label="复制验证码"
                        >
                          <Copy className="h-4 w-4" />
                        </Button>
                      </div>
                    </div>
                    <div className="text-xs text-muted-foreground mb-3">
                      套餐：{rechargeResult.tier_name} · {(rechargeResult.tokens ?? 0).toLocaleString()} tokens · ¥{rechargeResult.price ?? 0}
                    </div>
                    <div className="p-2.5 bg-primary/10 border border-primary/30 rounded-lg text-xs text-primary mb-3">
                      请使用微信扫码付款，务必在付款备注中填写上方验证码
                    </div>
                    <Button
                      onClick={handlePaid}
                      disabled={confirming}
                      className="w-full"
                    >
                      {confirming ? "确认中..." : "我已付款"}
                    </Button>
                  </div>
                </div>
              </div>
            )}

            {step === 3 && rechargeResult && (
              <div className="p-4 bg-muted/50 rounded-xl border border-border">
                <div className="flex items-start gap-4">
                  <div className="w-36 h-36 shrink-0">
                    {qrError ? (
                      <div className="w-full h-full rounded-lg border border-border bg-muted/50 flex items-center justify-center text-sm text-muted-foreground">
                        请扫码付款
                      </div>
                    ) : (
                      <img
                        src="/qrcode.svg"
                        alt="微信收款码"
                        className="w-full h-full object-cover rounded-lg border border-border"
                        onError={() => setQrError(true)}
                      />
                    )}
                  </div>
                  <div className="flex-1">
                    <div className="mb-3">
                      <div className="text-sm text-foreground font-medium mb-2">充值状态</div>
                      <Badge variant={STATUS_BADGE_VARIANT[rechargeResult.status]} className="gap-1.5">
                        {rechargeResult.status === "pending" && (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        )}
                        {rechargeResult.status === "pending_review" && (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        )}
                        {rechargeResult.status === "approved" && (
                          <Check className="h-3.5 w-3.5" />
                        )}
                        {rechargeResult.status === "rejected" && (
                          <span className="text-xs">✕</span>
                        )}
                        {rechargeResult.status === "cancelled" && (
                          <span className="text-xs">–</span>
                        )}
                        {STATUS_LABEL[rechargeResult.status]}
                      </Badge>
                    </div>

                    <div className="mb-3 p-3 bg-background rounded-lg border-2 border-primary/40 shadow-sm">
                      <div className="text-xs text-muted-foreground mb-1">验证码</div>
                      <div className="flex items-center gap-2">
                        <div className="text-3xl font-bold font-mono text-primary tracking-widest select-all">
                          {rechargeResult.verify_code}
                        </div>
                        <Button
                          onClick={handleCopyCode}
                          size="icon"
                          variant="outline"
                          className="shrink-0 h-9 w-9"
                          aria-label="复制验证码"
                        >
                          <Copy className="h-4 w-4" />
                        </Button>
                      </div>
                    </div>

                    <div className="text-xs text-muted-foreground mb-3">
                      套餐：{rechargeResult.tier_name} · {(rechargeResult.tokens ?? 0).toLocaleString()} tokens · ¥{rechargeResult.price ?? 0}
                    </div>

                    {rechargeResult.status === "pending" && (
                      <div className="flex items-center gap-2">
                        <Button
                          onClick={checkStatus}
                          disabled={checking}
                          variant="outline"
                          size="sm"
                        >
                          <RefreshCw className="h-3.5 w-3.5" />
                          {checking ? "查询中..." : "查询状态"}
                        </Button>
                        <Button
                          onClick={handleCancel}
                          disabled={cancelling}
                          variant="secondary"
                          size="sm"
                        >
                          {cancelling ? "取消中..." : "取消充值"}
                        </Button>
                      </div>
                    )}

                    {rechargeResult.status === "pending_review" && (
                      <div className="space-y-2">
                        <div className="p-2.5 bg-primary/10 border border-primary/30 rounded-lg text-xs text-primary flex items-center gap-2">
                          <Loader2 className="h-4 w-4 animate-spin" />
                          等待审核中...
                        </div>
                        <Button
                          onClick={checkStatus}
                          disabled={checking}
                          variant="outline"
                          size="sm"
                        >
                          <RefreshCw className="h-3.5 w-3.5" />
                          {checking ? "查询中..." : "查询审核状态"}
                        </Button>
                      </div>
                    )}

                    {rechargeResult.status === "approved" && (
                      <div className="p-2.5 bg-emerald-50 border border-emerald-200 rounded-lg text-xs text-emerald-600">
                        充值成功！额度已到账，窗口将自动关闭
                      </div>
                    )}

                    {rechargeResult.status === "rejected" && (
                      <div className="space-y-2">
                        <div className="p-2.5 bg-destructive/10 border border-destructive/30 rounded-lg text-xs text-destructive">
                          充值被拒绝，请确认付款金额与套餐一致后重新提交
                        </div>
                        <Button
                          onClick={handleNewRecharge}
                          size="sm"
                        >
                          重新充值
                        </Button>
                      </div>
                    )}

                    {rechargeResult.status === "cancelled" && (
                      <div className="space-y-2">
                        <div className="p-2.5 bg-muted border border-border rounded-lg text-xs text-muted-foreground">
                          充值已取消
                        </div>
                        <Button
                          onClick={handleNewRecharge}
                          size="sm"
                        >
                          重新充值
                        </Button>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            )}
          </>
        )}

        {history.length > 0 && (
          <div>
            <div className="text-xs text-muted-foreground font-medium mb-2">最近充值记录</div>
            <div className="space-y-1.5">
              {history.map((r) => (
                <div
                  key={r.id}
                  className="flex items-center justify-between px-3 py-2 bg-muted/50 rounded-lg border border-border text-xs"
                >
                  <div className="flex items-center gap-3 text-foreground">
                    <span className="font-medium">{r.tier_name}</span>
                    <span className="text-muted-foreground">{(r.tokens ?? 0).toLocaleString()} tokens</span>
                    <span className="text-muted-foreground">¥{r.price ?? 0}</span>
                  </div>
                  <div className="flex items-center gap-3">
                    <Badge variant={STATUS_BADGE_VARIANT[r.status]}>
                      {STATUS_LABEL[r.status]}
                    </Badge>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="flex items-center justify-center gap-2 text-xs text-muted-foreground">
          <span>不想充值？</span>
          <Button
            onClick={() => {
              setRechargeOpen(false);
              setSettingsOpen(true);
            }}
            variant="link"
            className="h-auto p-0 text-xs"
            aria-label="配置自己的 API Key"
          >
            配置自己的 API Key
          </Button>
          <span>即可无限使用</span>
        </div>
      </DialogContent>
    </Dialog>
  );
}
