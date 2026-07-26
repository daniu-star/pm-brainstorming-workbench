/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  images: {
    unoptimized: true,
  },
  async rewrites() {
    return [
      {
        source: "/backend/:path*",
        destination: "https://123chenjunliang-pm-brainstorm-workbench.hf.space/:path*",
      },
    ];
  },
};
module.exports = nextConfig;
