"use client";

import { useState, useEffect, useCallback, useRef } from "react";

type ToastType = "success" | "error" | "warning" | "info";

interface Toast {
  id: string;
  type: ToastType;
  message: string;
  exiting?: boolean;
}

const TOAST_ICONS: Record<ToastType, { color: string; path: string }> = {
  success: {
    color: "text-emerald-600",
    path: "M20 6L9 17l-5-5",
  },
  error: {
    color: "text-red-600",
    path: "M18 6L6 18M6 6l12 12",
  },
  warning: {
    color: "text-amber-600",
    path: "M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z M12 9v4 M12 17h.01",
  },
  info: {
    color: "text-amber-600",
    path: "M12 16v-4 M12 8h.01 M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z",
  },
};

const BG_MAP: Record<ToastType, string> = {
  success: "bg-emerald-50 border-emerald-200",
  error: "bg-red-50 border-red-200",
  warning: "bg-amber-50 border-amber-200",
  info: "bg-amber-50 border-amber-200",
};

let addToastFn: ((type: ToastType, message: string) => void) | null = null;

export function toast(type: ToastType, message: string) {
  if (!message || message === "1 error" || message === "undefined") return;
  addToastFn?.(type, message);
}

export function ToastContainer() {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const timersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  const removeToast = useCallback((id: string) => {
    setToasts((prev) => prev.map((t) => (t.id === id ? { ...t, exiting: true } : t)));
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
      timersRef.current.delete(id);
    }, 250);
  }, []);

  const addToast = useCallback(
    (type: ToastType, message: string) => {
      const id = `${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
      setToasts((prev) => [...prev.slice(-4), { id, type, message }]);
      const timer = setTimeout(() => removeToast(id), 4000);
      timersRef.current.set(id, timer);
    },
    [removeToast]
  );

  useEffect(() => {
    addToastFn = addToast;
    return () => {
      addToastFn = null;
      timersRef.current.forEach((t) => clearTimeout(t));
    };
  }, [addToast]);

  if (toasts.length === 0) return null;

  return (
    <div
      aria-live="polite"
      aria-label="通知"
      className="fixed top-16 right-4 z-[60] flex flex-col gap-2 w-80 max-w-[calc(100vw-2rem)]"
    >
      {toasts.map((t) => {
        const icon = TOAST_ICONS[t.type];
        return (
          <div
            key={t.id}
            role="alert"
            className={`${t.exiting ? "toast-exit" : "toast-enter"} flex items-start gap-3 p-3 rounded-xl border backdrop-blur-sm ${BG_MAP[t.type]}`}
          >
            <svg
              width="18"
              height="18"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              className={`shrink-0 mt-0.5 ${icon.color}`}
            >
              <path d={icon.path} />
            </svg>
            <span className="text-sm text-warm-600 flex-1 leading-relaxed">{t.message}</span>
            <button
              onClick={() => removeToast(t.id)}
              className="shrink-0 text-warm-500 hover:text-warm-600 transition-colors min-h-[28px] min-w-[28px] flex items-center justify-center"
              aria-label="关闭通知"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M18 6L6 18M6 6l12 12" />
              </svg>
            </button>
          </div>
        );
      })}
    </div>
  );
}
