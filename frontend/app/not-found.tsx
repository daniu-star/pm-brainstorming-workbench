import Link from "next/link";
import { BrainIcon } from "@/components/icons";

export default function NotFound() {
  return (
    <main className="flex min-h-dvh flex-col items-center justify-center bg-dark-900 bg-mesh px-6 text-center">
      <div className="flex size-14 items-center justify-center rounded-xl border border-brand-300/30 bg-brand-300/10 text-brand-300">
        <BrainIcon size={26} />
      </div>
      <h1 className="mt-6 text-3xl font-semibold text-zinc-100">页面不存在</h1>
      <p className="mt-3 max-w-sm text-sm leading-6 text-zinc-500">
        你访问的页面可能已被移除、重命名，或地址输入有误。
      </p>
      <Link
        href="/"
        className="mt-8 inline-flex h-11 items-center rounded-lg bg-brand-300 px-6 text-sm font-semibold text-dark-900 transition-opacity hover:opacity-90"
      >
        返回首页
      </Link>
    </main>
  );
}
