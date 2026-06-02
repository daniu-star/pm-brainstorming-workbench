import { getUserHeaders } from "./user";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...getUserHeaders(),
        ...options?.headers,
      },
    });
  } catch (err) {
    if (err instanceof TypeError && (err.message.includes("Failed to fetch") || err.message.includes("NetworkError"))) {
      throw new Error("无法连接到服务器，请确认后端服务已启动");
    }
    throw err;
  }
  if (!res.ok) {
    let errMsg: string;
    try {
      errMsg = await res.text();
    } catch {
      errMsg = `API 请求失败: ${res.status}`;
    }
    throw new Error(errMsg || `API 请求失败: ${res.status}`);
  }
  const contentType = res.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) {
    throw new Error(`意外的响应格式 (${contentType})，状态码: ${res.status}`);
  }
  try {
    return await res.json();
  } catch {
    throw new Error(`响应 JSON 解析失败，状态码: ${res.status}`);
  }
}

export function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}
