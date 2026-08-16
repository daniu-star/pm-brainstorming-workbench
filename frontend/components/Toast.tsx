"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

export type ToastKind = "success" | "error";

interface ToastItem {
  id: number;
  kind: ToastKind;
  message: string;
}

export interface ToastApi {
  success: (message: string) => void;
  error: (message: string) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

type PushFn = (kind: ToastKind, message: string) => void;

// Module-level bridge so non-React modules (e.g. zustand store) can toast.
let externalPush: PushFn | null = null;

export const toast: ToastApi = {
  success: (message) => externalPush?.("success", message),
  error: (message) => externalPush?.("error", message),
};

export function useToast(): ToastApi {
  return useContext(ToastContext) ?? toast;
}

const TOAST_DURATION_MS = 3000;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const nextIdRef = useRef(1);

  const push = useCallback<PushFn>((kind, message) => {
    const id = nextIdRef.current++;
    setItems((current) => [...current, { id, kind, message }]);
    window.setTimeout(() => {
      setItems((current) => current.filter((item) => item.id !== id));
    }, TOAST_DURATION_MS);
  }, []);

  useEffect(() => {
    externalPush = push;
    return () => {
      externalPush = null;
    };
  }, [push]);

  const api = useMemo<ToastApi>(
    () => ({
      success: (message: string) => push("success", message),
      error: (message: string) => push("error", message),
    }),
    [push]
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div
        className="pointer-events-none fixed left-1/2 top-4 z-[100] flex w-max max-w-[92vw] -translate-x-1/2 flex-col items-center gap-2"
        role="status"
        aria-live="polite"
      >
        {items.map((item) => (
          <div
            key={item.id}
            className={`msg-enter rounded-lg border px-4 py-2.5 text-sm shadow-lg backdrop-blur ${
              item.kind === "success"
                ? "border-emerald-400/30 bg-emerald-950/85 text-emerald-200"
                : "border-red-400/30 bg-red-950/85 text-red-200"
            }`}
          >
            {item.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
