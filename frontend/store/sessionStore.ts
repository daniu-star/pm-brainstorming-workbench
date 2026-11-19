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
import { messageActionForPhase } from "@/lib/phaseRouter";
import {
  api,
  cancelRun as apiCancelRun,
  errorMessage,
  getAudit,
  getCanvas,
  getLoop,
  getMessage as apiGetMessage,
  getProductState,
  getRun,
  getRunSnapshot,
  getSession,
  isApiError,
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
  LoopDecisionKind,
  LoopDecisionPayload,
  LoopSnapshot,
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
  StartLoopPayload,
  TargetRole,
} from "@/lib/contracts.generated";
import { isTerminalRunStatus } from "@/lib/contracts.generated";
import {
  mergeLoopUpdate,
  normalizeDecisionNote,
  normalizeLoopSnapshot,
  validateStartForm,
} from "@/lib/loopView";
import {
  acceptLoopVersion,
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

/** 审计快照读取失败记录：notFound 用于区分「确认 404」与「网络/服务端错误」 */
export interface AuditLoadError {
  message: string;
  notFound: boolean;
}

/**
 * 消息分页游标（PRD:307）。
 * `beforeSeq` 来自服务端 `next_before_seq`，为 null 表示没有更早的消息；
 * `earliestSeq` 是本地已加载消息里的最小 seq，`earliestSeq - 1` 即「还有 N 条」。
 */
export interface MessagePageState {
  hasEarlier: boolean;
  beforeSeq: number | null;
  earliestSeq: number | null;
}
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
  /**
   * 首次发送时的幂等 request_id：ChatPanel 点「重试」必须复用同一个值，
   * 否则响应丢失后重试会让后端再执行一次命令（FE-03）。
   */
  requestId?: string;
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
  /** 幂等 request_id（重试时复用首次的值） */
  requestId?: string;
  /** 本次命令对应的本地乐观消息 id：成功后绑定 Run，失败时精确回滚 */
  localId?: string;
}

/** 闭环快照读取失败记录：notFound 用于区分「确认 404」与「网络/服务端错误」 */
export interface LoopLoadError {
  message: string;
  notFound: boolean;
}

/** `startLoop` 的入参（页面表单 → store，store 负责补 expected_product_version） */
export interface StartLoopInput {
  scope?: "mvp" | "optimize";
  maxRounds?: number;
  targetScore?: number;
  focus?: string;
}

/** `decideLoop` 的入参；expectedLoopVersion 必须来自当前快照，防并发覆盖 */
export interface DecideLoopInput {
  loopId: string;
  expectedLoopVersion: number;
  decision: LoopDecisionKind;
  note?: string;
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
  /** v2 阶段原值：旧 phase 会把 auditing 与 audit_completed 混成 interview */
  v2Phase: V2Phase | null;
  /** true = v2 端点不可用，已回退旧路由 */
  legacyMode: boolean;
  messagesBySession: Record<string, MessageView[]>;
  /** 首屏消息分页游标：长会话刷新后仍可「加载更早」 */
  messagePages: Record<string, MessagePageState>;
  earlierLoading: Record<string, boolean>;
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
  /** 审计快照读取失败（按 auditId 记录；404 与其它错误分开，避免把读取失败渲染成「开始审计」） */
  auditErrors: Record<string, AuditLoadError | null>;
  stateWarnings: string[];
  lastEventSeq: Record<string, number>;
  subscribingRunId: string | null;
  activeRunId: string | null;
  lastFailedRunId: string | null;
  streamingMessageId: string | null;
  /** 闭环快照缓存（按 loop_id 键，切回旧闭环可复用） */
  loops: Record<string, LoopSnapshot>;
  /** 当前正在看的闭环；来自 sessionView.current_loop_id 或 start_loop 回执 */
  loopId: string | null;
  loopLoading: boolean;
  loopErrors: Record<string, LoopLoadError | null>;

  // ---- 旧 action ----
  loadSession: (id: string) => Promise<void>;
  sendMessage: (
    content: string,
    targetRole: Role | "all",
    options?: { requestId?: string }
  ) => void;
  sendToCoach: (content: string) => void;
  skipCoach: () => void;
  startInterview: () => void;
  /** 提交审计回答；返回 false 表示未提交（调用方应保留输入） */
  answerInterview: (answer: string) => Promise<boolean>;
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
  /** 按 before_seq 加载上一页消息；无更早消息或正在加载时直接返回 */
  loadEarlierMessages: () => Promise<void>;
  subscribeRun: (runId: string) => void;
  unsubscribe: () => void;
  /**
   * 恢复当前会话运行中 Run 的事件订阅（UX-D1）。
   * 工作台卸载会 abortStream + unsubscribe，客户端路由跳转到审计页时
   * 不会重新挂载工作台，需要由目标页显式调用本方法续订。
   */
  resumeRunSubscription: () => void;
  cancelRun: (runId?: string) => Promise<boolean>;
  retryFailedRun: (runId?: string) => Promise<boolean>;
  /**
   * 重取画布投影。返回 true 表示确实应用了新投影；失败/丢弃旧版本返回 false，
   * 便于调用方只在成功时提示「已刷新」（PROD-06）。
   */
  refreshCanvas: (productVersion?: number) => Promise<boolean>;
  /** 拉取审计快照并写入 `audits`；返回 false 表示读取失败（错误同时记入 `auditErrors`） */
  hydrateAuditById: (auditId: string) => Promise<boolean>;
  /** 写入一份已归一化的闭环快照（例如 run snapshot 里自带的 loop） */
  hydrateLoop: (snapshot: LoopSnapshot) => void;
  /** 拉取闭环快照并写入 `loops`；返回 false 表示读取失败（错误记入 `loopErrors`） */
  hydrateLoopById: (loopId: string) => Promise<boolean>;
  /** 启动闭环（start_loop）；expected_product_version 取当前产品版本 */
  startLoop: (input?: StartLoopInput) => Promise<CommandReceipt>;
  /** 提交闭环决策（loop_decision）；返回的 RunReceipt 会被正常订阅 */
  decideLoop: (input: DecideLoopInput) => Promise<CommandReceipt>;
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
  /** 每个 run 已重连次数：EOF 重连时递传，终态 / 放弃 / 显式退订时清理 */
  const reconnectAttempts = new Map<string, number>();
  /**
   * 本地乐观用户消息（`local-` 前缀）→ 所属会话与内容。
   * 只有服务端回执里出现同内容的 human 消息时才删除对应那一条，
   * 避免 `role.output.updated` 等流式事件把用户输入整条抹掉。
   */
  const pendingOptimistic = new Map<
    string,
    {
      sessionId: string;
      content: string;
      /** 定向角色：失败后写入 lastFailedSend，重试时不能变成群发（DX-05） */
      targetRole: Role | "all";
      /** 首次发送的幂等键，重试必须复用（FE-03） */
      requestId: string;
      /** 回执里的 run_id；null = 请求仍在途或未拿到回执 */
      runId: string | null;
    }
  >();

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

  /**
   * 只清掉某一类已恢复的告警（FE-05）：stateWarnings 只会 push，没有生命周期，
   * 画布重试成功后仍会挂旧错误，用户以为还没恢复。
   */
  function clearWarningsMatching(match: (warning: string) => boolean): void {
    set((state) => {
      const next = state.stateWarnings.filter((warning) => !match(warning));
      if (next.length === state.stateWarnings.length) return {};
      return { stateWarnings: next };
    });
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
    // 只清理「已被服务端回执确认」的本地乐观消息：内容与服务端 human 消息一致，
    // 且同一内容按条数一一对应；其余 local- 消息（发送未确认 / 流式事件先到）保留。
    const cleaned = dropConfirmedLocalMessages(existing, incoming);
    const merged = mergeMessagesById(cleaned, incoming);
    set((current) => ({
      messagesBySession: { ...current.messagesBySession, [sessionId]: merged.items },
    }));
    if (get().currentSessionId === sessionId) syncLegacyMessages(sessionId);
  }

  /** 服务端回执里出现同内容 human 消息时，删除对应的本地乐观消息（按内容计数一一匹配） */
  function dropConfirmedLocalMessages(
    existing: readonly MessageView[],
    incoming: readonly MessageView[]
  ): MessageView[] {
    const hasLocal = existing.some((item) => item.id.startsWith(LOCAL_MESSAGE_PREFIX));
    if (!hasLocal) return existing.slice();

    const confirmed = new Map<string, number>();
    for (const view of incoming) {
      if (view.sender_kind !== "human" || view.id.startsWith(LOCAL_MESSAGE_PREFIX)) continue;
      confirmed.set(view.content, (confirmed.get(view.content) ?? 0) + 1);
    }

    const kept: MessageView[] = [];
    for (const view of existing) {
      if (!view.id.startsWith(LOCAL_MESSAGE_PREFIX)) {
        kept.push(view);
        continue;
      }
      const count = confirmed.get(view.content) ?? 0;
      if (count <= 0) {
        kept.push(view);
        continue;
      }
      confirmed.set(view.content, count - 1);
      pendingOptimistic.delete(view.id);
    }
    return kept;
  }

  /**
   * Run 收尾兜底：绑定到该 Run、直到 Run 结束仍未收到服务端回执的本地乐观消息
   * 视为「发送未确认」。删除占位（避免刷新后重复出现）并写入 lastFailedSend。
   *
   * 只处理已绑定到本 Run 的条目：发送失败的条目在 dispatchCommand 的 catch 里
   * 已经精确回滚，未绑定 Run 的条目说明请求仍在途，不能被其它 Run 的收尾误删。
   */
  function settlePendingOptimistic(sessionId: string, runId: string): void {
    const stale = [...pendingOptimistic.entries()].filter(
      ([, item]) => item.sessionId === sessionId && item.runId === runId
    );
    if (stale.length === 0) return;
    const staleIds = new Set(stale.map(([id]) => id));
    for (const [id] of stale) pendingOptimistic.delete(id);
    set((state) => {
      const views = state.messagesBySession[sessionId] ?? [];
      return {
        messagesBySession: {
          ...state.messagesBySession,
          [sessionId]: views.filter((view) => !staleIds.has(view.id)),
        },
      };
    });
    if (get().currentSessionId === sessionId) syncLegacyMessages(sessionId);
    const last = stale[stale.length - 1][1];
    set({
      lastFailedSend: {
        content: last.content,
        targetRole: last.targetRole,
        requestId: last.requestId,
      },
    });
    setError("消息发送未确认，请重试");
  }

  /** 发送失败：精确删除这条本地乐观消息（不能等 Run 收尾按内容对账，会误删同内容的重试）。 */
  function rollbackOptimisticMessage(sessionId: string, localId: string): void {
    pendingOptimistic.delete(localId);
    set((state) => ({
      messagesBySession: {
        ...state.messagesBySession,
        [sessionId]: (state.messagesBySession[sessionId] ?? []).filter(
          (view) => view.id !== localId
        ),
      },
    }));
    if (get().currentSessionId === sessionId) syncLegacyMessages(sessionId);
  }

  /** 收到 Run 回执：把本地乐观消息绑定到该 Run，供收尾时判断「是否真的没送到」。 */
  function bindOptimisticRun(localId: string, runId: string): void {
    const entry = pendingOptimistic.get(localId);
    if (entry) pendingOptimistic.set(localId, { ...entry, runId });
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

  /** 已加载消息里的最小 seq（忽略缺失 seq 的本地消息） */
  function minMessageSeq(views: readonly MessageView[]): number | null {
    let earliest: number | null = null;
    for (const view of views) {
      if (typeof view.seq !== "number" || !Number.isFinite(view.seq)) continue;
      if (earliest === null || view.seq < earliest) earliest = view.seq;
    }
    return earliest;
  }

  function findMessage(sessionId: string, messageId: string): MessageView | undefined {
    return (get().messagesBySession[sessionId] ?? []).find((view) => view.id === messageId);
  }

  /** 追加本地乐观用户消息，返回 localId 与本次幂等 requestId（服务端回执确认前一直保留） */
  function appendOptimisticUserMessage(
    sessionId: string,
    content: string,
    targetRole: Role | "all" = "all",
    requestId: string = newRequestId()
  ): { localId: string; requestId: string } {
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
    pendingOptimistic.set(localId, {
      sessionId,
      content,
      targetRole,
      requestId,
      runId: null,
    });
    set({ streamingContent: "", error: null });
    return { localId, requestId };
  }

  // ---- 产品状态 / 画布 ---------------------------------------------------- //

  async function refreshCanvas(productVersion?: number): Promise<boolean> {
    const state = get();
    const sessionId = state.currentSessionId;
    if (!sessionId) return false;
    const captured: SessionGuard = { sessionId, generation: state.generation };
    // 版本 0 不是合法产品版本（服务端从 1 起），当成「未知」处理：否则会拼出
    // product_version=0 触发后端 400，画布重试永久失败（QA 第 5 轮 P1）。
    const pickVersion = (...candidates: Array<number | null | undefined>): number | undefined => {
      for (const candidate of candidates) {
        if (typeof candidate === "number" && Number.isFinite(candidate) && candidate > 0) {
          return candidate;
        }
      }
      return undefined;
    };
    const version = pickVersion(
      productVersion,
      state.productVersion,
      state.sessionView?.current_product_version,
    );

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
      if (!assertCurrent(captured)) {
        // 过期请求：同会话（generation 变化）时复位加载态；跨会话交给新会话自己的加载流程
        if (get().currentSessionId === captured.sessionId) {
          set({ canvasLoading: false, isGeneratingCanvas: false });
        }
        return false;
      }
      const previousVersion = get().canvas?.productVersion ?? 0;
      if (projection.product_version < previousVersion) {
        pushWarning(`丢弃旧画布版本 ${projection.product_version}（当前 ${previousVersion}）`);
        // 丢弃旧投影后必须退出 generating：否则 CanvasPanel 徽标永久停在「生成中」，重试入口也被隐藏（DX-03）
        set((current) => ({
          canvas: {
            projection: current.canvas?.projection ?? null,
            productVersion: current.canvas?.productVersion ?? null,
            status: current.canvas?.projection ? "stale" : "error",
            error: "画布版本落后于当前投影，已丢弃本次结果",
          },
          canvasLoading: false,
          isGeneratingCanvas: false,
        }));
        return false;
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
      // 刷新成功：清掉与画布相关的旧告警，琥珀条不应继续挂旧错误（FE-05）
      clearWarningsMatching(
        (warning) =>
          warning.startsWith("丢弃旧画布版本") ||
          warning.startsWith("画布加载失败") ||
          warning.startsWith("忽略非法 product_version"),
      );
      return true;
    } catch (error) {
      if (!assertCurrent(captured)) {
        // 过期请求：同会话时复位加载态，避免 loading 卡死
        if (get().currentSessionId === captured.sessionId) {
          set({ canvasLoading: false, isGeneratingCanvas: false });
        }
        return false;
      }
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
      return false;
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
      // 版本门限用「本会话基线」：切会话后全局 productVersion 已被重置，
      // 不能拿上一个会话的版本号把新会话的合法快照当旧版本丢弃。
      const baselineVersion = get().productStates[sessionId]?.version ?? 0;
      const acceptance = acceptProductVersion(baselineVersion, productState.version);
      if (acceptance.accepted) {
        set((state) => ({
          productState,
          productVersion: acceptance.version,
          productStates: { ...state.productStates, [sessionId]: productState as ProductState },
        }));
        canvasVersion = acceptance.version;
        clearWarningsMatching((warning) => warning.startsWith("丢弃旧版本 product_version"));
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
      clearWarningsMatching((warning) => warning.startsWith("丢弃旧版本 product_version"));
    } catch {
      // 产品状态刷新失败：保留版本号，画布徽标会提示重试
    }
  }

  async function hydrateMessages(sessionId: string, captured: SessionGuard): Promise<void> {
    try {
      const page = await listMessages(sessionId, { limit: 100 });
      if (!assertCurrent(captured)) return;
      mergeMessages(sessionId, page.items ?? []);
      if (!assertCurrent(captured)) return;
      set((state) => {
        const earliest = minMessageSeq(state.messagesBySession[sessionId] ?? []);
        const previous = state.messagePages[sessionId];
        const nextBefore = page.next_before_seq ?? null;
        // 刷新会重新拉最新一页：不能把已加载更早的游标推回最新页
        const beforeSeq =
          nextBefore === null
            ? previous?.beforeSeq ?? null
            : previous?.beforeSeq != null
              ? Math.min(previous.beforeSeq, nextBefore)
              : nextBefore;
        return {
          messagePages: {
            ...state.messagePages,
            [sessionId]: {
              hasEarlier: beforeSeq !== null && earliest !== null && earliest > 1,
              beforeSeq,
              earliestSeq: earliest,
            },
          },
        };
      });
    } catch {
      // 消息补齐失败不阻塞会话加载（首屏仍可交互）
    }
  }

  async function hydrateAudit(auditId: string, captured: SessionGuard): Promise<boolean> {
    if (!auditId) return false;
    try {
      const audit = await getAudit(auditId);
      if (!assertCurrent(captured)) return false;
      set((state) => ({
        audits: { ...state.audits, [audit.id]: audit },
        auditErrors: { ...state.auditErrors, [auditId]: null, [audit.id]: null },
      }));
      return true;
    } catch (error) {
      if (!assertCurrent(captured)) return false;
      set((state) => ({
        auditErrors: {
          ...state.auditErrors,
          [auditId]: {
            message: errorMessage(error),
            notFound: isApiError(error) && error.status === 404,
          },
        },
      }));
      return false;
    }
  }

  async function fetchLoop(loopId: string, captured: SessionGuard): Promise<boolean> {
    if (!loopId) return false;
    try {
      const snapshot = normalizeLoopSnapshot(await getLoop(loopId));
      if (!assertCurrent(captured)) return false;
      if (!snapshot) {
        set((state) => ({
          loopLoading: false,
          loopErrors: {
            ...state.loopErrors,
            [loopId]: { message: "闭环数据解析失败，请稍后重试", notFound: false },
          },
        }));
        return false;
      }
      get().hydrateLoop(snapshot);
      return true;
    } catch (error) {
      if (!assertCurrent(captured)) return false;
      set((state) => ({
        loopLoading: false,
        loopErrors: {
          ...state.loopErrors,
          [loopId]: {
            message: errorMessage(error),
            notFound: isApiError(error) && error.status === 404,
          },
        },
      }));
      return false;
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
      case "loop.updated": {
        // 事件 payload 是局部更新且可能不完整：只有 loop_id 与当前闭环一致才合并，
        // 随后用 GET /loops/{id} 兜底拉全量快照（避免把 artifacts/rounds 弄丢）。
        const loopId = typeof payload.loop_id === "string" ? payload.loop_id : "";
        const currentLoopId = get().loopId ?? get().sessionView?.current_loop_id ?? null;
        if (!loopId || loopId !== currentLoopId) break;
        const existing = get().loops[loopId];
        if (existing) {
          const merged = mergeLoopUpdate(existing, payload);
          set((state) => ({ loops: { ...state.loops, [loopId]: merged } }));
        }
        void get().hydrateLoopById(loopId);
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
    reconnectAttempts.delete(runId);
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
      if (session) set({ sessionView: session, phase: mapV2Phase(session.phase), v2Phase: session.phase });

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

      // 收尾兜底：Run 已结束仍未被服务端回执确认的本地乐观消息 → 提示发送失败
      settlePendingOptimistic(sessionId, runId);

      // 闭环轮次结束后刷新工作室快照：优先用 run snapshot 里自带的 loop（省一次请求），
      // 否则按 run.loop_id / session.current_loop_id 兜底拉取（契约 §6）。
      const loopId = snapshot?.loop_id ?? session?.current_loop_id ?? get().loopId ?? null;
      if (snapshot?.loop) {
        const hydratedLoop = normalizeLoopSnapshot(snapshot.loop);
        if (hydratedLoop) get().hydrateLoop(hydratedLoop);
      } else if (loopId) {
        await get().hydrateLoopById(loopId);
      }
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
    // attempt = 第几次重连（从 1 开始）：指数退避，最长 10s
    reconnectAttempts.set(runId, attempt);
    await sleep(Math.min(600 * 2 ** attempt, 10000));
    if (!assertCurrent(captured)) return;
    try {
      const run = await getRun(runId);
      if (!assertCurrent(captured)) return;
      if (isTerminalRunStatus(run.status)) {
        reconnectAttempts.delete(runId);
        await finalizeRun(runId, captured);
        return;
      }
      if (attempt >= 2) {
        reconnectAttempts.delete(runId);
        setError("事件流已断开，请刷新页面继续");
        set({ isStreaming: false, subscribingRunId: null });
        return;
      }
      set({ subscribingRunId: null });
      get().subscribeRun(runId);
    } catch (error) {
      if (!assertCurrent(captured)) return;
      if (attempt >= 2) {
        reconnectAttempts.delete(runId);
        setError(errorMessage(error));
        set({ isStreaming: false, subscribingRunId: null });
        return;
      }
      // 退避期间 getRun 也失败（网络抖动）：继续下一次重连，计数继续递传
      set({ subscribingRunId: null });
      void reconnectRun(runId, captured, attempt + 1);
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
          } else if (reason === "eof" || reason === "idle") {
            // 断线/空闲超时重连：沿用已记录的重连次数，避免每次都从第 1 次重来导致无限重连
            const nextAttempt = (reconnectAttempts.get(runId) ?? 0) + 1;
            void reconnectRun(runId, captured, nextAttempt);
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
        // 控制回执没有可等待的 Run 时清零 streaming，否则输入框会一直处于禁用态
        isStreaming: Boolean(receipt.related_run_id),
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
      // 结束/取消审计后后端会派生一个收尾 Run（生成报告）：订阅它，
      // 完成后 finalizeRun 会重新拉会话与审计快照（无 related_run_id 时页面轮询兜底）
      if (receipt.related_run_id) get().subscribeRun(receipt.related_run_id);
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
        // 回执到位 = 这条命令已被后端接受，旧的「发送失败」重试条必须失效，
        // 否则用户再点一次重试会重复发送（FE-01）
        lastFailedSend: null,
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
    payload: object,
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
        requestId: options.requestId,
      });
      if (!assertCurrent(captured)) return receipt;
      applyReceipt(receipt);
      // 回执到了：把乐观消息绑定到本次 Run；收尾时只有「已绑定但未确认」才算发送失败
      if (options.localId && isRunReceipt(receipt)) {
        bindOptimisticRun(options.localId, receipt.run_id);
      }
      return receipt;
    } catch (error) {
      if (assertCurrent(captured)) {
        // 发送失败必须立刻精确回滚这条草稿，否则重试成功后会残留一条同内容占位，
        // 被 Run 收尾的按内容对账误判成「消息发送未确认」（FE-01）。
        if (options.localId) rollbackOptimisticMessage(sessionId, options.localId);
        setError(errorMessage(error));
        set({ isStreaming: false });
        // 只有聊天发送失败才挂出「重试」条：普通命令失败不能复用上一条聊天内容重发
        if (options.localId && lastSent) set({ lastFailedSend: lastSent });
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
      v2Phase: view.phase,
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
    if (view.current_loop_id) void get().hydrateLoopById(view.current_loop_id);
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
      v2Phase: null,
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
    v2Phase: null,
    legacyMode: false,
    messagesBySession: {},
    messagePages: {},
    earlierLoading: {},
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
    auditErrors: {},
    stateWarnings: [],
    lastEventSeq: {},
    subscribingRunId: null,
    activeRunId: null,
    lastFailedRunId: null,
    streamingMessageId: null,
    loops: {},
    loopId: null,
    loopLoading: false,
    loopErrors: {},

    // ---- 旧 action ----
    loadSession: async (id: string) => {
      get().unsubscribe();
      get().abortStream();
      resetStreamingBuffers();
      pendingOptimistic.clear();
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
        v2Phase: null,
        // 会话级重置：sessionView / 画布 / 产品状态属于上一个会话，必须清空
        // （canvases / productStates 是按 sessionId 键的缓存，保留以便切回时复用）
        sessionView: null,
        // messages 是「当前会话」的旧组件投影（syncLegacyMessages 写入）：必须一起清空，
        // 否则切会话时会先渲染上一个会话的消息（UX-D4）
        messages: [],
        stateWarnings: [],
        canvas: null,
        canvasLoading: false,
        isGeneratingCanvas: false,
        productVersion: 0,
        productState: null,
        // 闭环是会话级视图：切会话时清空当前闭环指针与加载态（loops 缓存按 loop_id 保留）
        loopId: null,
        loopLoading: false,
        loopErrors: {},
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

    sendMessage: (content: string, targetRole: Role | "all", options?: { requestId?: string }) => {
      const state = get();
      if (!state.sessionId) return;
      // 重试复用首次的 request_id：响应丢失后重试不会让后端重复执行命令（FE-03）
      const retryRequestId =
        state.lastFailedSend &&
        state.lastFailedSend.content === content &&
        state.lastFailedSend.targetRole === targetRole
          ? state.lastFailedSend.requestId
          : undefined;
      const requestId = options?.requestId ?? retryRequestId ?? newRequestId();
      lastSent = { content, targetRole, requestId };

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
      const optimistic = appendOptimisticUserMessage(sessionId, content, targetRole, requestId);
      set({
        isStreaming: true,
        error: null,
        streamingContent: "",
        streamingRole: null,
        streamingMessageId: null,
      });
      const captured = capture();
      // 审计结束后旧 phase 仍是 interview，必须按 v2 阶段原值路由：
      // 只有 auditing 才发 answer_audit，其余（含 audit_completed）发 brainstorm
      const action: Action = state.v2Phase
        ? messageActionForPhase(state.v2Phase)
        : state.phase === "coach" || state.phase === "define"
          ? "clarify"
          : "brainstorm";
      const payload: Record<string, unknown> =
        action === "clarify"
          ? { content }
          : { content, target: mapTargetRole(targetRole) };
      void dispatchCommand(action, payload, captured, {
        requestId: optimistic.requestId,
        localId: optimistic.localId,
      }).catch(() => undefined);
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
      // 兼容旧链接（?problem=）：会话创建时后端已写入同内容的 clarify 消息并启动首个 Run
      // （backend/api/v2/common.py:397-429），这里再发一次会撞 SESSION_BUSY。
      const duplicated = (state.messagesBySession[sessionId] ?? []).some(
        (view) =>
          view.sender_kind === "human" &&
          view.stage === "clarify" &&
          !view.id.startsWith(LOCAL_MESSAGE_PREFIX) &&
          view.content === content
      );
      if (duplicated) return;
      const optimistic = appendOptimisticUserMessage(sessionId, content);
      set({ isStreaming: true, error: null, streamingContent: "", streamingRole: null });
      void dispatchCommand("clarify", { content }, capture(), {
        requestId: optimistic.requestId,
        localId: optimistic.localId,
      }).catch(() => undefined);
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

    answerInterview: async (answer: string): Promise<boolean> => {
      const state = get();
      if (!state.sessionId) return false;
      if (state.legacyMode) {
        startLegacyStream(
          "/api/interview/respond",
          { session_id: state.sessionId, answer },
          answer
        );
        return true;
      }
      const captured = capture();
      const sessionId = captured.sessionId;
      if (!sessionId) return false;
      const auditId = get().sessionView?.current_audit_id ?? null;
      let audit: AuditSnapshot | null = auditId ? get().audits[auditId] ?? null : null;
      try {
        if (!audit && auditId) audit = await getAudit(auditId);
      } catch (error) {
        if (assertCurrent(captured)) setError(errorMessage(error));
        return false;
      }
      if (!assertCurrent(captured)) return false;
      const activeQuestionId = audit?.active_question_id ?? null;
      if (!audit || !activeQuestionId) {
        setError("当前没有待回答的审计问题");
        return false;
      }
      set({ isStreaming: true, error: null });
      const optimistic = appendOptimisticUserMessage(sessionId, answer);
      try {
        await dispatchCommand(
          "answer_audit",
          { audit_id: audit.id, question_id: activeQuestionId, answer },
          captured,
          { requestId: optimistic.requestId, localId: optimistic.localId }
        );
        return true;
      } catch (error) {
        if (assertCurrent(captured)) setError(errorMessage(error));
        return false;
      }
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
      const refreshed = await get().refreshCanvas();
      // 失败时 refreshCanvas 已 pushWarning（画布加载失败：…），不能再弹成功 toast
      if (refreshed) toast.success("画布已刷新");
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

    loadEarlierMessages: async () => {
      const state = get();
      const sessionId = state.currentSessionId;
      if (!sessionId) return;
      const page = state.messagePages[sessionId];
      if (!page?.hasEarlier || page.beforeSeq === null) return;
      if (state.earlierLoading[sessionId]) return;
      const captured = capture();
      set((current) => ({
        earlierLoading: { ...current.earlierLoading, [sessionId]: true },
      }));
      try {
        const result = await listMessages(sessionId, {
          beforeSeq: page.beforeSeq,
          limit: 100,
        });
        if (!assertCurrent(captured)) return;
        mergeMessages(sessionId, result.items ?? []);
        if (!assertCurrent(captured)) return;
        set((current) => {
          const earliest = minMessageSeq(current.messagesBySession[sessionId] ?? []);
          return {
            messagePages: {
              ...current.messagePages,
              [sessionId]: {
                hasEarlier: result.next_before_seq != null && earliest !== null && earliest > 1,
                beforeSeq: result.next_before_seq ?? null,
                earliestSeq: earliest,
              },
            },
          };
        });
      } catch (error) {
        if (assertCurrent(captured)) setError(errorMessage(error));
      } finally {
        if (get().currentSessionId === sessionId) {
          set((current) => ({
            earlierLoading: { ...current.earlierLoading, [sessionId]: false },
          }));
        }
      }
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
      // 显式退订/切换会话后重新订阅应从第 1 次重连开始计数
      reconnectAttempts.clear();
      set({ subscribingRunId: null });
    },

    resumeRunSubscription: () => {
      const state = get();
      const runId = state.sessionView?.active_run_id ?? state.activeRunId ?? null;
      if (!runId) return;
      if (state.subscribingRunId === runId) return;
      // subscribeRun 内部会取消旧订阅并打开新流；不调用 cancel run API（服务端任务继续跑）
      get().subscribeRun(runId);
    },

    cancelRun: async (runId?: string) => {
      const state = get();
      const targetId = runId ?? state.activeRunId ?? state.sessionView?.active_run_id ?? null;
      if (!targetId) return false;
      try {
        const run = await apiCancelRun(targetId);
        if (get().currentSessionId !== run.session_id) return false;
        updateRunState(run.id, {
          status: run.status,
          action: run.action,
          errorCode: run.error_code,
          errorMessage: run.error_message,
        });
        return true;
      } catch (error) {
        // 调用方（loop/audit 页）必须能区分「已提交」与「失败」：
        // 只 toast 不返回结果会让页面把 409/500 当成功提示（FE-DX-01）。
        setError(errorMessage(error));
        toast.error(errorMessage(error));
        return false;
      }
    },

    retryFailedRun: async (runId?: string) => {
      const state = get();
      const targetId = runId ?? state.lastFailedRunId ?? state.activeRunId ?? null;
      if (!targetId) return false;
      try {
        const receipt = await apiRetryFailed(targetId, {
          expectedSessionVersion: state.sessionView?.session_version ?? null,
        });
        if (get().currentSessionId !== receipt.session_id) return false;
        applyReceipt(receipt);
        return true;
      } catch (error) {
        setError(errorMessage(error));
        toast.error(errorMessage(error));
        return false;
      }
    },

    refreshCanvas: async (productVersion?: number) => {
      return refreshCanvas(productVersion);
    },

    hydrateAuditById: async (auditId: string) => {
      return hydrateAudit(auditId, capture());
    },

    hydrateLoop: (snapshot: LoopSnapshot) => {
      if (!snapshot || !snapshot.loop_id) return;
      set((state) => {
        const sessionId = state.currentSessionId;
        const belongsToCurrent = !sessionId || !snapshot.session_id || snapshot.session_id === sessionId;
        const previous = state.loops[snapshot.loop_id];
        // loop_version 单调（FE-AUDIT-01）：一轮闭环内有多条 loop.updated、finalizeRun、
        // 决策后刷新并发写回，旧响应后到会把 awaiting_decision 改回 running 并锁死决策键。
        const acceptance = acceptLoopVersion(previous?.loop_version ?? 0, snapshot.loop_version);
        if (!acceptance.accepted) {
          return {
            loopLoading: false,
            stateWarnings: acceptance.warning
              ? [...state.stateWarnings, acceptance.warning].slice(-MAX_WARNINGS)
              : state.stateWarnings,
          };
        }
        return {
          loops: { ...state.loops, [snapshot.loop_id]: snapshot },
          loopId: belongsToCurrent ? snapshot.loop_id : state.loopId,
          loopLoading: false,
          loopErrors: { ...state.loopErrors, [snapshot.loop_id]: null },
        };
      });
    },

    hydrateLoopById: async (loopId: string) => {
      if (!loopId) return false;
      const existing = get().loops[loopId];
      set({ loopLoading: !existing });
      return fetchLoop(loopId, capture());
    },

    startLoop: async (input: StartLoopInput = {}) => {
      const captured = capture();
      if (!captured.sessionId) throw new Error("会话尚未加载");
      const state = get();
      const form = validateStartForm({
        scope: input.scope,
        maxRounds: input.maxRounds,
        targetScore: input.targetScore,
        focus: input.focus,
      });
      // 契约 §4：expected_product_version 必须是当前产品版本，不能用 0/undefined，
      // 否则后端会按「乐观锁基线 0」直接 VERSION_CONFLICT。
      const productVersion =
        state.productVersion || state.sessionView?.current_product_version || 1;
      const payload: StartLoopPayload = {
        scope: form.scope,
        max_rounds: form.max_rounds,
        target_score: form.target_score,
        focus: form.focus,
        expected_product_version: productVersion,
      };
      set({ isStreaming: true, error: null });
      const receipt = await dispatchCommand("start_loop", payload, captured);
      if (isRunReceipt(receipt) && receipt.loop_id) {
        // 立刻把 current_loop_id 指到新闭环：否则页面在首轮 Run 结束前会按
        // sessionView.current_loop_id 判定「当前闭环」失败，退化成「历史闭环 · 只读」，
        // 决策/中止入口全部消失（loop-state-sync）。
        set((prev) =>
          prev.sessionView ? { sessionView: { ...prev.sessionView, current_loop_id: receipt.loop_id! } } : {},
        );
        await get().hydrateLoopById(receipt.loop_id);
      }
      return receipt;
    },

    decideLoop: async (input: DecideLoopInput) => {
      const captured = capture();
      if (!captured.sessionId) throw new Error("会话尚未加载");
      const payload: LoopDecisionPayload = {
        loop_id: input.loopId,
        expected_loop_version: Math.max(1, Math.round(input.expectedLoopVersion)),
        decision: input.decision,
        note: normalizeDecisionNote(input.note),
      };
      set({ isStreaming: true, error: null });
      const receipt = await dispatchCommand("loop_decision", payload, captured);
      // ship/stop 的 Run 可能同步结束、iterate 会立刻推进轮次：回执后先刷新一次快照，
      // 之后 Run 收尾时 finalizeRun 还会再刷一次（幂等）。
      await get().hydrateLoopById(input.loopId);
      return receipt;
    },

    clearStateWarnings: () => set({ stateWarnings: [] }),

    reset: () => {
      get().unsubscribe();
      get().abortStream();
      resetStreamingBuffers();
      seenEvents.clear();
      reconnectAttempts.clear();
      pendingOptimistic.clear();
      lastSent = null;
      abortController = null;
      set((state) => ({
        generation: state.generation + 1,
        sessionId: null,
        currentSessionId: null,
        phase: "define",
        messages: [],
        messagesBySession: {},
        messagePages: {},
        earlierLoading: {},
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
        auditErrors: {},
        lastFailedSend: null,
        coachTriggeredFor: null,
        isGeneratingCanvas: false,
        sessionView: null,
        sessionLoading: false,
        legacyMode: false,
        v2Phase: null,
        runs: {},
        runSnapshots: {},
        audits: {},
        loops: {},
        loopId: null,
        loopLoading: false,
        loopErrors: {},
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
