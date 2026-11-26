"""第 42 批（P0）：API 基址同源化 —— 生产 bundle 曾把 API_BASE 编译成跨域 hf.space，
导致浏览器所有 JSON 写请求被 CORS 预检拦截（HF 代理不回 Access-Control-Allow-Credentials）。"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(".")
p = ROOT / "lib/api.ts"
src = p.read_text(encoding="utf-8")

old = 'export const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";'
assert src.count(old) == 1
new = '''/**
 * API 基址解析（第 42 批，线上登录 100% 失败的真因）。
 *
 * 线上 Vercel 项目的环境变量把 `NEXT_PUBLIC_API_URL` 覆盖成了
 * `https://123chenjunliang-pm-brainstorm-workbench.hf.space`，构建时被内联进 bundle，
 * 于是浏览器对 hf.space 发 JSON POST → 触发 CORS 预检 → HF 平台代理的预检响应
 * 没有 `Access-Control-Allow-Credentials` → 浏览器直接拒绝（表现为
 * `TypeError: Failed to fetch` / 「网络连接失败」）。这不是网络抖动，改重试也修不好。
 *
 * 因此生产环境（页面不是 localhost）一律走同源 `/backend`（由 next.config.js 的
 * rewrites 反代到 Space），只有本地开发才允许绝对地址（127.0.0.1:8001）。
 * 需要跨域直连的极少数场景用 `NEXT_PUBLIC_ALLOW_CROSS_ORIGIN_API=1` 显式放开。
 */
function resolveApiBase(): string {
  const configured = (process.env.NEXT_PUBLIC_API_URL ?? "").trim();
  const allowCrossOrigin = process.env.NEXT_PUBLIC_ALLOW_CROSS_ORIGIN_API === "1";
  const isAbsolute = /^https?:\\/\\//i.test(configured);

  if (!isAbsolute) {
    // 相对路径（/backend）或空值：直接使用；空值时线上同源、本地回退到 8001。
    return configured || (typeof window === "undefined" ? "/backend" : "http://127.0.0.1:8001");
  }

  if (typeof window === "undefined") return configured;
  const host = window.location.hostname;
  const isLocalPage = host === "localhost" || host === "127.0.0.1" || host === "::1";
  if (isLocalPage || allowCrossOrigin) return configured;

  // 生产页面 + 绝对基址 = 必然跨域，预检过不去：强制同源反代。
  return "/backend";
}

export const API_BASE = resolveApiBase();'''
src = src.replace(old, new)

p.write_text(src, encoding="utf-8", newline="")
print("patched lib/api.ts")
