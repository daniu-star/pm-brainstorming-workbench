export default function Loading() {
  return (
    <div className="flex min-h-dvh items-center justify-center bg-dark-900" role="status" aria-label="加载中">
      <svg
        className="h-8 w-8 animate-spin text-brand-400"
        viewBox="0 0 24 24"
        fill="none"
        aria-hidden="true"
      >
        <circle className="opacity-20" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" />
        <path d="M22 12a10 10 0 0 0-10-10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
      </svg>
    </div>
  );
}
