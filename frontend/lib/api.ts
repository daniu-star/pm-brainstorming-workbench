const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  /** 后端响应体（如 429 的 { message, retry_after }），供调用方读取附加信息。 */
  payload: Record<string, unknown> | null;

  constructor(message: string, status: number, payload: Record<string, unknown> | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
  }
}

/** Map HTTP status codes to friendly Chinese messages. */
export function statusMessage(status: number): string {
  if (status === 401) return "请先登录";
  if (status === 403) return "没有权限";
  if (status === 404) return "资源不存在";
  if (status >= 500) return "服务暂不可用，请稍后重试";
  return `请求失败（${status}）`;
}

const MAX_DETAIL_LENGTH = 200;

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail = "";
    let payload: Record<string, unknown> | null = null;
    try {
      const text = await res.text();
      if (text) {
        try {
          const parsed = JSON.parse(text) as { detail?: unknown };
          payload = typeof parsed === "object" && parsed !== null ? (parsed as Record<string, unknown>) : null;
          if (parsed && typeof parsed.detail === "string" && parsed.detail) {
            detail = parsed.detail;
          } else if (
            parsed &&
            typeof parsed.detail === "object" &&
            parsed.detail !== null &&
            typeof (parsed.detail as Record<string, unknown>).message === "string"
          ) {
            // detail 为 { message, retry_after } 形态（如 429 限流）。
            detail = (parsed.detail as Record<string, string>).message;
          }
        } catch {
          // Non-JSON body — fall back to status message
        }
      }
    } catch {
      // Body read failure — fall back to status message
    }
    if (detail.length > MAX_DETAIL_LENGTH) detail = detail.slice(0, MAX_DETAIL_LENGTH);
    throw new ApiError(detail || statusMessage(res.status), res.status, payload);
  }
  return res.json() as Promise<T>;
}

export function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}
