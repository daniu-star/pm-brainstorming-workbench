/**
 * Next.js 配置（第 40 批：补齐线上部署链路）。
 *
 * 两种部署形态共用一份配置：
 * 1) 本地开发：`start.bat` 会把 NEXT_PUBLIC_API_URL 指到 http://127.0.0.1:8001，
 *    前端直接跨端口请求后端，不经过 rewrites。
 * 2) 线上（Vercel 前端 + Hugging Face Space 后端）：NEXT_PUBLIC_API_URL=/backend，
 *    由下面的 rewrites 把 /backend/:path* 反代到后端容器；Cookie 因此是
 *    same-origin 的，SameSite=lax 才能随请求发送。
 *
 * 反代目标可用 BACKEND_ORIGIN 覆盖（换成自建服务器或新的 Space 时不必改代码）。
 */
const BACKEND_ORIGIN =
  process.env.BACKEND_ORIGIN ||
  "https://123chenjunliang-pm-brainstorm-workbench.hf.space";

/** @type {import('next').NextConfig} */
const nextConfig = {
  eslint: {
    ignoreDuringBuilds: true,
  },
  typescript: {
    ignoreBuildErrors: false,
  },
  async rewrites() {
    return [
      {
        source: "/backend/:path*",
        destination: `${BACKEND_ORIGIN}/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
