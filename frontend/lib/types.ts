export type Role = "cto" | "designer" | "ops" | "user" | "coach" | "interviewer";

export interface Message {
  id?: string;
  role: "user" | "assistant";
  content: string;
  role_name?: string;
  timestamp?: string;
}

export interface Session {
  id: string;
  problem_statement: string;
  phase: "define" | "coach" | "brainstorm" | "interview";
  messages: Message[];
  discussion_map: DiscussionMap | null;
  canvas_tree: FeatureTree | null;
  created_at: string;
}

export interface DiscussionMap {
  topic: string;
  timeline: TimelineNode[];
}

export interface TimelineNode {
  id: string;
  type: "consensus" | "disagreement" | "summary";
  content: string;
  roles: string[];
  timestamp?: string;
  positions?: { role: string; stance: string }[];
}

export interface FeatureTree {
  root: string;
  branches: Branch[];
}

export interface Branch {
  name: string;
  children: LeafNode[];
}

export interface LeafNode {
  name: string;
  source_role: Role;
  type: "feature" | "risk" | "question" | "insight";
  source_text: string;
}

export interface RoleInfo {
  id: Role;
  name: string;
  color: string;
}

export const ROLES: RoleInfo[] = [
  { id: "cto", name: "技术负责人", color: "#3b82f6" },
  { id: "designer", name: "设计师", color: "#a855f7" },
  { id: "ops", name: "运营负责人", color: "#22c55e" },
  { id: "user", name: "目标用户", color: "#f97316" },
];

export const ROLE_MAP: Record<string, RoleInfo> = {};
ROLES.forEach((r) => (ROLE_MAP[r.id] = r));
// Chinese aliases for all core roles
ROLE_MAP["技术负责人"] = { id: "cto", name: "技术负责人", color: "#3b82f6" };
ROLE_MAP["设计师"] = { id: "designer", name: "设计师", color: "#a855f7" };
ROLE_MAP["运营负责人"] = { id: "ops", name: "运营负责人", color: "#22c55e" };
ROLE_MAP["目标用户"] = { id: "user", name: "目标用户", color: "#f97316" };
ROLE_MAP["coach"] = { id: "coach", name: "产品教练", color: "#f59e0b" };
ROLE_MAP["产品教练"] = { id: "coach", name: "产品教练", color: "#f59e0b" };
ROLE_MAP["interviewer"] = { id: "interviewer", name: "AI 面试官", color: "#ef4444" };
ROLE_MAP["AI面试官"] = { id: "interviewer", name: "AI 面试官", color: "#ef4444" };

export interface SessionSummary {
  id: string;
  problem_statement: string;
  phase: "define" | "coach" | "brainstorm" | "interview";
  message_count: number;
  created_at: string;
}

export const TYPE_CONFIG: Record<string, { label: string; color: string; bg: string }> = {
  feature:  { label: "功能", color: "#22c55e", bg: "#22c55e18" },
  risk:     { label: "风险", color: "#ef4444", bg: "#ef444418" },
  question: { label: "问题", color: "#f59e0b", bg: "#f59e0b18" },
  insight:  { label: "洞察", color: "#f59e0b", bg: "#f59e0b18" },
  // Chinese aliases
  "功能": { label: "功能", color: "#22c55e", bg: "#22c55e18" },
  "风险": { label: "风险", color: "#ef4444", bg: "#ef444418" },
  "问题": { label: "问题", color: "#f59e0b", bg: "#f59e0b18" },
  "洞察": { label: "洞察", color: "#f59e0b", bg: "#f59e0b18" },
};

export interface ProductPortrait {
  product_name: string;
  tagline: string;
  target_users: string;
  core_features: { name: string; description: string; priority: "must-have" | "nice-to-have" }[];
  style_keywords: string[];
  color_scheme: { primary: string; secondary: string; accent: string; background: string };
  interaction_style: string;
  wireframe_description: string;
}

export const TIMELINE_NODE_CONFIG: Record<string, { label: string; color: string; bg: string; icon: string }> = {
  consensus:     { label: "共识", color: "#22c55e", bg: "#22c55e18", icon: "✓" },
  disagreement:  { label: "分歧", color: "#ef4444", bg: "#ef444418", icon: "✗" },
  summary:       { label: "总结", color: "#f59e0b", bg: "#f59e0b18", icon: "◆" },
};
