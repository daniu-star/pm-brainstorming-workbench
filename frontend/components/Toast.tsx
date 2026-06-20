"use client";

// 统一 Toast 系统：基于 shadcn/ui 的 toast
// 保持调用方 `import { toast } from "@/components/Toast"` 零侵入迁移
// 旧版 toast(type, message) 签名 → 自动转换为新签名

import { toast as shadcnToast } from "@/components/ui/use-toast";
import type { ToastProps } from "@/components/ui/toast";

type LegacyToastType = "success" | "error" | "warning" | "info";

const VARIANT_MAP: Record<LegacyToastType, "default" | "destructive"> = {
  success: "default",
  error: "destructive",
  warning: "default",
  info: "default",
};

/**
 * 兼容旧版 toast(type, message) 和新版 toast({ title, description, variant }) 调用。
 * shadcn toast 是模块级单例，直接调用即可。
 */
export function toast(typeOrProps: LegacyToastType | Record<string, unknown>, message?: string) {
  if (typeof typeOrProps === "string") {
    // 旧签名: toast("success", "消息")
    if (!message || message === "1 error" || message === "undefined") return;
    shadcnToast({
      title: message,
      variant: VARIANT_MAP[typeOrProps],
    });
  } else {
    // 新签名: toast({ title, description, variant })
    shadcnToast(typeOrProps as Parameters<typeof shadcnToast>[0]);
  }
}

export { useToast } from "@/components/ui/use-toast";

// 向后兼容：旧代码可能 import { ToastContainer }
export function ToastContainer() {
  // shadcn Toaster 已在 layout.tsx 挂载，这里返回 null
  return null;
}
