export interface TeamQuota {
  quota: number;
  used: number;
  remaining: number;
}

export interface TeamMember {
  id: string;
  email: string;
  nickname: string;
  role: "owner" | "admin" | "member";
  status: string;
  joined_at: string;
  quota: TeamQuota;
}

export interface TeamInvitation {
  id: string;
  email: string;
  role: "admin" | "member";
  status: string;
  delivery_status: "pending" | "sent" | "failed";
  created_at: string;
  expires_at: string;
}

export interface TeamChatMessage {
  id: string;
  author_name: string;
  content: string;
  created_at: string;
}

export interface TeamSummary {
  id: string;
  name: string;
  current_user_role: "owner" | "admin" | "member";
  members: TeamMember[];
  invitations: TeamInvitation[];
  quota: { total: number; used: number; remaining: number };
  prd_document_count: number;
  prd_project_count: number;
  session_count: number;
  recent_sessions: Array<{
    id: string;
    problem_statement: string;
    phase: string;
    prd_count: number;
    updated_at: string;
  }>;
  smtp_configured: boolean;
  updated_at: string;
}
