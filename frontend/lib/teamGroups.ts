/**
 * 团队工作区（成员组）：把「常用协作名单」沉淀成可复用的组。
 *
 * 关键语义（UI 与文档必须一致，避免误解为「组=授权」）：
 * * 组只保存名单，本身不授予任何权限；
 * * 把组「应用到会话」时才逐条写成 `session_shares`，权限判断永远只看会话级共享行；
 * * 因此删除组不会回收已授予的访问，撤销访问要在共享列表里移除那个人。
 *
 * 后端接口在 `backend/api/v2/groups.py`；`apiV2` 不补前缀，路径必须写完整。
 */
import { apiV2, errorMessage } from "./api";

export type TeamGroupRole = "viewer" | "editor";

export interface TeamGroupMember {
  email: string;
  role: TeamGroupRole;
  created_at?: string;
}

export interface TeamGroup {
  id: string;
  name: string;
  owner_email?: string;
  members: TeamGroupMember[];
  created_at?: string;
}

export interface TeamGroupListPage {
  items: TeamGroup[];
}

export interface ApplyGroupReceipt {
  group_id: string;
  added: number;
  already_shared: number;
  members_total: number;
}

export const MAX_GROUP_NAME = 80;
export const MAX_MEMBERS_PER_GROUP = 50;

/** 组名归一化：去首尾空白、把连续空白压成一个空格；空名或超长返回 null。 */
export function normalizeGroupName(raw: string | null | undefined): string | null {
  if (typeof raw !== "string") return null;
  const name = raw.trim().replace(/\s+/g, " ");
  if (!name || name.length > MAX_GROUP_NAME) return null;
  return name;
}

/** 组卡片上的一句话摘要，例如「3 人 · 2 只读 · 1 可操作」。 */
export function groupSummary(group: TeamGroup): string {
  const members = group.members ?? [];
  if (members.length === 0) return "还没有成员";
  const viewers = members.filter((m) => m.role === "viewer").length;
  const editors = members.length - viewers;
  const parts = [`${members.length} 人`];
  if (viewers > 0) parts.push(`${viewers} 只读`);
  if (editors > 0) parts.push(`${editors} 可操作`);
  return parts.join(" · ");
}

/** 「应用到会话」之后给用户看的反馈。 */
export function applyGroupHint(receipt: ApplyGroupReceipt): string {
  if (receipt.members_total === 0) return "这个成员组还是空的：先加人再应用。";
  if (receipt.added === 0) {
    return `组内 ${receipt.members_total} 人已经都在共享列表里，无需重复添加。`;
  }
  const tail =
    receipt.already_shared > 0
      ? `，其中 ${receipt.already_shared} 人原本就在共享列表里`
      : "";
  return `已添加 ${receipt.added} 人${tail}。`;
}

/** 组名重复 / 空名等后端拒绝的中文兜底文案。 */
export function groupErrorHint(error: unknown): string {
  const message = errorMessage(error);
  // 通用文案保持本页自己的措辞（「操作」而非「请求」），便于用户理解这是组操作失败。
  const generic = message === "请求失败，请稍后重试" || message === "服务暂不可用，请稍后重试";
  if (!message || generic) return "操作失败，请稍后重试";
  if (message.includes("同名")) return "已经有同名的成员组，换个名字试试。";
  if (message.includes("最多")) return message;
  return message;
}

// --------------------------------------------------------------------------- //
// 接口
// --------------------------------------------------------------------------- //

export function listTeamGroups(signal?: AbortSignal): Promise<TeamGroupListPage> {
  return apiV2<TeamGroupListPage>("/api/v2/groups", { signal });
}

export function createTeamGroup(name: string, signal?: AbortSignal): Promise<TeamGroup> {
  return apiV2<TeamGroup>("/api/v2/groups", { method: "POST", body: { name }, signal });
}

export function deleteTeamGroup(groupId: string): Promise<{ deleted: boolean }> {
  return apiV2<{ deleted: boolean }>(`/api/v2/groups/${encodeURIComponent(groupId)}`, {
    method: "DELETE",
  });
}

export function addTeamGroupMember(
  groupId: string,
  member: { email: string; role: TeamGroupRole }
): Promise<TeamGroupMember> {
  return apiV2<TeamGroupMember>(`/api/v2/groups/${encodeURIComponent(groupId)}/members`, {
    method: "POST",
    body: member,
  });
}

export function removeTeamGroupMember(groupId: string, email: string): Promise<{ removed: boolean }> {
  return apiV2<{ removed: boolean }>(
    `/api/v2/groups/${encodeURIComponent(groupId)}/members/${encodeURIComponent(email)}`,
    { method: "DELETE" }
  );
}

/** 把成员组一次性应用到会话（幂等：已是成员的会被跳过）。 */
export function applyGroupToSession(
  sessionId: string,
  groupId: string
): Promise<ApplyGroupReceipt> {
  return apiV2<ApplyGroupReceipt>(
    `/api/v2/sessions/${encodeURIComponent(sessionId)}/shares/from-group`,
    { method: "POST", body: { group_id: groupId } }
  );
}
