import { create } from "zustand";
import type { Message, Role, FeatureTree, SessionSummary, SessionPhase } from "@/lib/types";
import { createSSEConnection, type SSEEvent } from "@/lib/sse";
import { api } from "@/lib/api";
import { toast } from "@/components/Toast";

const VALID_PHASES: SessionPhase[] = ["define", "coach", "brainstorm", "interview"];

// 与后端 agent_loop.INTERRUPT_MARK 保持一致。
const INTERRUPT_MARK = "[生成中断]";

// Network-class errors that make the last send retryable
const RETRYABLE_ERROR_RE = /网络|服务暂不可用|timeout|timed ?out|failed to fetch|networkerror|connection|连接/i;

function isRetryableError(message: string): boolean {
  return RETRYABLE_ERROR_RE.test(message);
}

function normalizePhase(raw: string): SessionPhase {
  return (VALID_PHASES as string[]).includes(raw) ? (raw as SessionPhase) : "brainstorm";
}

interface FailedSend {
  content: string;
  targetRole: Role | "all";
}

interface SessionState {
  sessionId: string | null;
  phase: SessionPhase;
  messages: Message[];
  canvasTree: FeatureTree | null;
  isStreaming: boolean;
  streamingRole: string | null;
  streamingContent: string;
  error: string | null;
  historySessions: SessionSummary[];
  historyLoading: boolean;
  historyError: string | null;
  isHistoryOpen: boolean;
  interviewMode: "voice" | "text";
  isPlayingAudio: boolean;
  targetRole: Role | "all";
  coveredDimensions: string[];
  lastFailedSend: FailedSend | null;
  coachTriggeredFor: string | null;
  isGeneratingCanvas: boolean;

  loadSession: (id: string) => Promise<void>;
  sendMessage: (content: string, targetRole: Role | "all") => void;
  sendToCoach: (content: string) => void;
  skipCoach: () => void;
  startInterview: () => void;
  answerInterview: (answer: string) => void;
  generateCanvas: () => Promise<void>;
  setPhase: (phase: SessionState["phase"]) => void;
  clearError: () => void;
  clearLastFailedSend: () => void;
  abortStream: () => void;
  fetchHistory: () => Promise<void>;
  toggleHistory: () => void;
  setInterviewMode: (mode: "voice" | "text") => void;
  setPlayingAudio: (playing: boolean) => void;
  setTargetRole: (role: Role | "all") => void;
  setCoachTriggered: (sessionId: string | null) => void;
}

export const useSessionStore = create<SessionState>((set, get) => {
  let abortController: AbortController | null = null;
  let canvasUpdatePending = false;
  // Remember what was last sent so failures can offer a retry.
  let lastSent: FailedSend | null = null;
  // 并行脑暴时按角色分流 token，避免多角色内容混流（B110）。
  let roleBuffers: Record<string, string> = {};

  function joinedStreamingContent(): string {
    const roles = Object.keys(roleBuffers);
    if (roles.length <= 1) return roleBuffers[roles[0]] ?? "";
    return roles.map((r) => roleBuffers[r]).join("\n\n");
  }

  function resetStreamingBuffers() {
    roleBuffers = {};
  }

  async function autoUpdateCanvas(sid: string) {
    if (canvasUpdatePending) return;
    canvasUpdatePending = true;
    try {
      const tree = await api<FeatureTree>("/api/canvas/incremental", {
        method: "POST",
        body: JSON.stringify({ session_id: sid }),
      });
      set({ canvasTree: tree });
    } catch {
      // Canvas update is best-effort, don't disrupt chat
    } finally {
      canvasUpdatePending = false;
    }
  }

  function markFailedIfRetryable(message: string | undefined) {
    if (lastSent && message && isRetryableError(message)) {
      set({ lastFailedSend: lastSent });
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
        set({ streamingRole: event.role || null });
        break;
      case "token":
        if (event.token) {
          // 按角色分流累积；单一角色时即为完整内容（B110）。
          const role = event.role || "assistant";
          roleBuffers[role] = (roleBuffers[role] || "") + event.token;
          set({ streamingContent: joinedStreamingContent() });
        }
        break;
      case "role_done": {
        const role = event.role || "assistant";
        // 优先使用后端携带的全量内容，天然免疫并行 token 乱序（B110）。
        const content =
          typeof event.content === "string"
            ? event.content
            : roleBuffers[role] ?? get().streamingContent;
        delete roleBuffers[role];
        const state = get();
        if (content) {
          const msg: Message = {
            role: "assistant",
            content,
            role_name: event.role_name || event.role,
            timestamp: new Date().toISOString(),
          };
          set({
            messages: [...state.messages, msg],
            streamingContent: joinedStreamingContent(),
            streamingRole: Object.keys(roleBuffers)[0] ?? null,
          });
        } else {
          set({
            streamingContent: joinedStreamingContent(),
            streamingRole: Object.keys(roleBuffers)[0] ?? null,
          });
        }
        // Auto-update canvas after each role finishes speaking
        if (state.sessionId) {
          autoUpdateCanvas(state.sessionId);
        }
        break;
      }
      case "role_error": {
        // 单角色失败为非终结性错误：仅提示，不结束整轮流（B116）。
        // 同时把该角色已生成的部分落为带标记消息（与后端 _persist_partial
        // 落库的 content+"[生成中断]" 保持一致），并清除其缓冲（B125）。
        const role = event.role || "assistant";
        const partial = roleBuffers[role];
        delete roleBuffers[role];
        const state = get();
        if (partial) {
          const msg: Message = {
            role: "assistant",
            content: partial + INTERRUPT_MARK,
            role_name: event.role_name || event.role,
            timestamp: new Date().toISOString(),
          };
          set({
            messages: [...state.messages, msg],
            streamingContent: joinedStreamingContent(),
            streamingRole: Object.keys(roleBuffers)[0] ?? null,
            error: event.message || "生成中断",
          });
        } else {
          set({ error: event.message || "生成中断" });
        }
        break;
      }
      case "dimensions_update":
        if (Array.isArray(event.covered)) {
          set({ coveredDimensions: event.covered.map((d) => String(d)) });
        }
        break;
      case "error": {
        // 终结性错误（会话不存在/单角色流中断）：把已生成部分落为带标记
        // 消息（后端已落库相同内容），避免刷新后内容"复活"不一致（B125）。
        const errRole = event.role || "assistant";
        const partial = roleBuffers[errRole] || (Object.keys(roleBuffers).length === 1 ? Object.values(roleBuffers)[0] : "");
        resetStreamingBuffers();
        const state = get();
        if (partial) {
          const msg: Message = {
            role: "assistant",
            content: partial + INTERRUPT_MARK,
            role_name: errRole,
            timestamp: new Date().toISOString(),
          };
          set({
            messages: [...state.messages, msg],
            streamingContent: "",
            streamingRole: null,
            error: event.message || "未知错误",
            isStreaming: false,
          });
        } else {
          set({ error: event.message || "未知错误", isStreaming: false });
        }
        markFailedIfRetryable(event.message);
        break;
      }
    }
  }

  function handleDone() {
    const state = get();
    // 兜底：若仍有未 role_done 的残留缓冲（异常路径），按角色落为带标记消息。
    const pending = Object.entries(roleBuffers).filter(([, c]) => c);
    if (pending.length > 0) {
      const extra: Message[] = pending.map(([role, content]) => ({
        role: "assistant" as const,
        content: content + INTERRUPT_MARK,
        role_name: role,
        timestamp: new Date().toISOString(),
      }));
      set({
        messages: [...state.messages, ...extra],
        streamingContent: "",
        streamingRole: null,
        isStreaming: false,
      });
    } else {
      set({ isStreaming: false, streamingContent: "", streamingRole: null });
    }
    resetStreamingBuffers();
    // 并行脑暴结束后兜底刷新画布（B112）。
    if (get().sessionId) {
      autoUpdateCanvas(get().sessionId as string);
    }
  }

  function handleError(err: string) {
    // 错误统一由 ChatPanel 内联展示，避免 toast 双通道重复播报（B119）。
    set({ error: err, isStreaming: false });
    resetStreamingBuffers();
    // Connection-level failure — always offer a retry for the last send.
    if (lastSent) {
      set({ lastFailedSend: lastSent });
    }
  }

  return {
    sessionId: null,
    phase: "define",
    messages: [],
    canvasTree: null,
    isStreaming: false,
    streamingRole: null,
    streamingContent: "",
    error: null,
    historySessions: [],
    historyLoading: false,
    historyError: null,
    isHistoryOpen: false,
    interviewMode: "text",
    isPlayingAudio: false,
    targetRole: "all",
    coveredDimensions: [],
    lastFailedSend: null,
    coachTriggeredFor: null,
    isGeneratingCanvas: false,

    setTargetRole: (role) => set({ targetRole: role }),

    setCoachTriggered: (sessionId) => set({ coachTriggeredFor: sessionId }),

    loadSession: async (id: string) => {
      // 动态路由参数变化不卸载组件：切换会话前必须中断旧 SSE 流，
      // 否则旧会话的 token/role_done 会写入新会话状态（B124）。
      get().abortStream();
      set({ error: null, lastFailedSend: null, targetRole: "all" });
      resetStreamingBuffers();
      const session = await api<{
        id: string;
        phase: string;
        messages: Message[];
        canvas_tree: FeatureTree | null;
        interview_dimensions_covered?: string[];
      }>(`/api/session/${id}`);
      set({
        sessionId: session.id,
        phase: normalizePhase(session.phase),
        messages: session.messages || [],
        canvasTree: session.canvas_tree,
        coveredDimensions: Array.isArray(session.interview_dimensions_covered)
          ? session.interview_dimensions_covered.map((d) => String(d))
          : [],
      });
    },

    sendToCoach: (content: string) => {
      const state = get();
      if (!state.sessionId) return;

      if (abortController) {
        abortController.abort();
      }

      const userMsg: Message = {
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
      resetStreamingBuffers();

      abortController = createSSEConnection(
        "/api/brainstorm/coach",
        { session_id: state.sessionId, content },
        handleSSEEvent,
        handleDone,
        handleError
      );
    },

    sendMessage: (content: string, targetRole: Role | "all") => {
      const state = get();
      if (!state.sessionId) return;

      if (abortController) {
        abortController.abort();
      }

      lastSent = { content, targetRole };

      const userMsg: Message = {
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
      resetStreamingBuffers();

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

    skipCoach: () => {
      get().sendMessage("（跳过引导，直接开始脑暴）", "all");
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
      resetStreamingBuffers();

      abortController = createSSEConnection(
        "/api/interview/start",
        { session_id: state.sessionId },
        handleSSEEvent,
        handleDone,
        handleError
      );
    },

    answerInterview: (answer: string) => {
      const state = get();
      if (!state.sessionId) return;

      if (abortController) {
        abortController.abort();
      }

      const userMsg: Message = {
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
      resetStreamingBuffers();

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
      if (!state.sessionId || state.isGeneratingCanvas) return;
      set({ isGeneratingCanvas: true });
      try {
        const tree = await api<FeatureTree>("/api/canvas/generate", {
          method: "POST",
          body: JSON.stringify({ session_id: state.sessionId }),
        });
        set({ canvasTree: tree });
        toast.success("画布已更新");
      } catch (err) {
        toast.error(err instanceof Error ? err.message : "生成画布失败，请稍后重试");
      } finally {
        set({ isGeneratingCanvas: false });
      }
    },

    setPhase: (phase) => set({ phase }),
    clearError: () => set({ error: null }),

    clearLastFailedSend: () => set({ lastFailedSend: null }),

    abortStream: () => {
      if (abortController) {
        abortController.abort();
        abortController = null;
      }
      resetStreamingBuffers();
      set({ isStreaming: false, streamingContent: "", streamingRole: null });
    },

    fetchHistory: async () => {
      set({ historyLoading: true, historyError: null });
      try {
        const sessions = await api<SessionSummary[]>("/api/session");
        set({ historySessions: sessions, historyLoading: false });
      } catch (err) {
        set({
          historyError: err instanceof Error ? err.message : "加载历史会话失败",
          historyLoading: false,
        });
      }
    },

    toggleHistory: () => set((s) => ({ isHistoryOpen: !s.isHistoryOpen })),

    setInterviewMode: (mode) => set({ interviewMode: mode }),

    setPlayingAudio: (playing) => set({ isPlayingAudio: playing }),

  };
});
