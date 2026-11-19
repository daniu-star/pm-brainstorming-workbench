/**
 * HTTP 客户端（M1）
 *
 * 两套入口并存：
 * - 旧入口 `api()` / `apiUrl()` / `statusMessage()`：迁移期旧路由（`/api/session/*`、
 *   `/api/brainstorm/*`、`/api/canvas/*`、`/api/interview/*`）仍在用，行为保持不变。
 * - v2 入口：`createSession` / `sendCommand` / `getRunSnapshot` ... 对应
 *   `docs/v4-m1-interfaces.md` §5.2 的 `/api/v2` 端点表。写请求统一携带
 *   `Idempotency-Key`（值 = body.request_id，缺省时用 `crypto.randomUUID()` 生成）。
 *
 * 错误统一为 `ApiError(code, message, status, retryable, details, requestId)`；
 * 为兼容旧调用方额外保留 `status` / `payload` 字段（login 页读 `payload.detail`、
 * AuthGate 读 `status`）。
 */

import type {
  Action,
  ApiErrorBody,
  AuditSnapshot,
  CanvasProjection,
  CommandPayloadMap,
  CommandReceipt,
  ControlReceipt,
  DeletedSession,
  Evidence,
  LoopArtifactResponse,
  LoopDecisionPayload,
  LoopListPage,
  LoopRoundView,
  LoopSnapshot,
  MessageListPage,
  MessageView,
  ProductState,
  RunReceipt,
  RunSnapshot,
  RunView,
  SessionListPage,
  SessionView,
  StartLoopPayload,
} from "./contracts.generated";

export const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// --------------------------------------------------------------------------- //
// 错误
// --------------------------------------------------------------------------- //

/** 中文错误码文案（与 backend/domain/errors.py 的 ErrorCode 对齐）。 */
export const CODE_MESSAGES: Record<string, string> = {
  UNAUTHENTICATED: "请先登录",
  FORBIDDEN: "没有权限",
  NOT_FOUND: "资源不存在",
  INVALID_REQUEST: "请求参数有误",
  UNKNOWN_ACTION: "不支持的操作",
  INPUT_TOO_LONG: "输入内容过长，请精简后再试",
  IDEMPOTENCY_KEY_MISMATCH: "请求标识不一致，请重试",
  IDEMPOTENCY_KEY_REUSED: "重复的请求，已忽略",
  VERSION_CONFLICT: "内容已在别处更新，请刷新后重试",
  SESSION_BUSY: "当前会话有任务正在运行，请稍后再试",
  QUEUE_LIMIT_EXCEEDED: "任务排队过多，请稍后再试",
  CANVAS_IS_DERIVED: "画布由产品状态自动生成，不能直接修改",
  BUDGET_EXCEEDED: "本次会话额度已用尽",
  RUN_DEADLINE_EXCEEDED: "任务执行超时",
  QUEUE_WAIT_EXCEEDED: "任务排队超时，请重试",
  RETRY_CONTEXT_CHANGED: "会话内容已变化，无法按原上下文重试",
  RETRY_AUDIT_CONTEXT_CHANGED: "审计内容已变化，无法按原上下文重试",
  CONTEXT_TOO_LARGE: "上下文过长，请精简后再试",
  MODEL_OUTPUT_INVALID: "模型输出异常，请重试",
  LEASE_LOST: "任务执行中断，请重试",
  AUDIT_CLOSED: "审计已结束",
  AUDIT_ACTIVE: "已有进行中的审计",
  LOOP_ACTIVE: "已有进行中的闭环工作室",
  LOOP_NOT_AWAITING_DECISION: "闭环当前不在决策阶段",
  LOOP_ROUNDS_EXHAUSTED: "闭环轮次已用尽，只能交付或停止",
  LOOP_CLAIMED: "该轮闭环已被其他任务领取，请稍后刷新",
  LOOP_VERSION_CONFLICT: "闭环状态已在别处更新，请刷新后重试",
  QUESTION_ALREADY_ANSWERED: "该问题已回答",
  QUESTION_NOT_ACTIVE: "当前问题已不是最新问题",
  CONTROL_SUPERSEDED: "该控制操作已被新的操作取代",
  EVENT_CURSOR_EXPIRED: "事件流已过期，正在重新同步",
  INVALID_EVENT_CURSOR: "事件游标无效，正在重新同步",
  FEATURE_NOT_READY: "该功能尚未开放",
  INTERNAL: "服务内部错误，请稍后重试",
  TIMEOUT: "请求超时，请稍后重试",
};

export class ApiError extends Error {
  /** 后端错误码（ErrorCode），网络错误为 `NETWORK_ERROR` */
  readonly code: string;
  /** HTTP 状态码，网络错误为 0 */
  readonly status: number;
  /** 是否可安全重试（后端 retryable） */
  readonly retryable: boolean;
  /** 后端 details（结构因错误而异） */
  readonly details: unknown;
  /** 后端 request_id，便于排查 |
   */
  readonly requestId: string | null;
  /** 兼容旧代码：原始响应体（如 429 的 { detail: { message, retry_after } }） */
  readonly payload: Record<string, unknown> | null;

  constructor(
    code: string,
    message: string,
    status: number,
    retryable = false,
    details: unknown = null,
    requestId: string | null = null,
    payload: Record<string, unknown> | null = null
  ) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.retryable = retryable;
    this.details = details;
    this.requestId = requestId;
    this.payload = payload;
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError;
}

/** Map HTTP status codes to friendly Chinese messages. */
export function statusMessage(status: number): string {
  if (status === 401) return "请先登录";
  if (status === 403) return "没有权限";
  if (status === 404) return "资源不存在";
  if (status >= 500) return "服务暂不可用，请稍后重试";
  return `请求失败（${status}）`;
}

/** 面向用户的错误文案：优先错误码中文表，其次后端 message，最后按状态码。 */
export function errorMessage(error: unknown): string {
  if (isApiError(error)) {
    if (error.code === "NETWORK_ERROR") return "网络连接失败，请检查网络后重试";
    return CODE_MESSAGES[error.code] ?? (error.message || statusMessage(error.status));
  }
  if (error instanceof Error && error.message) return error.message;
  return "请求失败，请稍后重试";
}

/**
 * v2 端点不存在 / 网关不可达：用于 store 的 legacy 降级判断。
 *
 * 只有「端点确实不存在」才允许切到 legacy：404 / 405 / 501。
 * 410（EVENT_CURSOR_EXPIRED）应走快照恢复、503（FEATURE_NOT_READY 或启动中）
 * 与 502（网关抖动）应作为可重试错误提示用户——否则一次临时故障会让整个
 * 会话永久降级到旧写路径（QA 第 5 轮 P1）。
 */
export function isEndpointUnavailable(error: unknown): boolean {
  if (isApiError(error)) {
    return [404, 405, 501].includes(error.status);
  }
  // fetch 网络层失败（后端未启动 / CORS）
  return error instanceof TypeError;
}

function statusFallbackCode(status: number): string {
  if (status === 401) return "UNAUTHENTICATED";
  if (status === 403) return "FORBIDDEN";
  if (status === 404) return "NOT_FOUND";
  if (status === 409) return "VERSION_CONFLICT";
  if (status === 410) return "EVENT_CURSOR_EXPIRED";
  if (status === 422) return "INVALID_REQUEST";
  if (status === 429) return "QUEUE_LIMIT_EXCEEDED";
  if (status >= 500) return "INTERNAL";
  return `HTTP_${status}`;
}

/** 由响应构造 ApiError：优先 v2 错误体，其次旧 `detail` 形态。 */
export function errorFromResponse(status: number, body: unknown): ApiError {
  const payload = typeof body === "object" && body !== null ? (body as Record<string, unknown>) : null;

  const v2 = payload && typeof payload.error === "object" && payload.error !== null
    ? (payload.error as Partial<ApiErrorBody["error"]>)
    : null;
  if (v2 && typeof v2.code === "string") {
    return new ApiError(
      v2.code,
      typeof v2.message === "string" && v2.message ? v2.message : (CODE_MESSAGES[v2.code] ?? statusMessage(status)),
      status,
      Boolean(v2.retryable),
      v2.details ?? null,
      typeof v2.request_id === "string" ? v2.request_id : null,
      payload
    );
  }

  // 旧错误体：{ detail: string } 或 { detail: { message } }
  let detail = "";
  if (payload) {
    const rawDetail = payload.detail;
    if (typeof rawDetail === "string") detail = rawDetail;
    else if (rawDetail && typeof rawDetail === "object") {
      const message = (rawDetail as Record<string, unknown>).message;
      if (typeof message === "string") detail = message;
    }
    if ("message" in payload && typeof payload.message === "string" && !detail) {
      detail = payload.message;
    }
  }

  const code = statusFallbackCode(status);
  return new ApiError(
    code,
    detail ? detail.slice(0, 200) : statusMessage(status),
    status,
    status === 429 || status >= 500,
    payload,
    null,
    payload
  );
}

// --------------------------------------------------------------------------- //
// 请求 ID / 查询串
// --------------------------------------------------------------------------- //

/** 生成幂等请求 ID（UUID v4）。 */
export function newRequestId(): string {
  const cryptoObj = globalThis.crypto as unknown as { randomUUID?: () => string } | undefined;
  if (cryptoObj && typeof cryptoObj.randomUUID === "function") {
    return cryptoObj.randomUUID();
  }
  // 极旧环境的降级实现（非密码学安全，仅用于幂等键）
  const bytes = new Uint8Array(16);
  const getRandomValues = (globalThis.crypto as unknown as { getRandomValues?: (a: Uint8Array) => Uint8Array } | undefined)
    ?.getRandomValues;
  if (typeof getRandomValues === "function") {
    getRandomValues.call(globalThis.crypto, bytes);
  } else {
    for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256);
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

export type QueryValue = string | number | boolean | null | undefined;

function buildQuery(query?: Record<string, QueryValue>): string {
  if (!query) return "";
  const parts: string[] = [];
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`);
  }
  return parts.length > 0 ? `?${parts.join("&")}` : "";
}

// --------------------------------------------------------------------------- //
// 旧入口（保持原行为）
// --------------------------------------------------------------------------- //

const MAX_DETAIL_LENGTH = 200;

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let text = "";
    try {
      text = await res.text();
    } catch {
      text = "";
    }
    let body: unknown = null;
    if (text) {
      try {
        body = JSON.parse(text);
      } catch {
        body = null;
      }
    }
    throw errorFromResponse(res.status, body);
  }
  return res.json() as Promise<T>;
}

export function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}

// --------------------------------------------------------------------------- //
// v2 客户端
// --------------------------------------------------------------------------- //

export interface V2RequestOptions {
  method?: "GET" | "POST" | "DELETE" | "PATCH";
  body?: unknown;
  query?: Record<string, QueryValue>;
  /** 幂等请求 ID；写请求缺省自动生成 */
  requestId?: string;
  signal?: AbortSignal;
  headers?: Record<string, string>;
}

const WRITE_METHODS = new Set(["POST", "PATCH", "DELETE", "PUT"]);

/** 单次 HTTP 请求的默认超时；避免后端/网关挂起时按钮永久停在「提交中」。 */
export const REQUEST_TIMEOUT_MS = 15_000;

interface RequestGuard {
  signal: AbortSignal;
  didTimeout: () => boolean;
  dispose: () => void;
}

/** 把调用方 signal 与超时合并成一个 controller，并记录超时是否已触发。 */
function guardRequest(callerSignal: AbortSignal | undefined, timeoutMs: number): RequestGuard {
  const controller = new AbortController();
  let timedOut = false;

  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);

  const onCallerAbort = () => controller.abort();
  if (callerSignal) {
    if (callerSignal.aborted) controller.abort();
    else callerSignal.addEventListener("abort", onCallerAbort, { once: true });
  }

  return {
    signal: controller.signal,
    didTimeout: () => timedOut,
    dispose: () => {
      clearTimeout(timer);
      callerSignal?.removeEventListener("abort", onCallerAbort);
    },
  };
}

export async function apiV2<T>(path: string, options: V2RequestOptions = {}): Promise<T> {
  const method = options.method ?? "GET";
  const isWrite = WRITE_METHODS.has(method);
  const requestId = options.requestId ?? (isWrite ? newRequestId() : undefined);

  const headers: Record<string, string> = { Accept: "application/json", ...(options.headers ?? {}) };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (isWrite && requestId) headers["Idempotency-Key"] = requestId;

  const guard = guardRequest(options.signal, REQUEST_TIMEOUT_MS);
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}${buildQuery(options.query)}`, {
      method,
      credentials: "include",
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: guard.signal,
      cache: "no-store",
    });
  } catch (error) {
    if (guard.didTimeout()) {
      throw new ApiError("TIMEOUT", CODE_MESSAGES.TIMEOUT, 0, true, null, requestId ?? null, null);
    }
    if (isApiError(error)) throw error;
    if (error instanceof Error && error.name === "AbortError") throw error;
    throw new ApiError("NETWORK_ERROR", "网络连接失败，请检查网络后重试", 0, true, null, requestId ?? null, null);
  } finally {
    guard.dispose();
  }

  if (!res.ok) {
    let body: unknown = null;
    try {
      body = await res.json();
    } catch {
      try {
        body = await res.text();
      } catch {
        body = null;
      }
    }
    throw errorFromResponse(res.status, body);
  }

  if (res.status === 204) return undefined as T;
  try {
    return (await res.json()) as T;
  } catch {
    return undefined as T;
  }
}

/** 生成 SSE 事件流地址（相对路径，交由 `openRunEvents` 拼接 API_BASE）。 */
export function runEventsUrl(runId: string, afterSeq?: number): string {
  const base = `/api/v2/runs/${encodeURIComponent(runId)}/events`;
  return afterSeq !== undefined && afterSeq > 0 ? `${base}?after_seq=${afterSeq}` : base;
}

// ---- 会话 ---------------------------------------------------------------- //

export function createSession(
  problemStatement: string,
  options: { requestId?: string; signal?: AbortSignal } = {}
): Promise<SessionView> {
  return apiV2<SessionView>("/api/v2/sessions", {
    method: "POST",
    body: { problem_statement: problemStatement },
    requestId: options.requestId,
    signal: options.signal,
  });
}

export function listSessions(
  params: { cursor?: string | null; limit?: number } = {}
): Promise<SessionListPage> {
  return apiV2<SessionListPage>("/api/v2/sessions", {
    query: { cursor: params.cursor ?? undefined, limit: params.limit },
  });
}

export function getSession(sessionId: string, options: { signal?: AbortSignal } = {}): Promise<SessionView> {
  return apiV2<SessionView>(`/api/v2/sessions/${encodeURIComponent(sessionId)}`, { signal: options.signal });
}

export function deleteSession(
  sessionId: string,
  options: { expectedSessionVersion?: number | null; requestId?: string } = {}
): Promise<DeletedSession> {
  const requestId = options.requestId ?? newRequestId();
  return apiV2<DeletedSession>(`/api/v2/sessions/${encodeURIComponent(sessionId)}`, {
    method: "DELETE",
    body: {
      request_id: requestId,
      expected_session_version: options.expectedSessionVersion ?? null,
    },
    requestId,
  });
}

export function listMessages(
  sessionId: string,
  params: { beforeSeq?: number; limit?: number; stage?: string; auditId?: string } = {}
): Promise<MessageListPage> {
  return apiV2<MessageListPage>(`/api/v2/sessions/${encodeURIComponent(sessionId)}/messages`, {
    query: {
      before_seq: params.beforeSeq,
      limit: params.limit,
      stage: params.stage,
      audit_id: params.auditId,
    },
  });
}

export function getMessage(messageId: string): Promise<MessageView> {
  return apiV2<MessageView>(`/api/v2/messages/${encodeURIComponent(messageId)}`);
}

export function getProductState(
  sessionId: string,
  version?: number
): Promise<ProductState> {
  return apiV2<ProductState>(`/api/v2/sessions/${encodeURIComponent(sessionId)}/product-state`, {
    query: { version },
  });
}

export function getCanvas(sessionId: string, productVersion?: number): Promise<CanvasProjection> {
  return apiV2<CanvasProjection>(`/api/v2/sessions/${encodeURIComponent(sessionId)}/canvas`, {
    query: { product_version: productVersion },
  });
}

export function getEvidence(evidenceId: string): Promise<Evidence> {
  return apiV2<Evidence>(`/api/v2/evidence/${encodeURIComponent(evidenceId)}`);
}

// ---- 命令 ---------------------------------------------------------------- //

export interface SendCommandOptions {
  expectedSessionVersion?: number | null;
  requestId?: string;
  /** 直接给出完整 payload 时使用（默认由 action 泛型推导） */
  rawPayload?: Record<string, unknown>;
}

export function sendCommand<A extends Action>(
  sessionId: string,
  action: A,
  payload?: CommandPayloadMap[A],
  options: SendCommandOptions = {}
): Promise<CommandReceipt> {
  const requestId = options.requestId ?? newRequestId();
  return apiV2<CommandReceipt>(`/api/v2/sessions/${encodeURIComponent(sessionId)}/commands`, {
    method: "POST",
    body: {
      request_id: requestId,
      action,
      expected_session_version: options.expectedSessionVersion ?? null,
      payload: options.rawPayload ?? (payload ?? {}),
    },
    requestId,
  });
}

// ---- Run ---------------------------------------------------------------- //

export function getRun(runId: string, options: { signal?: AbortSignal } = {}): Promise<RunView> {
  return apiV2<RunView>(`/api/v2/runs/${encodeURIComponent(runId)}`, { signal: options.signal });
}

export function getRunSnapshot(runId: string, options: { signal?: AbortSignal } = {}): Promise<RunSnapshot> {
  return apiV2<RunSnapshot>(`/api/v2/runs/${encodeURIComponent(runId)}/snapshot`, { signal: options.signal });
}

export function cancelRun(runId: string, options: { requestId?: string } = {}): Promise<RunView> {
  return apiV2<RunView>(`/api/v2/runs/${encodeURIComponent(runId)}/cancel`, {
    method: "POST",
    body: { request_id: options.requestId ?? newRequestId() },
    requestId: options.requestId,
  });
}

export function retryFailed(
  runId: string,
  options: { expectedSessionVersion?: number | null; requestId?: string } = {}
): Promise<RunReceipt> {
  const requestId = options.requestId ?? newRequestId();
  return apiV2<RunReceipt>(`/api/v2/runs/${encodeURIComponent(runId)}/retry-failed`, {
    method: "POST",
    body: {
      request_id: requestId,
      expected_session_version: options.expectedSessionVersion ?? null,
    },
    requestId,
  });
}

// ---- 审计 / 控制 ---------------------------------------------------------- //

export function getAudit(auditId: string): Promise<AuditSnapshot> {
  return apiV2<AuditSnapshot>(`/api/v2/audits/${encodeURIComponent(auditId)}`);
}

export function getControl(controlId: string): Promise<ControlReceipt> {
  return apiV2<ControlReceipt>(`/api/v2/controls/${encodeURIComponent(controlId)}`);
}

// ---- 闭环工作室 Loop Studio（契约 §6） ------------------------------------ //

export interface LoopListOptions {
  limit?: number;
  signal?: AbortSignal;
}

export function listLoops(sessionId: string, options: LoopListOptions = {}): Promise<LoopListPage> {
  return apiV2<LoopListPage>(`/api/v2/sessions/${encodeURIComponent(sessionId)}/loops`, {
    query: { limit: options.limit },
    signal: options.signal,
  });
}

export function getLoop(loopId: string, options: { signal?: AbortSignal } = {}): Promise<LoopSnapshot> {
  return apiV2<LoopSnapshot>(`/api/v2/loops/${encodeURIComponent(loopId)}`, {
    signal: options.signal,
  });
}

export function getLoopRound(
  loopId: string,
  ordinal: number,
  options: { signal?: AbortSignal } = {}
): Promise<LoopRoundView> {
  return apiV2<LoopRoundView>(
    `/api/v2/loops/${encodeURIComponent(loopId)}/rounds/${encodeURIComponent(String(ordinal))}`,
    { signal: options.signal }
  );
}

export function getLoopArtifact(
  loopId: string,
  artifactId: string,
  options: { signal?: AbortSignal } = {}
): Promise<LoopArtifactResponse> {
  return apiV2<LoopArtifactResponse>(
    `/api/v2/loops/${encodeURIComponent(loopId)}/artifacts/${encodeURIComponent(artifactId)}`,
    { signal: options.signal }
  );
}

/** `start_loop`：创建闭环并异步启动第 1 轮；已有活跃闭环时后端返回 409 LOOP_ACTIVE。 */
export function startLoop(
  sessionId: string,
  payload: StartLoopPayload,
  options: SendCommandOptions = {}
): Promise<CommandReceipt> {
  return sendCommand(sessionId, "start_loop", payload, options);
}

/** `loop_decision`：iterate / ship / stop；返回 RunReceipt（含 run_id 与 loop_id）。 */
export function decideLoop(
  sessionId: string,
  payload: LoopDecisionPayload,
  options: SendCommandOptions = {}
): Promise<CommandReceipt> {
  return sendCommand(sessionId, "loop_decision", payload, options);
}
