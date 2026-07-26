import { create } from "zustand";
import type { Message, Role, DiscussionMap, SessionSummary, ProductPortrait, PipelineNodeState, PipelineNodeName, PipelineResult, PipelineSSEEvent, AcceptanceResult, SessionPhase, ClarificationState } from "@/lib/types";
import { PIPELINE_NODE_ORDER } from "@/lib/types";
import { createSSEConnection, type SSEEvent, type SSEConnectionStatus } from "@/lib/sse";
import { api } from "@/lib/api";
import { saveApiKeyConfig, clearApiKeyConfig, getStoredApiKey, getStoredBaseUrl, getStoredModel, saveJwtToken, clearJwtToken, isLoggedIn as checkIsLoggedIn, getJwtToken } from "@/lib/user";
import { toast } from "@/components/Toast";

const ONBOARDING_KEY = "pm-brainstorm-onboarded";

function isOnboarded(): boolean {
  if (typeof window === "undefined") return false;
  return localStorage.getItem(ONBOARDING_KEY) === "true";
}

function markOnboarded(): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(ONBOARDING_KEY, "true");
}

function generateId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

interface SessionState {
  sessionId: string | null;
  interviewId: string | null;
  dimensionsCovered: string[];
  questionCount: number;
  interviewCompleted: boolean;
  auditStatus: "not_started" | "active" | "completed" | "aborted" | "superseded";
  currentAuditDimension: string | null;
  auditReport: string | null;
  phase: SessionPhase;
  clarificationState: ClarificationState | null;
  messages: Message[];
  discussionMap: DiscussionMap | null;
  canvasStatus: "idle" | "syncing" | "ready" | "stale" | "error";
  productPortrait: ProductPortrait | null;
  isGeneratingPortrait: boolean;
  isStreaming: boolean;
  streamingRole: string | null;
  streamingContent: string;
  error: string | null;
  historySessions: SessionSummary[];
  isHistoryOpen: boolean;
  interviewMode: "voice" | "text";
  isPlayingAudio: boolean;
  targetRole: Role | "all";
  connectionStatus: "connected" | "reconnecting" | "disconnected";
  userApiKey: string;
  userBaseUrl: string;
  userModel: string;
  tokenQuota: number;
  tokensUsed: number;
  isSettingsOpen: boolean;
  isRechargeOpen: boolean;
  isOnboardingOpen: boolean;
  hasCompletedOnboarding: boolean;
  isLoggedIn: boolean;
  userNickname: string | null;
  lastAnswerQuality: "good" | "bad" | "neutral";
  setTargetRole: (role: Role | "all") => void;
  setUserApiKey: (key: string, baseUrl: string, model: string) => Promise<void>;
  clearUserApiKey: () => void;
  fetchQuota: () => Promise<void>;
  refreshAfterRecharge: () => Promise<void>;
  setSettingsOpen: (open: boolean) => void;
  setRechargeOpen: (open: boolean) => void;
  setOnboardingOpen: (open: boolean) => void;
  completeOnboarding: () => void;
  login: (phone: string, code: string) => Promise<void>;
  logout: () => void;

  createSession: (problem: string) => Promise<void>;
  loadSession: (id: string) => Promise<void>;
  createInterviewSpace: (parentSessionId: string) => Promise<string>;
  sendMessage: (content: string, targetRole: Role | "all") => void;
  startClarification: () => void;
  sendToCoach: (content: string) => void;
  skipClarification: () => Promise<void>;
  confirmClarification: () => Promise<void>;
  startInterview: () => void;
  answerInterview: (answer: string) => void;
  generateCanvas: () => Promise<void>;
  setCanvasNodeStatus: (nodeId: string, status: "draft" | "confirmed") => Promise<void>;
  generateProductPortrait: () => Promise<void>;
  clearError: () => void;
  abortStream: () => void;
  fetchHistory: () => Promise<void>;
  toggleHistory: () => void;
  setInterviewMode: (mode: "voice" | "text") => void;
  setPlayingAudio: (playing: boolean) => void;

  // Pipeline 相关字段
  pipelineNodes: PipelineNodeState[];
  pipelineResult: PipelineResult | null;
  isPipelineRunning: boolean;
  pipelineRevisionCount: number;
  runPipeline: () => void;
  clearPipeline: () => void;
}

export const useSessionStore = create<SessionState>((set, get) => {
  let abortController: AbortController | null = null;
  let canvasUpdatePending = false;
  let canvasUpdateQueued = false;

  async function autoUpdateCanvas(sid: string) {
    if (canvasUpdatePending) {
      canvasUpdateQueued = true;
      return;
    }
    canvasUpdatePending = true;
    set({ canvasStatus: "syncing" });
    try {
      const map = await api<DiscussionMap>("/api/canvas/incremental", {
        method: "POST",
        body: JSON.stringify({ session_id: sid }),
      });
      set({ discussionMap: map, canvasStatus: "ready" });
    } catch (error) {
      const message = error instanceof Error ? error.message : "画布同步失败";
      set({ error: `画布暂未同步：${message}`, canvasStatus: "error" });
    } finally {
      canvasUpdatePending = false;
      if (canvasUpdateQueued) {
        canvasUpdateQueued = false;
        void autoUpdateCanvas(sid);
      }
    }
  }

  const evaluateAnswerQuality = (aiResponse: string): "good" | "bad" | "neutral" => {
    const goodKeywords = ["很好", "不错", "优秀", "出色", "到位", "精准", "深入", "全面", "清晰", "合理", "赞", "棒", "excellent", "good", "great", "well"];
    const badKeywords = ["不够", "需要改进", "缺失", "不足", "欠缺", "薄弱", "偏离", "模糊", "浅", "缺乏", "忽略", "遗漏", "insufficient", "weak", "lacking", "missing", "needs improvement"];

    const lower = aiResponse.toLowerCase();
    const hasGood = goodKeywords.some(k => lower.includes(k));
    const hasBad = badKeywords.some(k => lower.includes(k));

    if (hasGood && !hasBad) return "good";
    if (hasBad && !hasGood) return "bad";
    return "neutral";
  };

  function handleSSEEvent(event: SSEEvent) {
    switch (event.type) {
      case "phase_change":
        if (event.phase === "clarify" || event.phase === "audit" || event.phase === "brainstorm") {
          set({ phase: event.phase });
        }
        break;
      case "clarification_state":
        if (event.clarification_state) {
          set({ clarificationState: event.clarification_state });
        }
        break;
      case "audit_state":
        set({
          phase: event.audit_status === "completed" ? "decision_ready" : "audit",
          auditStatus: event.audit_status || "active",
          dimensionsCovered: event.dimensions_covered || [],
          questionCount: event.question_count || 0,
          currentAuditDimension: event.current_dimension || null,
          interviewCompleted: Boolean(event.interview_completed),
          auditReport: event.audit_report || null,
        });
        break;
      case "role_start":
        set({ streamingRole: event.role || null, connectionStatus: "connected" });
        break;
      case "token":
        if (event.token) {
          set((s) => ({
            streamingContent: s.streamingContent + event.token,
          }));
        }
        break;
      case "role_done": {
        const state = get();
        const msg: Message = {
          id: generateId(),
          role: "assistant",
          content: state.streamingContent,
          role_name: event.role_name || event.role,
          timestamp: new Date().toISOString(),
        };
        set({
          messages: [...state.messages, msg],
          streamingContent: "",
          streamingRole: null,
          ...(event.dimensions_covered ? { dimensionsCovered: event.dimensions_covered } : {}),
          ...(event.question_count !== undefined ? { questionCount: event.question_count } : {}),
          ...(event.interview_completed !== undefined ? { interviewCompleted: event.interview_completed } : {}),
          ...(event.current_dimension !== undefined ? { currentAuditDimension: event.current_dimension } : {}),
          lastAnswerQuality: evaluateAnswerQuality(state.streamingContent),
        });
        break;
      }
      case "round_completed": {
        const state = get();
        if (state.sessionId) {
          void autoUpdateCanvas(state.sessionId);
        }
        break;
      }
      case "error":
        handleError(event.message || "未知错误");
        break;
      case "quota_deduct":
        set((s) => ({ tokensUsed: s.tokensUsed + (event.tokens || 0) }));
        break;
    }
  }

  function handleDone() {
    const state = get();
    if (state.streamingContent) {
      const msg: Message = {
        id: generateId(),
        role: "assistant",
        content: state.streamingContent,
        role_name: state.streamingRole || undefined,
        timestamp: new Date().toISOString(),
      };
      set({
        messages: [...state.messages, msg],
        streamingContent: "",
        streamingRole: null,
        isStreaming: false,
      });
    } else {
      set({ isStreaming: false, connectionStatus: "connected" });
    }
  }

  function handleError(err: string) {
    const reconnecting = err.includes("重连");
    set({
      error: err,
      ...(reconnecting ? {} : { isStreaming: false }),
      connectionStatus: reconnecting ? "reconnecting" : "disconnected",
    });
    if (!reconnecting) {
      toast("error", err);
    }
  }

  function handleStatusChange(status: SSEConnectionStatus) {
    if (status === "connected") {
      set({ connectionStatus: "connected" });
    } else if (status === "reconnecting") {
      set({ connectionStatus: "reconnecting" });
    } else if (status === "disconnected") {
      set({ connectionStatus: "disconnected" });
    }
  }

  return {
    sessionId: null,
    interviewId: null,
    dimensionsCovered: [],
    questionCount: 0,
    interviewCompleted: false,
    auditStatus: "not_started",
    currentAuditDimension: null,
    auditReport: null,
    phase: "draft",
    clarificationState: null,
    messages: [],
    discussionMap: null,
    canvasStatus: "idle",
    productPortrait: null,
    isGeneratingPortrait: false,
    isStreaming: false,
    streamingRole: null,
    streamingContent: "",
    error: null,
    historySessions: [],
    isHistoryOpen: false,
    interviewMode: "text",
    isPlayingAudio: false,
    targetRole: "all",
    connectionStatus: "connected",
    userApiKey: getStoredApiKey(),
    userBaseUrl: getStoredBaseUrl(),
    userModel: getStoredModel(),
    tokenQuota: 0,
    tokensUsed: 0,
    isSettingsOpen: false,
    isRechargeOpen: false,
    isOnboardingOpen: false,
    hasCompletedOnboarding: isOnboarded(),
    isLoggedIn: checkIsLoggedIn(),
    userNickname: null,
    lastAnswerQuality: "neutral" as const,
    // Pipeline 初始状态
    pipelineNodes: [],
    pipelineResult: null,
    isPipelineRunning: false,
    pipelineRevisionCount: 0,
    setTargetRole: (role) => set({ targetRole: role }),

    createSession: async (problem: string) => {
      const session = await api<{ id: string; phase: SessionPhase; clarification_state: ClarificationState }>("/api/session", {
        method: "POST",
        body: JSON.stringify({ problem_statement: problem }),
      });
      set({
        sessionId: session.id,
        phase: session.phase,
        clarificationState: session.clarification_state,
        messages: [],
        discussionMap: null,
        canvasStatus: "idle",
      });
    },

    loadSession: async (id: string) => {
      const session = await api<{
        id: string;
        phase: string;
        messages: Message[];
        discussion_map: DiscussionMap | null;
        product_portrait: ProductPortrait | null;
        clarification_state?: ClarificationState;
        canvas_status?: SessionState["canvasStatus"];
      }>(`/api/session/${id}`);
      set({
        sessionId: session.id,
        phase: (
          session.phase === "coach"
            ? "clarify"
            : session.phase === "interview"
              ? "audit"
              : session.phase || "draft"
        ) as SessionState["phase"],
        clarificationState: session.clarification_state || null,
        messages: session.messages || [],
        discussionMap: session.discussion_map,
        canvasStatus: session.canvas_status || (session.discussion_map ? "ready" : "idle"),
        productPortrait: session.product_portrait || null,
      });
    },

    createInterviewSpace: async (parentSessionId: string) => {
      const result = await api<{
        interview_id: string;
        status: SessionState["auditStatus"];
        messages: Message[];
        dimensions_covered: string[];
        current_dimension: string | null;
        question_count: number;
        report: string | null;
      }>("/api/interview/create-space", {
        method: "POST",
        body: JSON.stringify({ parent_session_id: parentSessionId }),
      });
      set({
        interviewId: result.interview_id,
        phase: result.status === "completed" ? "decision_ready" : "audit",
        messages: result.messages || [],
        dimensionsCovered: result.dimensions_covered || [],
        questionCount: result.question_count || 0,
        interviewCompleted: result.status === "completed",
        auditStatus: result.status,
        currentAuditDimension: result.current_dimension,
        auditReport: result.report,
      });
      return result.interview_id;
    },

    startClarification: () => {
      const state = get();
      if (!state.sessionId || state.isStreaming) return;

      abortController?.abort();
      set({ isStreaming: true, streamingContent: "", error: null });
      abortController = createSSEConnection(
        "/api/brainstorm/coach/start",
        { session_id: state.sessionId },
        handleSSEEvent,
        handleDone,
        handleError,
        handleStatusChange
      );
    },

    sendToCoach: (content: string) => {
      const state = get();
      if (!state.sessionId) return;

      if (abortController) {
        abortController.abort();
      }

      const userMsg: Message = {
        id: generateId(),
        role: "user",
        content,
        timestamp: new Date().toISOString(),
      };
      set({
        messages: [...state.messages, userMsg],
        isStreaming: true,
        streamingContent: "",
        error: null,
      });

      abortController = createSSEConnection(
        "/api/brainstorm/coach",
        { session_id: state.sessionId, content },
        handleSSEEvent,
        handleDone,
        handleError,
        handleStatusChange
      );
    },

    skipClarification: async () => {
      const state = get();
      if (!state.sessionId) return;
      const result = await api<{ phase: SessionPhase; clarification_state: ClarificationState }>(
        "/api/brainstorm/coach/skip",
        {
          method: "POST",
          body: JSON.stringify({ session_id: state.sessionId }),
        }
      );
      set({
        phase: result.phase,
        clarificationState: result.clarification_state,
        error: null,
      });
    },

    confirmClarification: async () => {
      const state = get();
      if (!state.sessionId) return;
      const result = await api<{ phase: SessionPhase; clarification_state: ClarificationState }>(
        "/api/brainstorm/coach/confirm",
        {
          method: "POST",
          body: JSON.stringify({ session_id: state.sessionId }),
        }
      );
      set({
        phase: result.phase,
        clarificationState: result.clarification_state,
        error: null,
      });
      toast("success", "需求已确认，多角色专家已就位");
    },

    sendMessage: (content: string, targetRole: Role | "all") => {
      const state = get();
      if (!state.sessionId) return;

      if (abortController) {
        abortController.abort();
      }

      const userMsg: Message = {
        id: generateId(),
        role: "user",
        content,
        timestamp: new Date().toISOString(),
      };
      set({
        messages: [...state.messages, userMsg],
        isStreaming: true,
        streamingContent: "",
        error: null,
      });

      abortController = createSSEConnection(
        "/api/brainstorm/message",
        {
          session_id: state.sessionId,
          content,
          target_role: targetRole,
        },
        handleSSEEvent,
        handleDone,
        handleError
      );
    },

    startInterview: () => {
      const state = get();
      if (!state.sessionId) return;

      if (abortController) {
        abortController.abort();
      }

      set({
        phase: "audit",
        auditStatus: state.auditStatus === "not_started" ? "active" : state.auditStatus,
        isStreaming: true,
        streamingContent: "",
        error: null,
      });

      const useSpace = !!state.interviewId;
      const endpoint = useSpace
        ? `/api/interview/space/${state.interviewId}/start`
        : "/api/interview/start";
      const body = useSpace ? {} : { session_id: state.sessionId };

      abortController = createSSEConnection(
        endpoint,
        body,
        handleSSEEvent,
        handleDone,
        handleError,
        handleStatusChange
      );
    },

    answerInterview: (answer: string) => {
      const state = get();
      if (!state.sessionId) return;

      if (abortController) {
        abortController.abort();
      }

      const userMsg: Message = {
        id: generateId(),
        role: "user",
        content: answer,
        timestamp: new Date().toISOString(),
        stage: "audit",
        audit_run_id: state.interviewId || undefined,
      };
      set({
        messages: [...state.messages, userMsg],
        isStreaming: true,
        streamingContent: "",
        error: null,
      });

      const useSpace = !!state.interviewId;
      const endpoint = useSpace
        ? `/api/interview/space/${state.interviewId}/respond`
        : "/api/interview/respond";
      const body = useSpace ? { answer } : { session_id: state.sessionId, answer };

      abortController = createSSEConnection(
        endpoint,
        body,
        handleSSEEvent,
        handleDone,
        handleError
      );
    },

    generateCanvas: async () => {
      const state = get();
      if (!state.sessionId) return;
      set({ canvasStatus: "syncing" });
      try {
        const map = await api<DiscussionMap>("/api/canvas/generate", {
          method: "POST",
          body: JSON.stringify({ session_id: state.sessionId }),
        });
        set({ discussionMap: map, canvasStatus: "ready" });
      } catch (error) {
        set({ canvasStatus: "error" });
        throw error;
      }
    },

    setCanvasNodeStatus: async (nodeId, status) => {
      const state = get();
      if (!state.sessionId) return;
      const map = await api<DiscussionMap>(
        `/api/canvas/${state.sessionId}/nodes/${encodeURIComponent(nodeId)}`,
        {
          method: "PATCH",
          body: JSON.stringify({ status }),
        }
      );
      set({ discussionMap: map, canvasStatus: "ready" });
    },

    generateProductPortrait: async () => {
      const state = get();
      if (!state.sessionId) return;
      set({ isGeneratingPortrait: true });
      try {
        const portrait = await api<ProductPortrait>("/api/product/portrait", {
          method: "POST",
          body: JSON.stringify({ session_id: state.sessionId }),
        });
        set({ productPortrait: portrait, isGeneratingPortrait: false });
        toast("success", "产品画像已生成");
      } catch (err) {
        set({ isGeneratingPortrait: false });
        toast("error", `生成产品画像失败：${err instanceof Error ? err.message : "未知错误"}`);
      }
    },

    clearError: () => set({ error: null }),

    abortStream: () => {
      if (abortController) {
        abortController.abort();
        abortController = null;
      }
      set({ isStreaming: false, streamingContent: "", streamingRole: null });
    },

    fetchHistory: async () => {
      try {
        const sessions = await api<SessionSummary[]>("/api/session");
        set({ historySessions: sessions });
      } catch {
        // silent fail - history is best-effort
      }
    },

    toggleHistory: () => set((s) => ({ isHistoryOpen: !s.isHistoryOpen })),

    setInterviewMode: (mode) => set({ interviewMode: mode }),

    setPlayingAudio: (playing) => set({ isPlayingAudio: playing }),

    setUserApiKey: async (key, baseUrl, model) => {
      set({ userApiKey: key, userBaseUrl: baseUrl, userModel: model });
      saveApiKeyConfig(key, baseUrl, model);
      if (key) {
        try {
          const result = await api<{ status: string; provider?: string; base_url?: string; model?: string }>("/api/user/test-key", {
            method: "POST",
            body: JSON.stringify({ api_key: key, base_url: baseUrl, model }),
          });
          const correctedBaseUrl = result.base_url || baseUrl;
          const correctedModel = result.model || model;
          if (correctedBaseUrl !== baseUrl || correctedModel !== model) {
            set({ userBaseUrl: correctedBaseUrl, userModel: correctedModel });
            saveApiKeyConfig(key, correctedBaseUrl, correctedModel);
          }
          toast("success", "API Key 已在当前浏览器保存并验证通过，不会写入服务端数据库");
        } catch (err) {
          toast("error", `API Key 验证失败：${err instanceof Error ? err.message : "连接失败"}`);
        }
      }
    },

    clearUserApiKey: () => {
      set({ userApiKey: "", userBaseUrl: "", userModel: "" });
      clearApiKeyConfig();
      toast("info", "API Key 已清除，将使用平台额度");
    },

    fetchQuota: async () => {
      try {
        const data = await api<{ quota: number; used: number; remaining: number }>("/api/user/quota");
        set({ tokenQuota: data.quota, tokensUsed: data.used });
      } catch {
        if (!get().userApiKey) {
          set({ tokenQuota: 100000, tokensUsed: 0 });
        }
      }
    },

    refreshAfterRecharge: async () => {
      await get().fetchQuota();
      toast("success", "充值成功！额度已到账");
    },

    setSettingsOpen: (open) => set({ isSettingsOpen: open }),
    setRechargeOpen: (open) => set({ isRechargeOpen: open }),
    setOnboardingOpen: (open) => set({ isOnboardingOpen: open }),
    completeOnboarding: () => {
      markOnboarded();
      set({ hasCompletedOnboarding: true, isOnboardingOpen: false });
    },

    login: async (phone: string, code: string) => {
      const result = await api<{ token: string; user: { nickname?: string; phone?: string } }>("/api/auth/sms/verify", {
        method: "POST",
        body: JSON.stringify({ phone, code }),
      });
      saveJwtToken(result.token);
      set({
        isLoggedIn: true,
        userNickname: result.user?.nickname || result.user?.phone || null,
      });
      await get().fetchQuota();
      toast("success", "登录成功");
    },

    logout: () => {
      clearJwtToken();
      set({ isLoggedIn: false, userNickname: null });
      toast("info", "已退出登录");
    },

    runPipeline: () => {
      const state = get();
      if (!state.sessionId) {
        toast("error", "请先创建会话");
        return;
      }
      if (state.isPipelineRunning) {
        toast("info", "Pipeline 正在运行中");
        return;
      }

      // 重置 pipeline 节点为 pending 状态
      const initialNodes: PipelineNodeState[] = PIPELINE_NODE_ORDER.map((name) => ({
        name,
        status: "pending" as const,
      }));

      set({
        pipelineNodes: initialNodes,
        pipelineResult: null,
        isPipelineRunning: true,
        pipelineRevisionCount: 0,
        error: null,
      });

      // Pipeline 专用的 SSE 事件处理
      function handlePipelineEvent(event: SSEEvent) {
        const currentNode = event.node as PipelineNodeName | undefined;

        switch (event.type) {
          case "pipeline_start":
            // Pipeline 已启动，无需额外处理
            break;

          case "node_start": {
            if (!currentNode) break;
            set((s) => ({
              pipelineNodes: s.pipelineNodes.map((n) =>
                n.name === currentNode
                  ? { ...n, status: "running", startedAt: Date.now(), output: "" }
                  : n
              ),
            }));
            break;
          }

          case "token": {
            if (!currentNode) break;
            set((s) => ({
              pipelineNodes: s.pipelineNodes.map((n) =>
                n.name === currentNode
                  ? { ...n, output: (n.output || "") + (event.token || "") }
                  : n
              ),
            }));
            break;
          }

          case "node_done": {
            if (!currentNode) break;
            set((s) => ({
              pipelineNodes: s.pipelineNodes.map((n) =>
                n.name === currentNode
                  ? {
                      ...n,
                      status: "completed",
                      completedAt: Date.now(),
                      output: event.output || n.output || "",
                      tokens: event.tokens || n.tokens,
                    }
                  : n
              ),
            }));
            // 特殊节点：更新画布/画像/验收结果到 store
            if (currentNode === "canvas_synthesis" && event.canvas_tree) {
              set({ discussionMap: event.canvas_tree as unknown as DiscussionMap });
            }
            if (currentNode === "portrait" && event.portrait) {
              set({ productPortrait: event.portrait as unknown as ProductPortrait });
            }
            break;
          }

          case "revision_start": {
            const revCount = event.revision_count || 1;
            // 将 cot 到 pm_acceptance 的节点重置为 pending
            const revisionStartIndex = PIPELINE_NODE_ORDER.indexOf("cot");
            const nodesToReset = PIPELINE_NODE_ORDER.slice(revisionStartIndex);
            set((s) => ({
              pipelineRevisionCount: revCount,
              pipelineNodes: s.pipelineNodes.map((n) =>
                nodesToReset.includes(n.name)
                  ? { ...n, status: "pending" as const, output: undefined, startedAt: undefined, completedAt: undefined }
                  : n
              ),
            }));
            break;
          }

          case "pipeline_done": {
            const acceptanceResult = event.acceptance_result
              ? {
                  passed: event.acceptance_result.passed,
                  gaps: event.acceptance_result.gaps || [],
                  suggestions: event.acceptance_result.suggestions || [],
                  summary: event.acceptance_result.summary || "",
                }
              : { passed: true, gaps: [], suggestions: [], summary: "" };

            const result: PipelineResult = {
              prd: event.prd || "",
              canvasTree: event.canvas_tree || {},
              productPortrait: event.product_portrait || {},
              acceptanceResult,
              revisionCount: event.revision_count || 0,
            };
            set({
              pipelineResult: result,
              isPipelineRunning: false,
            });
            if (acceptanceResult.passed) {
              toast("success", "Pipeline 完成，PM 验收通过");
            } else {
              toast("warning", `Pipeline 完成，但 PM 验收未通过（第 ${result.revisionCount} 次修订后仍有缺口）`);
            }
            break;
          }

          case "error": {
            const errorMsg = event.message || "Pipeline 执行出错";
            if (currentNode) {
              set((s) => ({
                pipelineNodes: s.pipelineNodes.map((n) =>
                  n.name === currentNode ? { ...n, status: "error" as const } : n
                ),
              }));
            }
            set({ error: errorMsg, isPipelineRunning: false });
            toast("error", errorMsg);
            break;
          }

          case "quota_deduct":
            set((s) => ({ tokensUsed: s.tokensUsed + (event.tokens || 0) }));
            get().fetchQuota();
            break;
        }
      }

      function handlePipelineDone() {
        // 确保运行状态被重置（pipeline_done 可能已处理）
        const current = get();
        if (current.isPipelineRunning) {
          set({ isPipelineRunning: false });
        }
      }

      function handlePipelineError(err: string) {
        set({ error: err, isPipelineRunning: false });
        toast("error", err);
      }

      abortController = createSSEConnection(
        "/api/brainstorm/pipeline",
        { session_id: state.sessionId },
        handlePipelineEvent,
        handlePipelineDone,
        handlePipelineError
      );
    },

    clearPipeline: () => {
      if (abortController && get().isPipelineRunning) {
        abortController.abort();
        abortController = null;
      }
      set({
        pipelineNodes: [],
        pipelineResult: null,
        isPipelineRunning: false,
        pipelineRevisionCount: 0,
      });
    },

  };
});

if (typeof window !== "undefined") {
  useSessionStore.getState().fetchQuota();
  useSessionStore.setState({ error: null });
}
