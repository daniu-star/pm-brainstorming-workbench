"use client";

/**
 * 会话共享的客户端调用（第 35 批后端能力的 UI 接线）。
 *
 * 角色语义见 `backend/api/v2/shares.py`：
 * * owner：会话所有者，可增删共享成员、提交命令、删除会话；
 * * editor：可读 + 可提交命令，但命令以所有者身份落账；
 * * viewer：只读。
 */
import { api, apiV2 } from "@/lib/api";

export type SessionShareRole = "viewer" | "editor";

export interface SessionShare {
  email: string;
  role: SessionShareRole;
  created_at: string;
}

export interface SessionShareListPage {
  items: SessionShare[];
}

export const SHARE_ROLE_LABELS: Record<SessionShareRole, string> = {
  viewer: "只读",
  editor: "可操作",
};

export const SHARE_ROLE_HINTS: Record<SessionShareRole, string> = {
  viewer: "能看会话、消息、画布与审计报告，不能提交任何命令",
  editor: "能提交改稿、跑脑暴、审计等命令，但额度与模型 Key 仍记在会话所有者名下",
};

/** 邮箱归一化：去空白 + 小写。返回 null 表示明显不合法（UI 层先挡一次）。 */
export function normalizeShareEmail(raw: string): string | null {
  const value = raw.trim().toLowerCase();
  if (!value) return null;
  if (value.length > 320) return null;
  const at = value.indexOf("@");
  if (at <= 0 || at === value.length - 1) return null;
  if (value.includes(" ")) return null;
  return value;
}

/** 只有所有者能管理共享成员。 */
export function canManageShares(sessionOwnerEmail: string | null | undefined, myEmail: string | null): boolean {
  if (!sessionOwnerEmail || !myEmail) return false;
  return sessionOwnerEmail.trim().toLowerCase() === myEmail.trim().toLowerCase();
}

/** 把成员列表里的自己过滤掉，避免出现「你自己」这种无意义行。 */
export function withoutSelf(items: SessionShare[], myEmail: string | null): SessionShare[] {
  if (!myEmail) return items;
  const me = myEmail.trim().toLowerCase();
  return items.filter((item) => item.email.trim().toLowerCase() !== me);
}

/** 当前登录账号（`/api/auth/me`）：会话页据此判断自己是不是所有者。 */
export async function getCurrentEmail(): Promise<string | null> {
  try {
    const me = await api<{ authenticated?: boolean; email?: string }>("/api/auth/me");
    const email = (me?.email ?? "").trim().toLowerCase();
    return email || null;
  } catch {
    return null;
  }
}

export function listShares(sessionId: string, signal?: AbortSignal): Promise<SessionShareListPage> {
  return apiV2<SessionShareListPage>(`/api/v2/sessions/${sessionId}/shares`, { signal });
}

export function addShare(
  sessionId: string,
  input: { email: string; role: SessionShareRole }
): Promise<SessionShare> {
  return apiV2<SessionShare>(`/api/v2/sessions/${sessionId}/shares`, {
    method: "POST",
    body: input,
  });
}

export function removeShare(sessionId: string, email: string): Promise<{ removed: boolean }> {
  return apiV2<{ removed: boolean }>(
    `/api/v2/sessions/${sessionId}/shares/${encodeURIComponent(email)}`,
    { method: "DELETE" }
  );
}
