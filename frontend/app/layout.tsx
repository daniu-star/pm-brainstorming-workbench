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
      <body className="min-h-screen bg-warm-50 text-warm-600 antialiased">
        <ErrorBoundary>
          {children}
        </ErrorBoundary>
        <ToastContainer />
      </body>
    </html>
  );
}
