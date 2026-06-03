import type { Metadata } from "next";
import "./globals.css";
import { ToastContainer } from "@/components/Toast";
import { ErrorBoundary } from "@/components/ErrorBoundary";

export const metadata: Metadata = {
  title: "产品脑暴工作台",
  description: "AI 驱动的多角色产品头脑风暴与可视化画布工作台",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <head>
        <meta name="theme-color" content="#faf8f5" />
      </head>
      <body className="min-h-screen bg-warm-50 text-warm-600 antialiased">
        <a href="#main-content" className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-[100] focus:px-4 focus:py-2 focus:bg-amber-600 focus:text-white focus:rounded-lg focus:text-sm focus:font-semibold">
          Skip to content
        </a>
        <ErrorBoundary>
          {children}
        </ErrorBoundary>
        <ToastContainer />
      </body>
    </html>
  );
}
