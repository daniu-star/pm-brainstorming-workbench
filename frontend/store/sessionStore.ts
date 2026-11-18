/**
 * 会话 Store（M1：会话隔离 + 事件去重 + 确定性画布）
 *
 * 兼容策略：旧组件（ChatPanel / InputBox / RoleSelector / InterviewView / Header /
 * HistoryDrawer / CanvasToolbar / NavButtons）按旧的扁平字段消费（`messages` / `phase` /
 * `canvasTree` / `isStreaming` ...），这些字段全部保留。v2 数据放在以 id 为键的缓存里：
 * - `currentSessionId` + `generation`：异步写回守卫（切换会话 / 登出 +1）；
 * - `messagesBySession[sessionId]`：v2 MessageView，按 `message_id` 去重；
 * - `runs[runId]` / `lastEventSeq[runId]`：事件按 `run_id + event_seq` 去重；
 * - `productState` / `productVersion`：版本单调，旧版本丢弃并记 warning；
 * - `canvas`：只由 `product_state.updated` 触发 GET /canvas，带 latest/stale/generating 状态。
 *
 * 降级：v2 端点不存在时（404/501/网络错误）自动回退旧 `/api/session/{id}`，
 * 并置 `legacyMode=true`，此时旧的 incremental 画布轮询路径才会启用。
 */

import { create } from "zustand";
import type { FeatureTree, Message, Role, SessionPhase as LegacyPhase, SessionSummary } from "@/lib/types";
import { createSSEConnection, openRunEvents, type SSEEvent } from "@/lib/sse";
import {
  api,
  cancelRun as apiCancelRun,
  errorMessage,
  getAudit,
  getCanvas,
  getMessage as apiGetMessage,
  getProductState,
  getRun,
  getRunSnapshot,
  getSession,
  isEndpointUnavailable,
  listMessages,
  newRequestId,
  retryFailed as apiRetryFailed,
  runEventsUrl,
  sendCommand as apiSendCommand,
} from "@/lib/api";
import type {
  Action,
  AuditSnapshot,
  CanvasProjection,
  CommandReceipt,
  ControlReceipt,
  MessageStage,
  MessageView,
  ProductState,
  RoleCode,
  RunEvent,
  RunReceipt,
  RunSnapshot,
  RunView,
  SessionPhase as V2Phase,
  SessionView,
  StartAuditRecovery,
  TargetRole,
} from "@/lib/contracts.generated";
import { isTerminalRunStatus } from "@/lib/contracts.generated";
import {
  acceptProductVersion,
  isGuardCurrent,
  mergeMessagesById,
  shouldRefetchCanvas,
  type SessionGuard,
} from "@/lib/stateGuards";
import { toast } from "@/components/Toast";

// --------------------------------------------------------------------------- //
// 常量 / 类型
// --------------------------------------------------------------------------- //

const VALID_PHASES: LegacyPhase[] = ["define", "coach", "brainstorm", "interview"];
const INTERRUPT_MARK = "[生成中断]";
const RETRYABLE_ERROR_RE = /网络|服务暂不可用|timeout|timed ?out|failed to fetch|networkerror|connection|连接/i;
const LOCAL_MESSAGE_PREFIX = "local-";
const MAX_WARNINGS = 5;

function isRetryableError(message: string): boolean {
  return RETRYABLE_ERROR_RE.test(message);
}

function normalizePhase(raw: string): LegacyPhase {
  return (VALID_PHASES as string[]).includes(raw) ? (raw as LegacyPhase) : "brainstorm";
}

/** v2 phase → 旧 UI phase（旧组件只认 define/coach/brainstorm/interview）。 */
function mapV2Phase(phase: V2Phase): LegacyPhase {
  switch (phase) {
    case "clarifying":
      return "coach";
    case "brainstorming":
      return "brainstorm";
    case "auditing":
    case "audit_completed":
      return "interview";
    default:
      return "brainstorm";
  }
}

function phaseToStage(phase: LegacyPhase): MessageStage {
  if (phase === "coach" || phase === "define") return "clarify";
  if (phase === "interview") return "audit";
  return "brainstorm";
}

const ROLE_CODE_LABELS: Record<RoleCode, string> = {
  coach: "产品教练",
  cto: "技术负责人",
  designer: "设计师",
  ops: "运营负责人",
  target_user: "目标用户",
  moderator: "主持人",
  interviewer: "AI 面试官",
};

function mapTargetRole(target: Role | "all"): TargetRole {
  switch (target) {
    case "all":
      return "all";
    case "cto":
      return "cto";
    case "designer":
      return "designer";
    case "ops":
      return "ops";
    case "user":
      return "target_user";
    default:
      return "auto";
  }
}

function toLegacyMessage(view: MessageView): Message {
  return {
    role: view.sender_kind === "human" ? "user" : "assistant",
    content: view.content,
    role_name: view.role_code ? ROLE_CODE_LABELS[view.role_code] : undefined,
    timestamp: view.created_at,
  };
}

export type CanvasStatus = "generating" | "latest" | "stale" | "error";

export interface CanvasState {
  projection: CanvasProjection | null;
  productVersion: number | null;
  status: CanvasStatus;
  error: string | null;
}

export interface RunState {
  runId: string;
  sessionId: string;
  status: RunView["status"];
  action: Action | null;
  lastEventSeq: number;
  errorCode: string | null;
  errorMessage: string | null;
}

function emptyCanvas(): CanvasState {
  return { projection: null, productVersion: null, status: "latest", error: null };
}

interface FailedSend {
  content: string;
  targetRole: Role | "all";
}

interface LegacySessionPayload {
  id: string;
  phase: string;
  messages: Message[];
  canvas_tree: FeatureTree | null;
  interview_dimensions_covered?: string[];
}

export interface SendCommandOptions {
  expectedSessionVersion?: number | null;
  expectedProductVersion?: number | null;
}

interface SessionState {
  // ---- 旧字段（组件契约，勿改） ----
  sessionId: string | null;
  phase: LegacyPhase;
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

  // ---- v2 状态 ----
  currentSessionId: string | null;
  /** 每次 loadSession / reset +1，用来丢弃过期异步结果 */
  generation: number;
  sessionLoading: boolean;
  sessionView: SessionView | null;
  /** true = v2 端点不可用，已回退旧路由 */
  legacyMode: boolean;
  messagesBySession: Record<string, MessageView[]>;
  runs: Record<string, RunState>;
  runSnapshots: Record<string, RunSnapshot>;
  audits: Record<string, AuditSnapshot>;
  productStates: Record<string, ProductState>;
  productState: ProductState | null;
  productVersion: number;
  canvas: CanvasState | null;
  canvasLoading: boolean;
  canvases: Record<string, CanvasState>;
  errors: Record<string, string | null>;
  stateWarnings: string[];
  lastEventSeq: Record<string, number>;
  subscribingRunId: string | null;
  activeRunId: string | null;
  lastFailedRunId: string | null;
  streamingMessageId: string | null;

  // ---- 旧 action ----
  loadSession: (id: string) => Promise<void>;
  sendMessage: (content: string, targetRole: Role | "all") => void;
  sendToCoach: (content: string) => void;
  skipCoach: () => void;
  startInterview: () => void;
  answerInterview: (answer: string) => void;
  generateCanvas: () => Promise<void>;
  setPhase: (phase: LegacyPhase) => void;
  clearError: () => void;
  clearLastFailedSend: () => void;
  abortStream: () => void;
  fetchHistory: () => Promise<void>;
  toggleHistory: () => void;
  setInterviewMode: (mode: "voice" | "text") => void;
  setPlayingAudio: (playing: boolean) => void;
  setTargetRole: (role: Role | "all") => void;
  setCoachTriggered: (sessionId: string | null) => void;

  // ---- v2 action ----
  reloadSession: () => Promise<void>;
  sendCommand: (
    action: Action,
    payload?: Record<string, unknown>,
    options?: SendCommandOptions
  ) => Promise<CommandReceipt>;
  subscribeRun: (runId: string) => void;
  unsubscribe: () => void;
  cancelRun: (runId?: string) => Promise<void>;
  retryFailedRun: (runId?: string) => Promise<void>;
  refreshCanvas: (productVersion?: number) => Promise<void>;
  /** 拉取审计快照并写入 `audits`（审计页用；失败静默，不打断页面） */
  hydrateAuditById: (auditId: string) => Promise<void>;
  clearStateWarnings: () => void;
  reset: () => void;
}

// --------------------------------------------------------------------------- //
// 回执类型守卫
// --------------------------------------------------------------------------- //

function isRunReceipt(receipt: CommandReceipt): receipt is RunReceipt {
  return typeof (receipt as RunReceipt).run_id === "string";
}

function isControlReceipt(receipt: CommandReceipt): receipt is ControlReceipt {
  return typeof (receipt as ControlReceipt).control_id === "string";
}

function isStartAuditRecovery(receipt: CommandReceipt): receipt is StartAuditRecovery {
  return (
    !("run_id" in receipt) &&
    !("control_id" in receipt) &&
    typeof (receipt as StartAuditRecovery).audit_id === "string"
  );
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    if (typeof setTimeout === "function") setTimeout(resolve, ms);
    else resolve();
  });
}

// --------------------------------------------------------------------------- //
// Store
// --------------------------------------------------------------------------- //

export const useSessionStore = create<SessionState>((set, get) => {
  let abortController: AbortController | null = null;
  let canvasUpdatePending = false;
  let lastSent: FailedSend | null = null;
  let roleBuffers: Record<string, string> = {};
  let runStreamCancel: (() => void) | null = null;
  /** 每个 run 的事件去重集合（跨重连复用） */
  const seenEvents = new Map<string, Set<string>>();

  function capture(): SessionGuard {
    const state = get();
    return { sessionId: state.currentSessionId, generation: state.generation };
  }

  function assertCurrent(captured: SessionGuard): boolean {
    return isGuardCurrent(capture(), captured);
  }

  function pushWarning(warning: string | null | undefined): void {
    if (!warning) return;
    set((state) => ({
      stateWarnings: [...state.stateWarnings, warning].slice(-MAX_WARNINGS),
    }));
  }

  function setError(message: string | null): void {
    const sessionId = get().currentSessionId;
    set((state) => ({
      error: message,
      errors: sessionId ? { ...state.errors, [sessionId]: message } : state.errors,
    }));
  }

  function joinedStreamingContent(): string {
    const roles = Object.keys(roleBuffers);
    if (roles.length <= 1) return roleBuffers[roles[0]] ?? "";
    return roles.map((role) => roleBuffers[role]).join("\n\n");
  }

  function resetStreamingBuffers(): void {
    roleBuffers = {};
  }

  function markFailedIfRetryable(message: string | undefined): void {
    if (lastSent && message && isRetryableError(message)) {
      set({ lastFailedSend: lastSent });
    }
  }

  // ---- 消息缓存 ---------------------------------------------------------- //

  /** 把 v2 MessageView 投影成旧组件消费的 Message[]（只用于当前会话）。 */
  function syncLegacyMessages(sessionId: string): void {
    const views = get().messagesBySession[sessionId] ?? [];
    set({ messages: views.filter((view) => view.sender_kind !== "system").map(toLegacyMessage) });
  }

  function mergeMessages(sessionId: string, incoming: MessageView[]): void {
    const state = get();
    const existing = state.messagesBySession[sessionId] ?? [];
    // 服务端消息回来时清掉本地乐观消息，避免同一句用户输入出现两次
    const cleaned = existing.filter((item) => !item.id.startsWith(LOCAL_MESSAGE_PREFIX));
    const merged = mergeMessagesById(cleaned, incoming);
    set((current) => ({
      messagesBySession: { ...current.messagesBySession, [sessionId]: merged.items },
    }));
    if (get().currentSessionId === sessionId) syncLegacyMessages(sessionId);
  }

  function nextSeq(sessionId: string): number {
    const views = get().messagesBySession[sessionId] ?? [];
    return (
      views.reduce(
        (max, view) => (typeof view.seq === "number" && view.seq > max ? view.seq : max),
        0
      ) + 1
    );
  }

  function findMessage(sessionId: string, messageId: string): MessageView | undefined {
    return (get().messagesBySession[sessionId] ?? []).find((view) => view.id === messageId);
  }

  function appendOptimisticUserMessage(sessionId: string, content: string): void {
    const state = get();
    const localId = `${LOCAL_MESSAGE_PREFIX}${newRequestId()}`;
    const view: MessageView = {
      id: localId,
      session_id: sessionId,
      seq: nextSeq(sessionId),
      sender_kind: "human",
      role_code: null,
      stage: phaseToStage(state.phase),
      content,
      status: "draft",
      run_id: null,
      audit_id: null,
      created_at: new Date().toISOString(),
    };
    mergeMessages(sessionId, [view]);
    set({ streamingContent: "", error: null });
  }

  // ---- 产品状态 / 画布 ---------------------------------------------------- //

  async function refreshCanvas(productVersion?: number): Promise<void> {
    const state = get();
    const sessionId = state.currentSessionId;
    if (!sessionId) return;
    const captured: SessionGuard = { sessionId, generation: state.generation };
    const version =
      productVersion ?? state.productVersion ?? state.sessionView?.current_product_version;

    set((current) => ({
      canvas: {
        projection: current.canvas?.projection ?? null,
        productVersion: current.canvas?.productVersion ?? null,
        status: "generating" as CanvasStatus,
        error: null,
      },
      canvasLoading: true,
      isGeneratingCanvas: true,
    }));

    try {
      const projection = await getCanvas(sessionId, version);
      if (!assertCurrent(captured)) return;
      const previousVersion = get().canvas?.productVersion ?? 0;
      if (projection.product_version < previousVersion) {
        pushWarning(`丢弃旧画布版本 ${projection.product_version}（当前 ${previousVersion}）`);
        return;
      }
      const nextCanvas: CanvasState = {
        projection,
        productVersion: projection.product_version,
        status: "latest",
        error: null,
      };
      set((current) => ({
        canvas: nextCanvas,
        canvases: { ...current.canvases, [sessionId]: nextCanvas },
        isGeneratingCanvas: false,
        canvasLoading: false,
      }));
    } catch (error) {
      if (!assertCurrent(captured)) return;
      const message = errorMessage(error);
      set((current) => {
        const failedCanvas: CanvasState = {
          projection: current.canvas?.projection ?? null,
          productVersion: current.canvas?.productVersion ?? null,
          status: "error",
          error: message,
        };
        return {
          canvas: failedCanvas,
          canvases: { ...current.canvases, [sessionId]: failedCanvas },
          isGeneratingCanvas: false,
          canvasLoading: false,
        };
      });
      pushWarning(`画布加载失败：${message}`);
    }
  }

  async function hydrateProductAndCanvas(
    sessionId: string,
    captured: SessionGuard,
    hints: { productVersion?: number; canvasVersion?: number } = {}
  ): Promise<void> {
    let productState: ProductState | null = null;
    try {
      productState = await getProductState(sessionId);
    } catch {
      // 产品状态可能是空的（新会话），不阻塞主流程
    }
    if (!assertCurrent(captured)) return;

    let canvasVersion = hints.canvasVersion ?? hints.productVersion;
    if (productState) {
      const acceptance = acceptProductVersion(get().productVersion, productState.version);
      if (acceptance.accepted) {
        set((state) => ({
          productState,
          productVersion: acceptance.version,
          productStates: { ...state.productStates, [sessionId]: productState as ProductState },
        }));
        canvasVersion = acceptance.version;
      } else {
        pushWarning(acceptance.warning);
      }
    }

    if (!shouldRefetchCanvas(get().canvas?.productVersion ?? null, Number(canvasVersion ?? 0))) {
      return;
    }
    await refreshCanvas(canvasVersion);
  }

  async function hydrateProductStateOnly(sessionId: string, version: number): Promise<void> {
    try {
      const state = await getProductState(sessionId, version);
      if (get().currentSessionId !== sessionId) return;
      const acceptance = acceptProductVersion(get().productVersion, state.version);
      if (!acceptance.accepted) {
        pushWarning(acceptance.warning);
        return;
      }
      set((current) => ({
        productState: state,
        productVersion: acceptance.version,
        productStates: { ...current.productStates, [sessionId]: state },
      }));
    } catch {
      // 产品状态刷新失败：保留版本号，画布徽标会提示重试
    }
  }

  async function hydrateMessages(sessionId: string, captured: SessionGuard): Promise<void> {
    try {
      const page = await listMessages(sessionId, { limit: 100 });
      if (!assertCurrent(captured)) return;
      mergeMessages(sessionId, page.items ?? []);
    } catch {
      // 消息补齐失败不阻塞会话加载（首屏仍可交互）
    }
  }

  async function hydrateAudit(auditId: string, captured: SessionGuard): Promise<void> {
    if (!auditId) return;
    try {
      const audit = await getAudit(auditId);
      if (!assertCurrent(captured)) return;
      set((state) => ({ audits: { ...state.audits, [audit.id]: audit } }));
    } catch {
      // 审计快照按需拉取，失败不阻塞
    }
  }

  async function refreshMessage(messageId: string, sessionId: string): Promise<void> {
    try {
      const view = await apiGetMessage(messageId);
      if (get().currentSessionId !== sessionId) return;
      mergeMessages(sessionId, [view]);
    } catch {
      // 消息补拉失败：保留流式占位内容
    }
  }

  // ---- Run 状态 / 事件 ---------------------------------------------------- //

  function updateRunState(runId: string, patch: Partial<RunState>): void {
    set((state) => {
      const existing: RunState = state.runs[runId] ?? {
        runId,
        sessionId: state.currentSessionId ?? "",
        status: "running",
        action: null,
        lastEventSeq: 0,
        errorCode: null,
        errorMessage: null,
      };
      return { runs: { ...state.runs, [runId]: { ...existing, ...patch } } };
    });
  }

  function statusForEvent(event: RunEvent): RunView["status"] | null {
    switch (event.type) {
      case "run.queued":
        return "queued";
      case "run.started":
        return "running";
      case "run.completed": {
        const raw = String((event.payload as Record<string, unknown> | null)?.status ?? "");
        return raw === "partial_succeeded" ? "partial_succeeded" : "succeeded";
      }
      case "run.failed":
        return "failed";
      case "run.cancelled":
        return "cancelled";
      default:
        return null;
    }
  }

  function handleRunEvent(event: RunEvent): void {
    const lastSeq = get().lastEventSeq[event.run_id] ?? 0;
    if (event.event_seq <= lastSeq) return; // 事件已处理过（run_id + event_seq 去重）
    set((state) => ({
      lastEventSeq: { ...state.lastEventSeq, [event.run_id]: event.event_seq },
    }));

    const sessionId = event.session_id;
    const isCurrentSession = sessionId === get().currentSessionId;
    const payload = (event.payload ?? {}) as Record<string, unknown>;
    const status = statusForEvent(event);
    if (status) updateRunState(event.run_id, { status, lastEventSeq: event.event_seq });

    if (!isCurrentSession) return; // 其它会话的事件只更新缓存

    switch (event.type) {
      case "run.started": {
        set({ isStreaming: true });
        break;
      }
      case "role.output.updated": {
        const messageId = typeof payload.message_id === "string" ? payload.message_id : "";
        const content =
          typeof payload.content === "string"
            ? payload.content
            : typeof payload.text === "string"
              ? payload.text
              : "";
        if (!messageId) break;
        const roleCode = (payload.role_code as RoleCode | undefined) ?? null;
        const existing = findMessage(sessionId, messageId);
        const view: MessageView =
          existing ?? {
            id: messageId,
            session_id: sessionId,
            seq: nextSeq(sessionId),
            sender_kind: "agent",
            role_code: roleCode,
            stage: phaseToStage(get().phase),
            content,
            status: "draft",
            run_id: event.run_id,
            audit_id: null,
            created_at: event.occurred_at,
          };
        // 同一 message_id 替换（不追加）
        mergeMessages(sessionId, [
          { ...view, content, status: "draft", role_code: roleCode ?? view.role_code },
        ]);
        set({
          streamingContent: content,
          streamingRole: roleCode ?? view.role_code ?? null,
          streamingMessageId: messageId,
          isStreaming: true,
        });
        break;
      }
      case "role.completed": {
        const messageId = typeof payload.message_id === "string" ? payload.message_id : "";
        if (messageId) {
          const current = findMessage(sessionId, messageId);
          if (!current || current.status === "draft") {
            void refreshMessage(messageId, sessionId);
          }
        }
        set({ streamingMessageId: null });
        break;
      }
      case "product_state.updated": {
        // 只接受单调递增的 product_version，再由版本触发 GET /canvas（不依赖事件次数）
        const rawVersion = Number(payload.product_version ?? payload.version ?? 0);
        const acceptance = acceptProductVersion(get().productVersion, rawVersion);
        if (!acceptance.accepted) {
          pushWarning(acceptance.warning);
          break;
        }
        set((state) => ({
          productVersion: acceptance.version,
          sessionView: state.sessionView
            ? {
                ...state.sessionView,
                current_product_version: Math.max(
                  state.sessionView.current_product_version,
                  acceptance.version
                ),
              }
            : state.sessionView,
          canvas: {
            ...(state.canvas ?? emptyCanvas()),
            status: "generating" as CanvasStatus,
          },
        }));
        void refreshCanvas(acceptance.version);
        void hydrateProductStateOnly(sessionId, acceptance.version);
        break;
      }
      case "audit.updated": {
        const audit = payload.audit;
        if (
          audit &&
          typeof audit === "object" &&
          typeof (audit as AuditSnapshot).id === "string"
        ) {
          const snapshot = audit as AuditSnapshot;
          set((state) => ({ audits: { ...state.audits, [snapshot.id]: snapshot } }));
        }
        break;
      }
      case "run.recovered": {
        const rawVersion = Number(payload.product_version ?? 0);
        if (rawVersion > 0 && acceptProductVersion(get().productVersion, rawVersion).accepted) {
          set({ productVersion: rawVersion });
        }
        break;
      }
      case "run.completed":
      case "run.failed":
      case "run.cancelled": {
        const terminalStatus = status ?? "failed";
        updateRunState(event.run_id, {
          status: terminalStatus,
          errorCode: typeof payload.error_code === "string" ? payload.error_code : null,
          errorMessage:
            typeof payload.error_message === "string" ? payload.error_message : null,
          lastEventSeq: event.event_seq,
        });
        if (terminalStatus === "failed") {
          setError(
            typeof payload.error_message === "string" ? payload.error_message : "任务执行失败"
          );
          set({ lastFailedRunId: event.run_id });
        } else {
          set({ lastFailedRunId: null });
        }
        const finalSessionVersion = payload.session_version;
        if (typeof finalSessionVersion === "number") {
          set((state) => ({
            sessionView: state.sessionView
              ? {
                  ...state.sessionView,
                  session_version: Math.max(
                    state.sessionView.session_version,
                    finalSessionVersion
                  ),
                }
              : state.sessionView,
          }));
        }
        if (!get().subscribingRunId) {
          // 事件流已经断开（例如从快照恢复），主动收尾
          void finalizeRun(event.run_id, capture());
        }
        break;
      }
      default:
        break;
    }
  }

  function parseSnapshotMessages(snapshot: RunSnapshot): MessageView[] {
    const raw = (snapshot.messages ?? []) as unknown[];
    const views: MessageView[] = [];
    for (const item of raw) {
      if (!item || typeof item !== "object") continue;
      const record = item as Record<string, unknown>;
      if (typeof record.id !== "string" || typeof record.content !== "string") continue;
      views.push({
        id: record.id,
        session_id:
          typeof record.session_id === "string" ? record.session_id : snapshot.session_id,
        seq: typeof record.seq === "number" ? record.seq : views.length + 1,
        sender_kind: (record.sender_kind as MessageView["sender_kind"]) ?? "agent",
        role_code: (record.role_code as MessageView["role_code"]) ?? null,
        stage: (record.stage as MessageView["stage"]) ?? "brainstorm",
        content: record.content,
        status: (record.status as MessageView["status"]) ?? "completed",
        run_id: typeof record.run_id === "string" ? record.run_id : snapshot.run_id,
        audit_id: typeof record.audit_id === "string" ? record.audit_id : null,
        created_at:
          typeof record.created_at === "string"
            ? record.created_at
            : new Date().toISOString(),
      });
    }
    return views;
  }

  async function finalizeRun(runId: string, captured: SessionGuard): Promise<void> {
    const sessionId = captured.sessionId;
    if (!sessionId) return;
    set({
      isStreaming: false,
      streamingContent: "",
      streamingRole: null,
      streamingMessageId: null,
      subscribingRunId: null,
    });
    runStreamCancel = null;

    try {
      const results = await Promise.allSettled([
        getRunSnapshot(runId),
        getRun(runId),
        getSession(sessionId),
      ]);
      if (!assertCurrent(captured)) return;

      const snapshot = results[0].status === "fulfilled" ? results[0].value : null;
      const run = results[1].status === "fulfilled" ? results[1].value : null;
      const session = results[2].status === "fulfilled" ? results[2].value : null;

      if (snapshot) {
        set((state) => ({ runSnapshots: { ...state.runSnapshots, [runId]: snapshot } }));
        const snapshotMessages = parseSnapshotMessages(snapshot);
        if (snapshotMessages.length > 0) mergeMessages(sessionId, snapshotMessages);
      }
      if (run) {
        updateRunState(runId, {
          status: run.status,
          action: run.action,
          errorCode: run.error_code,
          errorMessage: run.error_message,
          lastEventSeq: Math.max(
            get().lastEventSeq[runId] ?? 0,
            snapshot?.latest_event_seq ?? 0
          ),
        });
        if (run.status === "failed") {
          setError(run.error_message ?? "任务执行失败");
          set({ lastFailedRunId: runId });
        }
      }
      if (session) set({ sessionView: session, phase: mapV2Phase(session.phase) });

      const productVersion = Math.max(
        session?.current_product_version ?? 0,
        snapshot?.product_version ?? 0,
        get().productVersion
      );
      await Promise.all([
        hydrateMessages(sessionId, captured),
        hydrateProductAndCanvas(sessionId, captured, { productVersion }),
        // 审计运行结束后 refresh active_question_id 等状态，否则审计页停在旧问题
        hydrateAudit(
          session?.current_audit_id ?? get().sessionView?.current_audit_id ?? "",
          captured
        ),
      ]);
      if (!assertCurrent(captured)) return;

      if (session?.active_run_id && session.active_run_id !== runId) {
        get().subscribeRun(session.active_run_id);
      }
    } catch (error) {
      if (assertCurrent(captured)) setError(errorMessage(error));
    }
  }

  async function reconnectRun(
    runId: string,
    captured: SessionGuard,
    attempt: number
  ): Promise<void> {
    await sleep(600 * (attempt + 1));
    if (!assertCurrent(captured)) return;
    try {
      const run = await getRun(runId);
      if (!assertCurrent(captured)) return;
      if (isTerminalRunStatus(run.status)) {
        await finalizeRun(runId, captured);
        return;
      }
      if (attempt >= 2) {
        setError("事件流已断开，请刷新页面继续");
        set({ isStreaming: false, subscribingRunId: null });
        return;
      }
      set({ subscribingRunId: null });
      get().subscribeRun(runId);
    } catch (error) {
      if (assertCurrent(captured)) {
        setError(errorMessage(error));
        set({ isStreaming: false });
      }
    }
  }

  function openRunStream(runId: string): void {
    const state = get();
    const sessionId = state.currentSessionId;
    if (!sessionId) return;
    const captured: SessionGuard = { sessionId, generation: state.generation };
    const seen = seenEvents.get(runId) ?? new Set<string>();
    seenEvents.set(runId, seen);
    const afterSeq = state.lastEventSeq[runId] ?? 0;

    runStreamCancel = openRunEvents(
      runEventsUrl(runId, afterSeq > 0 ? afterSeq : undefined),
      {
        afterSeq: afterSeq > 0 ? afterSeq : undefined,
        seen,
        onEvent: handleRunEvent,
        onError: (error) => {
          if (assertCurrent(captured)) setError(errorMessage(error));
        },
        onClose: (reason) => {
          if (reason === "terminal") {
            void finalizeRun(runId, captured);
          } else if (reason === "eof") {
            void reconnectRun(runId, captured, 0);
          }
        },
      }
    );
    set({ subscribingRunId: runId, activeRunId: runId, isStreaming: true });
  }

  function applyReceipt(receipt: CommandReceipt): void {
    if (isStartAuditRecovery(receipt)) {
      const runId = receipt.current_run_id ?? receipt.latest_run_id;
      set((current) => ({
        sessionView: current.sessionView
          ? {
              ...current.sessionView,
              session_version: Math.max(
                current.sessionView.session_version,
                receipt.session_version
              ),
              current_audit_id: receipt.audit_id,
            }
          : current.sessionView,
        isStreaming: Boolean(runId),
      }));
      if (runId) get().subscribeRun(runId);
      else void hydrateAudit(receipt.audit_id, capture());
      pushWarning("已有进行中的审计，已继续该审计");
      return;
    }
    if (isControlReceipt(receipt)) {
      set((current) => ({
        sessionView: current.sessionView
          ? {
              ...current.sessionView,
              session_version: Math.max(
                current.sessionView.session_version,
                receipt.session_version
              ),
            }
          : current.sessionView,
      }));
      pushWarning("结束/取消审计请求已提交");
      return;
    }
    if (isRunReceipt(receipt)) {
      const runState: RunState = {
        runId: receipt.run_id,
        sessionId: receipt.session_id,
        status: receipt.status,
        action: receipt.action,
        lastEventSeq: get().lastEventSeq[receipt.run_id] ?? 0,
        errorCode: null,
        errorMessage: null,
      };
      set((current) => ({
        runs: { ...current.runs, [receipt.run_id]: runState },
        activeRunId: receipt.run_id,
        isStreaming: true,
        error: null,
        lastFailedRunId: null,
        sessionView: current.sessionView
          ? {
              ...current.sessionView,
              session_version: Math.max(
                current.sessionView.session_version,
                receipt.session_version
              ),
              current_audit_id: receipt.audit_id ?? current.sessionView.current_audit_id,
              active_run_id: receipt.run_id,
            }
          : current.sessionView,
      }));
      get().subscribeRun(receipt.run_id);
      return;
    }
    // 未知回执形态：兜底刷新会话
    void get().reloadSession();
  }

  async function dispatchCommand(
    action: Action,
    payload: Record<string, unknown>,
    captured: SessionGuard,
    options: SendCommandOptions = {}
  ): Promise<CommandReceipt> {
    const sessionId = captured.sessionId;
    if (!sessionId) throw new Error("会话尚未加载");
    const expected =
      options.expectedSessionVersion ?? get().sessionView?.session_version ?? null;
    try {
      const receipt = await apiSendCommand(sessionId, action, payload as never, {
        expectedSessionVersion: expected,
      });
      if (!assertCurrent(captured)) return receipt;
      applyReceipt(receipt);
      return receipt;
    } catch (error) {
      if (assertCurrent(captured)) {
        setError(errorMessage(error));
        set({ isStreaming: false });
        if (lastSent) set({ lastFailedSend: lastSent });
      }
      throw error;
    }
  }

  // ---- 旧 SSE 处理（仅 legacyMode 使用） --------------------------------- //

  async function autoUpdateCanvas(sessionId: string): Promise<void> {
    if (canvasUpdatePending) return;
    canvasUpdatePending = true;
    try {
      const tree = await api<FeatureTree>("/api/canvas/incremental", {
        method: "POST",
        body: JSON.stringify({ session_id: sessionId }),
      });
      set({ canvasTree: tree });
    } catch {
      // 旧画布增量更新是 best-effort，不打断聊天
    } finally {
      canvasUpdatePending = false;
    }
  }

  function handleSSEEvent(event: SSEEvent): void {
    switch (event.type) {
      case "phase_change":
        if (
          event.phase === "coach" ||
          event.phase === "interview" ||
          event.phase === "brainstorm"
        ) {
          set({ phase: event.phase });
        }
        break;
      case "role_start":
        set({ streamingRole: event.role || null });
        break;
      case "token":
        if (event.token) {
          const role = event.role || "assistant";
          roleBuffers[role] = (roleBuffers[role] || "") + event.token;
          set({ streamingContent: joinedStreamingContent() });
        }
        break;
      case "role_done": {
        const role = event.role || "assistant";
        const content =
          typeof event.content === "string"
            ? event.content
            : roleBuffers[role] ?? get().streamingContent;
        delete roleBuffers[role];
        const state = get();
        if (content) {
          set({
            messages: [
              ...state.messages,
              {
                role: "assistant",
                content,
                role_name: event.role_name || event.role,
                timestamp: new Date().toISOString(),
              },
            ],
            streamingContent: joinedStreamingContent(),
            streamingRole: Object.keys(roleBuffers)[0] ?? null,
          });
        } else {
          set({
            streamingContent: joinedStreamingContent(),
            streamingRole: Object.keys(roleBuffers)[0] ?? null,
          });
        }
        if (state.sessionId) void autoUpdateCanvas(state.sessionId);
        break;
      }
      case "role_error": {
        const role = event.role || "assistant";
        const partial = roleBuffers[role];
        delete roleBuffers[role];
        const state = get();
        if (partial) {
          set({
            messages: [
              ...state.messages,
              {
                role: "assistant",
                content: partial + INTERRUPT_MARK,
                role_name: event.role_name || event.role,
                timestamp: new Date().toISOString(),
              },
            ],
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
          set({ coveredDimensions: event.covered.map((item) => String(item)) });
        }
        break;
      case "error": {
        const errRole = event.role || "assistant";
        const partial =
          roleBuffers[errRole] ||
          (Object.keys(roleBuffers).length === 1 ? Object.values(roleBuffers)[0] : "");
        resetStreamingBuffers();
        const state = get();
        if (partial) {
          set({
            messages: [
              ...state.messages,
              {
                role: "assistant",
                content: partial + INTERRUPT_MARK,
                role_name: errRole,
                timestamp: new Date().toISOString(),
              },
            ],
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
      default:
        break;
    }
  }

  function handleDone(): void {
    const state = get();
    const pending = Object.entries(roleBuffers).filter(([, content]) => content);
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
    if (state.sessionId) void autoUpdateCanvas(state.sessionId);
  }

  function handleError(err: string): void {
    setError(err);
    set({ isStreaming: false });
    resetStreamingBuffers();
    if (lastSent) set({ lastFailedSend: lastSent });
  }

  function startLegacyStream(
    endpoint: string,
    body: Record<string, unknown>,
    optimisticMessage?: string
  ): void {
    const state = get();
    if (!state.sessionId) return;
    if (abortController) abortController.abort();
    resetStreamingBuffers();
    set({
      messages: optimisticMessage
        ? [
            ...state.messages,
            { role: "user", content: optimisticMessage, timestamp: new Date().toISOString() },
          ]
        : state.messages,
      isStreaming: true,
      streamingContent: "",
      error: null,
    });
    abortController = createSSEConnection(endpoint, body, handleSSEEvent, handleDone, handleError);
  }

  // ---- 会话加载 ---------------------------------------------------------- //

  async function hydrateV2(sessionId: string, captured: SessionGuard): Promise<void> {
    const view = await getSession(sessionId);
    if (!assertCurrent(captured)) return;
    set({
      sessionView: view,
      phase: mapV2Phase(view.phase),
      legacyMode: false,
      sessionLoading: false,
      errors: { ...get().errors, [sessionId]: null },
    });
    await hydrateMessages(sessionId, captured);
    if (!assertCurrent(captured)) return;
    await hydrateProductAndCanvas(sessionId, captured, {
      productVersion: view.current_product_version,
      canvasVersion: view.current_product_version,
    });
    if (!assertCurrent(captured)) return;
    if (view.current_audit_id) void hydrateAudit(view.current_audit_id, captured);
    if (view.active_run_id) get().subscribeRun(view.active_run_id);
  }

  async function hydrateLegacy(sessionId: string, captured: SessionGuard): Promise<void> {
    const session = await api<LegacySessionPayload>(
      `/api/session/${encodeURIComponent(sessionId)}`
    );
    if (!assertCurrent(captured)) return;
    set({
      legacyMode: true,
      sessionView: null,
      productState: null,
      productVersion: 0,
      canvas: null,
      canvasLoading: false,
      sessionLoading: false,
      sessionId: session.id,
      phase: normalizePhase(session.phase),
      messages: session.messages ?? [],
      canvasTree: session.canvas_tree ?? null,
      coveredDimensions: Array.isArray(session.interview_dimensions_covered)
        ? session.interview_dimensions_covered.map((item) => String(item))
        : [],
    });
  }

  return {
    // ---- 初始状态 ----
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

    currentSessionId: null,
    generation: 0,
    sessionLoading: false,
    sessionView: null,
    legacyMode: false,
    messagesBySession: {},
    runs: {},
    runSnapshots: {},
    audits: {},
    productStates: {},
    productState: null,
    productVersion: 0,
    canvas: null,
    canvasLoading: false,
    canvases: {},
    errors: {},
    stateWarnings: [],
    lastEventSeq: {},
    subscribingRunId: null,
    activeRunId: null,
    lastFailedRunId: null,
    streamingMessageId: null,

    // ---- 旧 action ----
    loadSession: async (id: string) => {
      get().unsubscribe();
      get().abortStream();
      resetStreamingBuffers();
      const generation = get().generation + 1;
      const captured: SessionGuard = { sessionId: id, generation };
      set({
        currentSessionId: id,
        sessionId: id,
        generation,
        sessionLoading: true,
        error: null,
        lastFailedSend: null,
        lastFailedRunId: null,
        targetRole: "all",
        streamingMessageId: null,
        isStreaming: false,
        streamingContent: "",
        streamingRole: null,
        subscribingRunId: null,
        activeRunId: null,
        legacyMode: false,
      });

      try {
        await hydrateV2(id, captured);
      } catch (error) {
        if (!assertCurrent(captured)) return;
        if (isEndpointUnavailable(error)) {
          try {
            await hydrateLegacy(id, captured);
          } catch (legacyError) {
            if (assertCurrent(captured)) {
              setError(errorMessage(legacyError));
              set({ sessionLoading: false });
            }
          }
        } else {
          setError(errorMessage(error));
          set({ sessionLoading: false });
        }
      }
    },

    sendMessage: (content: string, targetRole: Role | "all") => {
      const state = get();
      if (!state.sessionId) return;
      lastSent = { content, targetRole };

      if (state.legacyMode) {
        startLegacyStream(
          "/api/brainstorm/message",
          { session_id: state.sessionId, content, target_role: targetRole },
          content
        );
        return;
      }

      const sessionId = state.currentSessionId;
      if (!sessionId) return;
      appendOptimisticUserMessage(sessionId, content);
      set({
        isStreaming: true,
        error: null,
        streamingContent: "",
        streamingRole: null,
        streamingMessageId: null,
      });
      const captured = capture();
      const action: Action =
        state.phase === "coach" || state.phase === "define" ? "clarify" : "brainstorm";
      const payload: Record<string, unknown> =
        action === "clarify"
          ? { content }
          : { content, target: mapTargetRole(targetRole) };
      void dispatchCommand(action, payload, captured).catch(() => undefined);
    },

    sendToCoach: (content: string) => {
      const state = get();
      if (!state.sessionId) return;
      if (state.legacyMode) {
        startLegacyStream(
          "/api/brainstorm/coach",
          { session_id: state.sessionId, content },
          content
        );
        return;
      }
      const sessionId = state.currentSessionId;
      if (!sessionId) return;
      appendOptimisticUserMessage(sessionId, content);
      set({ isStreaming: true, error: null, streamingContent: "", streamingRole: null });
      void dispatchCommand("clarify", { content }, capture()).catch(() => undefined);
    },

    skipCoach: () => {
      const state = get();
      if (!state.sessionId) return;
      if (state.legacyMode) {
        get().sendMessage("（跳过引导，直接开始脑暴）", "all");
        return;
      }
      set({ isStreaming: true, error: null });
      void dispatchCommand("skip_clarification", {}, capture()).catch(() => undefined);
    },

    startInterview: () => {
      const state = get();
      if (!state.sessionId) return;
      if (state.legacyMode) {
        startLegacyStream("/api/interview/start", { session_id: state.sessionId });
        return;
      }
      const productVersion =
        state.productVersion || state.sessionView?.current_product_version || 1;
      set({ isStreaming: true, error: null });
      // 后端 StartAuditPayload 是 StrictModel(extra=forbid)：字段名必须是
      // expected_product_version（早期写 product_version 会被 422 拒绝）
      void dispatchCommand(
        "start_audit",
        { expected_product_version: productVersion },
        capture()
      ).catch(() => undefined);
    },

    answerInterview: (answer: string) => {
      const state = get();
      if (!state.sessionId) return;
      if (state.legacyMode) {
        startLegacyStream(
          "/api/interview/respond",
          { session_id: state.sessionId, answer },
          answer
        );
        return;
      }
      const captured = capture();
      void (async () => {
        const sessionId = captured.sessionId;
        if (!sessionId) return;
        const auditId = get().sessionView?.current_audit_id ?? null;
        let audit: AuditSnapshot | null = auditId ? get().audits[auditId] ?? null : null;
        try {
          if (!audit && auditId) audit = await getAudit(auditId);
        } catch (error) {
          if (assertCurrent(captured)) setError(errorMessage(error));
          return;
        }
        if (!assertCurrent(captured)) return;
        const activeQuestionId = audit?.active_question_id ?? null;
        if (!audit || !activeQuestionId) {
          setError("当前没有待回答的审计问题");
          return;
        }
        appendOptimisticUserMessage(sessionId, answer);
        set({ isStreaming: true, error: null });
        await dispatchCommand(
          "answer_audit",
          { audit_id: audit.id, question_id: activeQuestionId, answer },
          captured
        ).catch(() => undefined);
      })();
    },

    generateCanvas: async () => {
      const state = get();
      if (!state.sessionId) return;
      if (state.legacyMode) {
        if (state.isGeneratingCanvas) return;
        set({ isGeneratingCanvas: true });
        try {
          const tree = await api<FeatureTree>("/api/canvas/generate", {
            method: "POST",
            body: JSON.stringify({ session_id: state.sessionId }),
          });
          set({ canvasTree: tree });
          toast.success("画布已更新");
        } catch (error) {
          toast.error(errorMessage(error) || "生成画布失败，请稍后重试");
        } finally {
          set({ isGeneratingCanvas: false });
        }
        return;
      }
      // v2：画布由 ProductState 确定性派生，这里只重取投影（不触发模型调用）
      await get().refreshCanvas();
      toast.success("画布已刷新");
    },

    setPhase: (phase) => set({ phase }),
    clearError: () => setError(null),
    clearLastFailedSend: () => set({ lastFailedSend: null }),

    abortStream: () => {
      if (abortController) {
        abortController.abort();
        abortController = null;
      }
      resetStreamingBuffers();
      set({
        isStreaming: false,
        streamingContent: "",
        streamingRole: null,
        streamingMessageId: null,
      });
    },

    fetchHistory: async () => {
      set({ historyLoading: true, historyError: null });
      try {
        const sessions = await api<SessionSummary[]>("/api/session");
        set({ historySessions: sessions, historyLoading: false });
      } catch (error) {
        set({
          historyError: errorMessage(error) || "加载历史会话失败",
          historyLoading: false,
        });
      }
    },

    toggleHistory: () => set((state) => ({ isHistoryOpen: !state.isHistoryOpen })),

    setInterviewMode: (mode) => set({ interviewMode: mode }),
    setPlayingAudio: (playing) => set({ isPlayingAudio: playing }),
    setTargetRole: (role) => set({ targetRole: role }),
    setCoachTriggered: (sessionId) => set({ coachTriggeredFor: sessionId }),

    // ---- v2 action ----
    reloadSession: async () => {
      const sessionId = get().currentSessionId;
      if (!sessionId) return;
      await get().loadSession(sessionId);
    },

    sendCommand: async (action, payload, options = {}) => {
      const captured = capture();
      if (!captured.sessionId) throw new Error("会话尚未加载");
      set({ isStreaming: true, error: null });
      const receipt = await dispatchCommand(action, payload ?? {}, captured, options);
      return receipt;
    },

    subscribeRun: (runId: string) => {
      if (!runId) return;
      const state = get();
      if (state.subscribingRunId === runId) return;
      // 切换订阅：静默取消旧订阅，不调用 cancel run API（服务器任务继续跑）
      if (runStreamCancel) {
        runStreamCancel();
        runStreamCancel = null;
      }
      openRunStream(runId);
    },

    unsubscribe: () => {
      if (runStreamCancel) {
        runStreamCancel();
        runStreamCancel = null;
      }
      set({ subscribingRunId: null });
    },

    cancelRun: async (runId?: string) => {
      const state = get();
      const targetId = runId ?? state.activeRunId ?? state.sessionView?.active_run_id ?? null;
      if (!targetId) return;
      try {
        const run = await apiCancelRun(targetId);
        if (get().currentSessionId !== run.session_id) return;
        updateRunState(run.id, {
          status: run.status,
          action: run.action,
          errorCode: run.error_code,
          errorMessage: run.error_message,
        });
      } catch (error) {
        toast.error(errorMessage(error));
      }
    },

    retryFailedRun: async (runId?: string) => {
      const state = get();
      const targetId = runId ?? state.lastFailedRunId ?? state.activeRunId ?? null;
      if (!targetId) return;
      try {
        const receipt = await apiRetryFailed(targetId, {
          expectedSessionVersion: state.sessionView?.session_version ?? null,
        });
        if (get().currentSessionId !== receipt.session_id) return;
        applyReceipt(receipt);
      } catch (error) {
        setError(errorMessage(error));
        toast.error(errorMessage(error));
      }
    },

    refreshCanvas: async (productVersion?: number) => {
      await refreshCanvas(productVersion);
    },

    hydrateAuditById: async (auditId: string) => {
      await hydrateAudit(auditId, capture());
    },

    clearStateWarnings: () => set({ stateWarnings: [] }),

    reset: () => {
      get().unsubscribe();
      get().abortStream();
      resetStreamingBuffers();
      seenEvents.clear();
      lastSent = null;
      abortController = null;
      set((state) => ({
        generation: state.generation + 1,
        sessionId: null,
        currentSessionId: null,
        phase: "define",
        messages: [],
        messagesBySession: {},
        canvasTree: null,
        canvas: null,
        canvasLoading: false,
        canvases: {},
        productState: null,
        productVersion: 0,
        productStates: {},
        isStreaming: false,
        streamingRole: null,
        streamingContent: "",
        streamingMessageId: null,
        error: null,
        errors: {},
        lastFailedSend: null,
        coachTriggeredFor: null,
        isGeneratingCanvas: false,
        sessionView: null,
        sessionLoading: false,
        legacyMode: false,
        runs: {},
        runSnapshots: {},
        audits: {},
        lastEventSeq: {},
        subscribingRunId: null,
        activeRunId: null,
        lastFailedRunId: null,
        stateWarnings: [],
        coveredDimensions: [],
        targetRole: "all",
        interviewMode: "text",
        isPlayingAudio: false,
        isHistoryOpen: false,
        historySessions: [],
        historyLoading: false,
        historyError: null,
      }));
    },
  };
});
