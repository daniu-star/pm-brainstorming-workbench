import { create } from "zustand";
import type { Message, Role, DiscussionMap, SessionSummary, ProductPortrait } from "@/lib/types";
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
  phase: "define" | "coach" | "brainstorm" | "interview";
  messages: Message[];
  discussionMap: DiscussionMap | null;
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
  sendMessage: (content: string, targetRole: Role | "all") => void;
  sendToCoach: (content: string) => void;
  startInterview: () => void;
  answerInterview: (answer: string) => void;
  generateCanvas: () => Promise<void>;
  generateProductPortrait: () => Promise<void>;
  setPhase: (phase: SessionState["phase"]) => void;
  clearError: () => void;
  abortStream: () => void;
  fetchHistory: () => Promise<void>;
  toggleHistory: () => void;
  setInterviewMode: (mode: "voice" | "text") => void;
  setPlayingAudio: (playing: boolean) => void;
}

export const useSessionStore = create<SessionState>((set, get) => {
  let abortController: AbortController | null = null;
  let canvasUpdatePending = false;

  async function autoUpdateCanvas(sid: string) {
    if (canvasUpdatePending) return;
    canvasUpdatePending = true;
    try {
      const map = await api<DiscussionMap>("/api/canvas/incremental", {
        method: "POST",
        body: JSON.stringify({ session_id: sid }),
      });
      set({ discussionMap: map });
    } catch {
      // Canvas update is best-effort, don't disrupt chat
    } finally {
      canvasUpdatePending = false;
    }
  }

  function handleSSEEvent(event: SSEEvent) {
    switch (event.type) {
      case "phase_change":
        if (event.phase === "coach" || event.phase === "interview" || event.phase === "brainstorm") {
          set({ phase: event.phase });
        }
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
        });
        // Auto-update canvas after each role finishes speaking
        if (state.sessionId) {
          autoUpdateCanvas(state.sessionId);
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
    set({ error: err, isStreaming: false, connectionStatus: err.includes("重连") ? "reconnecting" : "disconnected" });
    if (!err.includes("重连")) {
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
    phase: "define",
    messages: [],
    discussionMap: null,
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
    setTargetRole: (role) => set({ targetRole: role }),

    createSession: async (problem: string) => {
      const session = await api<{ id: string }>("/api/session", {
        method: "POST",
        body: JSON.stringify({ problem_statement: problem }),
      });
      set({
        sessionId: session.id,
        phase: "brainstorm",
        messages: [],
        discussionMap: null,
      });
    },

    loadSession: async (id: string) => {
      const session = await api<{
        id: string;
        phase: string;
        messages: Message[];
        discussion_map: DiscussionMap | null;
        product_portrait: ProductPortrait | null;
      }>(`/api/session/${id}`);
      set({
        sessionId: session.id,
        phase: (session.phase || "define") as SessionState["phase"],
        messages: session.messages || [],
        discussionMap: session.discussion_map,
        productPortrait: session.product_portrait || null,
      });
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
        isStreaming: true,
        streamingContent: "",
        error: null,
      });

      abortController = createSSEConnection(
        "/api/interview/start",
        { session_id: state.sessionId },
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
      };
      set({
        messages: [...state.messages, userMsg],
        isStreaming: true,
        streamingContent: "",
        error: null,
      });

      abortController = createSSEConnection(
        "/api/interview/respond",
        {
          session_id: state.sessionId,
          answer,
        },
        handleSSEEvent,
        handleDone,
        handleError
      );
    },

    generateCanvas: async () => {
      const state = get();
      if (!state.sessionId) return;
      const map = await api<DiscussionMap>("/api/canvas/generate", {
        method: "POST",
        body: JSON.stringify({ session_id: state.sessionId }),
      });
      set({ discussionMap: map });
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

    setPhase: (phase) => set({ phase }),
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
      api("/api/user/apikey", {
        method: "POST",
        body: JSON.stringify({ api_key: key, base_url: baseUrl, model }),
      }).catch(() => {});
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
          toast("success", "API Key 已保存并验证通过，所有请求将使用你的 Key");
        } catch (err) {
          toast("error", `API Key 验证失败：${err instanceof Error ? err.message : "连接失败"}`);
        }
      }
    },

    clearUserApiKey: () => {
      set({ userApiKey: "", userBaseUrl: "", userModel: "" });
      clearApiKeyConfig();
      api("/api/user/apikey", {
        method: "POST",
        body: JSON.stringify({ api_key: "", base_url: "", model: "" }),
      }).catch(() => {});
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
      toast("success", "登录成功");
    },

    logout: () => {
      clearJwtToken();
      set({ isLoggedIn: false, userNickname: null });
      toast("info", "已退出登录");
    },

  };
});

if (typeof window !== "undefined") {
  useSessionStore.getState().fetchQuota();
  useSessionStore.setState({ error: null });
}
