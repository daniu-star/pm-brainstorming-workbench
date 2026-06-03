"use client";

export default function SessionError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="min-h-screen flex items-center justify-center bg-warm-50">
      <div className="text-center max-w-md px-4">
        <div className="mb-4 flex justify-center">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="#dc2626" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
            <path d="M12 9v4" />
            <path d="M12 17h.01" />
          </svg>
        </div>
        <h1 className="text-lg font-semibold text-red-600 mb-2">工作台加载出错</h1>
        <p className="text-sm text-warm-500 mb-4 leading-relaxed">
          {error.message || "未知错误，请刷新页面重试"}
        </p>
        <div className="flex gap-3 justify-center">
          <button
            onClick={reset}
            className="px-4 py-2 bg-amber-600 hover:bg-amber-700 active:bg-amber-800 text-white text-sm font-medium rounded-xl transition-colors duration-200"
          >
            重试
          </button>
          <a
            href="/"
            className="px-4 py-2 text-sm font-medium text-amber-600 hover:text-amber-700 border border-amber-200 hover:border-amber-300 rounded-xl transition-colors duration-200"
          >
            返回首页
          </a>
        </div>
      </div>
    </div>
  );
}
