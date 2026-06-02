"use client";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="zh-CN">
      <body style={{ margin: 0, background: "#0a0a0f", color: "#e4e4e7", fontFamily: "system-ui, sans-serif", minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center" }}>
        <div style={{ textAlign: "center", padding: "2rem", maxWidth: "480px" }}>
          <div style={{ marginBottom: "1rem", display: "flex", justifyContent: "center" }}>
            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="#f87171" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
              <path d="M12 9v4" />
              <path d="M12 17h.01" />
            </svg>
          </div>
          <h1 style={{ fontSize: "1.25rem", fontWeight: 600, color: "#f87171", marginBottom: "0.5rem" }}>应用出错</h1>
          <p style={{ color: "#a1a1aa", fontSize: "0.875rem", marginBottom: "1.5rem", lineHeight: 1.6 }}>
            {error.message || "未知错误"}
          </p>
          {error.digest && (
            <p style={{ color: "#a1a1aa", fontSize: "0.75rem", marginBottom: "1.5rem" }}>
              错误 ID: {error.digest}
            </p>
          )}
          <button
            onClick={reset}
            style={{
              padding: "0.75rem 1.5rem",
              background: "linear-gradient(135deg, #6366f1, #a855f7)",
              color: "white",
              border: "none",
              borderRadius: "0.75rem",
              fontSize: "0.875rem",
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            重试
          </button>
          <div style={{ marginTop: "1rem" }}>
            <a href="/" style={{ color: "#818cf8", fontSize: "0.875rem", textDecoration: "none" }}>
              返回首页
            </a>
          </div>
        </div>
      </body>
    </html>
  );
}
