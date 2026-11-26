"use client";

/**
 * 统一权限页（第 39 批）：`GET /api/v2/access` 的客户端封装 + 纯展示函数。
 *
 * 数据形状见 `backend/api/v2/access.py` 与 `domain/contracts.py::AccessOverview`：
 * * `owned`：我创建、可能已共享给别人的会话；
 * * `incoming`：别人共享给我的会话（带我的角色）；
 * * `groups`：我在团队工作区里维护的成员组名单；
 * * `totals`：四类计数（`shared_out` 统计的是会话数，不是成员数）。
 */
import { apiV2 } from "@/lib/api";

export type AccessRole = "owner" | "editor" | "viewer";
export type ShareRole = "viewer" | "editor";

export interface AccessShare {
  email: string;
  role: ShareRole;
  created_at: string;
}

export interface OwnedSession {
  session_id: string;
  title: string;
  phase: string;
  updated_at: string;
  shares: AccessShare[];
}

export interface IncomingSession {
  session_id: string;
  title: string;
  phase: string;
  owner_email: string;
  role: ShareRole;
  updated_at: string;
}

export interface AccessGroupMember {
  email: string;
  role: ShareRole;
  created_at: string;
}

export interface AccessGroup {
  id: string;
  name: string;
  owner_email: string;
  members: AccessGroupMember[];
  created_at: string;
}

export interface AccessTotals {
  owned: number;
  shared_out: number;
  incoming: number;
  groups: number;
}

export interface AccessOverview {
  owned: OwnedSession[];
  incoming: IncomingSession[];
  groups: AccessGroup[];
  totals: AccessTotals;
}

export const ACCESS_ROLE_LABELS: Record<AccessRole, string> = {
  owner: "所有者",
  editor: "可操作",
  viewer: "只读",
};

const PHASE_LABELS: Record<string, string> = {
  clarifying: "澄清中",
  brainstorming: "脑暴中",
  auditing: "审计中",
  audit_completed: "审计完成",
  define: "定义",
  coach: "教练",
  interview: "访谈",
  brainstorm: "脑暴",
};

/** 历史列表 / 权限页共用的相位文案。 */
export function phaseLabel(phase: string): string {
  return PHASE_LABELS[phase] ?? phase;
}

/**
 * 历史抽屉里的角色徽标：只有「共享给我」才需要提示，所有者本人不显示徽标。
 * 返回 null 表示不渲染。
 */
export function sessionRoleBadge(role: string | undefined): string | null {
  if (!role || role === "owner") return null;
  const label = role === "editor" ? "可操作" : "只读";
  return `共享给我 · ${label}`;
}

/** 汇总一行文案，权限页顶部直接展示。 */
export function accessTotalsText(totals: AccessTotals | null | undefined): string {
  if (!totals) return "正在读取权限信息…";
  const parts = [
    `我创建 ${totals.owned}`,
    `已外发 ${totals.shared_out}`,
    `共享给我 ${totals.incoming}`,
    `成员组 ${totals.groups}`,
  ];
  return parts.join(" · ");
}

/** 「已共享给 2 人（1 只读 / 1 可操作）」/「还没有共享给别人」。 */
export function sharesSummary(shares: AccessShare[] | null | undefined): string {
  const list = shares ?? [];
  if (list.length === 0) return "还没有共享给别人";
  const viewers = list.filter((item) => item.role === "viewer").length;
  const editors = list.filter((item) => item.role === "editor").length;
  const detail: string[] = [];
  if (viewers > 0) detail.push(`${viewers} 只读`);
  if (editors > 0) detail.push(`${editors} 可操作`);
  return `已共享给 ${list.length} 人（${detail.join(" / ")}）`;
}

/** ISO 时间 → 「2026-11-26 14:21」；解析失败回落占位符而不是 NaN。 */
export function formatUpdated(iso: string | null | undefined): string {
  if (!iso) return "—";
  const time = Date.parse(iso);
  if (!Number.isFinite(time)) return "—";
  const date = new Date(time);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(
    date.getHours()
  )}:${pad(date.getMinutes())}`;
}

/** 权限页错误提示：401 明确要求登录，其余保留后端文案。 */
export function accessErrorHint(error: unknown): string {
  if (!error) return "";
  const message = error instanceof Error ? error.message : String(error);
  if (message.includes("401") || message.includes("请先登录")) {
    return "请先登录再打开权限页。";
  }
  return message || "读取权限信息失败，请稍后重试。";
}

export function getAccessOverview(signal?: AbortSignal): Promise<AccessOverview> {
  return apiV2<AccessOverview>("/api/v2/access", { signal });
}

/** 成员主动退出共享：之后该会话不再出现在「共享给我」里。 */
export function leaveSharedSession(sessionId: string): Promise<{ status: string; session_id: string }> {
  return apiV2<{ status: string; session_id: string }>(
    `/api/v2/sessions/${sessionId}/share`,
    { method: "DELETE" }
  );
}
