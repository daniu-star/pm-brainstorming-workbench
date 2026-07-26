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

// ===== Pipeline 相关类型 =====
export type PipelineNodeName = "pm_prd" | "cot" | "coach" | "cto" | "designer" | "ops" | "user_feedback" | "canvas_synthesis" | "portrait" | "pm_acceptance";
export type PipelineNodeStatus = "pending" | "running" | "completed" | "error";
export interface PipelineNodeState {
  name: PipelineNodeName;
  status: PipelineNodeStatus;
  output?: string;
  startedAt?: number;
  completedAt?: number;
  tokens?: number;
}
export interface AcceptanceResult {
  passed: boolean;
  gaps: string[];
  suggestions: string[];
  summary: string;
}
export interface PipelineResult {
  prd: string;
  canvasTree: Record<string, unknown>;
  productPortrait: Record<string, unknown>;
  acceptanceResult: AcceptanceResult;
  revisionCount: number;
}

// Pipeline SSE 事件类型（用于在 store 中将 SSEEvent 安全转换为 pipeline 事件）
export interface PipelineSSEEvent {
  type: string;
  session_id?: string;
  node?: PipelineNodeName | string;
  role_name?: string;
  role?: string;
  token?: string;
  tokens?: number;
  output?: string;
  canvas_tree?: Record<string, unknown>;
  portrait?: Record<string, unknown>;
  acceptance_result?: AcceptanceResult;
  gaps?: string[];
  suggestions?: string[];
  revision_count?: number;
  total_tokens?: number;
  prd?: string;
  product_portrait?: Record<string, unknown>;
  message?: string;
}

// 10 个 Pipeline 节点的中文显示名映射
export const PIPELINE_NODE_LABELS: Record<PipelineNodeName, string> = {
  pm_prd: "产品经理（撰写PRD）",
  cot: "思维链引擎",
  coach: "产品教练",
  cto: "技术负责人",
  designer: "设计师",
  ops: "运营负责人",
  user_feedback: "目标用户",
  canvas_synthesis: "画布综合",
  portrait: "产品画像",
  pm_acceptance: "PM验收",
};

// Pipeline 节点顺序（用于初始化和修订循环重置）
export const PIPELINE_NODE_ORDER: PipelineNodeName[] = [
  "pm_prd",
  "cot",
  "coach",
  "cto",
  "designer",
  "ops",
  "user_feedback",
  "canvas_synthesis",
  "portrait",
  "pm_acceptance",
];

export interface Attachment {
  id: string;
  session_id: string;
  filename: string;
  size: number;
  content_type: string;
  uploaded_at: number;
  url: string;
}

// ===== 决策中心 =====
export type EvidenceSourceType = "interview" | "feedback" | "metric" | "competitor" | "document" | "manual";
export type ExperimentStatus = "planned" | "running" | "validated" | "invalidated";

export interface DecisionEvidence {
  id: string;
  title: string;
  source_type: EvidenceSourceType;
  summary: string;
  source_url: string;
  tags: string[];
  confidence: number;
  created_at: string;
}

export interface DecisionInitiative {
  id: string;
  title: string;
  description: string;
  reach: number;
  impact: number;
  confidence: number;
  effort: number;
  risk: number;
  priority_score: number;
  evidence_ids: string[];
  created_at: string;
}

export interface DecisionExperiment {
  id: string;
  title: string;
  hypothesis: string;
  primary_metric: string;
  success_criteria: string;
  initiative_id: string;
  status: ExperimentStatus;
  learning: string;
  created_at: string;
  updated_at?: string;
}

export interface DecisionHub {
  evidence: DecisionEvidence[];
  initiatives: DecisionInitiative[];
  experiments: DecisionExperiment[];
  updated_at: string;
}
