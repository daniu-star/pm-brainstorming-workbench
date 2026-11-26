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

/**
 * API 基址解析（第 42 批，线上登录 100% 失败的真因）。
 *
 * 线上 Vercel 项目的环境变量把 `NEXT_PUBLIC_API_URL` 覆盖成了
 * `https://123chenjunliang-pm-brainstorm-workbench.hf.space`，构建时被内联进 bundle，
 * 于是浏览器对 hf.space 发 JSON POST → 触发 CORS 预检 → HF 平台代理的预检响应
 * 没有 `Access-Control-Allow-Credentials` → 浏览器直接拒绝（表现为
 * `TypeError: Failed to fetch` / 「网络连接失败」）。这不是网络抖动，改重试也修不好。
 *
 * 因此生产环境（页面不是 localhost）一律走同源 `/backend`（由 next.config.js 的
 * rewrites 反代到 Space），只有本地开发才允许绝对地址（127.0.0.1:8001）。
 * 需要跨域直连的极少数场景用 `NEXT_PUBLIC_ALLOW_CROSS_ORIGIN_API=1` 显式放开。
 */
function resolveApiBase(): string {
  const configured = (process.env.NEXT_PUBLIC_API_URL ?? "").trim();
  const allowCrossOrigin = process.env.NEXT_PUBLIC_ALLOW_CROSS_ORIGIN_API === "1";
  const isAbsolute = /^https?:\/\//i.test(configured);

  if (!isAbsolute) {
    // 相对路径（/backend）或空值：直接使用；空值时线上同源、本地回退到 8001。
    return configured || (typeof window === "undefined" ? "/backend" : "http://127.0.0.1:8001");
  }

  if (typeof window === "undefined") return configured;
  const host = window.location.hostname;
  const isLocalPage = host === "localhost" || host === "127.0.0.1" || host === "::1";
  if (isLocalPage || allowCrossOrigin) return configured;

  // 生产页面 + 绝对基址 = 必然跨域，预检过不去：强制同源反代。
  return "/backend";
}

export const API_BASE = resolveApiBase();

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
  // 200 但正文不是 JSON（网关错误页 / 代理注入 HTML）：不能静默降级 legacy。
  INVALID_RESPONSE: "服务返回了无法解析的内容，请重试",
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

/** 网络层失败的统一文案（后端在唤醒、网关抖动、离线都走这里）。 */
export const NETWORK_ERROR_MESSAGE =
  "网络连接失败：服务端可能正在唤醒或网络抖动，已自动重试，请稍后再试一次";

/**
 * 不会自动重放的请求（写操作、真实计费调用、长任务）失败时的文案。
 *
 * 这些请求一旦重放可能重复提交或重复计费，所以宁可不重试；但文案不能跟着说
 * 「已自动重试」——否则用户会以为系统已经处理过了（线上出现过的误解）。
 */
export const NETWORK_ERROR_UNRETRIED_MESSAGE =
  "网络连接失败，请检查网络后重试（这类操作不会自动重放，避免重复提交或重复计费）";

/** 重试进度：让页面能显示「正在重试 2/4」而不是白屏等 20 秒。 */
export type RetryNotice = {
  /** 请求路径，例如 /api/v2/llm-config */
  path: string;
  /** 即将进行的第几次重试（1 表示第一次重试，不是第一次尝试） */
  attempt: number;
  /** 总共最多尝试几次（含首次） */
  attempts: number;
  /** 面向用户的简短原因 */
  reason: string;
};

type RetryListener = (notice: RetryNotice | null) => void;

const retryListeners = new Set<RetryListener>();

/**
 * 订阅重试进度；`notice === null` 表示「已恢复，可以收起提示」。
 *
 * 设计成模块级订阅而不是 React context：网络层在最底层，不应该依赖任何
 * 组件树；订阅者（全局横幅）挂载一次即可。
 */
export function subscribeRetryNotices(listener: RetryListener): () => void {
  retryListeners.add(listener);
  return () => {
    retryListeners.delete(listener);
  };
}

function emitRetry(notice: RetryNotice | null): void {
  for (const listener of retryListeners) {
    try {
      listener(notice);
    } catch {
      // 订阅者自己报错不能影响请求本身。
    }
  }
}

function retryReason(error: unknown): string {
  if (isApiError(error) && error.code === "TIMEOUT") return "服务端响应超时";
  if (isApiError(error) && [502, 503, 504].includes(error.status)) return "服务暂时不可用";
  return "网络连接中断";
}

/** fetch 抛出的 TypeError（浏览器多为 "Failed to fetch"）判定。 */
export function isNetworkFailure(error: unknown): boolean {
  if (isApiError(error)) {
    return error.code === "NETWORK_ERROR" || error.code === "TIMEOUT" || [502, 503, 504].includes(error.status);
  }
  if (!(error instanceof Error)) return false;
  if (error.name === "AbortError") return false;
  const text = error.message || "";
  return (
    error instanceof TypeError ||
    /failed to fetch|network ?error|load failed|fetch failed|terminated|other side closed/i.test(text)
  );
}

/** 面向用户的错误文案：优先错误码中文表，其次后端 message，最后按状态码。 */
export function errorMessage(error: unknown): string {
  if (isApiError(error)) {
    if (error.code === "NETWORK_ERROR") {
      // 只回放我们自己写下的两档网络文案，避免把上游英文原文透出给用户。
      if (error.message === NETWORK_ERROR_MESSAGE || error.message === NETWORK_ERROR_UNRETRIED_MESSAGE) {
        return error.message;
      }
      return NETWORK_ERROR_MESSAGE;
    }
    if (error.code === "TIMEOUT") return `${CODE_MESSAGES.TIMEOUT}（服务端连接超时，可能是后端正在唤醒）`;
    return CODE_MESSAGES[error.code] ?? (error.message || statusMessage(error.status));
  }
  if (isNetworkFailure(error)) return NETWORK_ERROR_MESSAGE;
  if (error instanceof Error && error.name === "AbortError") return "请求已取消";
  if (error instanceof Error && error.message) return error.message;
  // 纯对象错误（如视图层传入 { message, notFound }）也要能取到 message。
  if (
    typeof error === "object" &&
    error !== null &&
    typeof (error as { message?: unknown }).message === "string" &&
    (error as { message: string }).message
  ) {
    return (error as { message: string }).message;
  }
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
    // 502/503/504 是网关/后端暂不可用，不是「端点不存在」：否则一次网络抖动就会把
    // 整个会话永久降级到 legacy 写路径（QA 第 5 轮 P1 的同类问题，2026-11-26 线上复现）。
    return [404, 405, 501].includes(error.status);
  }
  // fetch 网络层失败（离线 / 后端唤醒中 / 网关抖动）只代表「这次没拿到响应」，
  // 绝不能据此判断端点不存在——交给调用方按可重试错误提示。
  return false;
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
  const method = (options?.method ?? "GET").toUpperCase();
  const retryable = method === "GET" || method === "HEAD";
  const attempts = retryable ? LEGACY_ATTEMPTS : 1;
  let lastError: unknown = null;

  for (let attempt = 0; attempt < attempts; attempt += 1) {
    if (attempt > 0) {
      emitRetry({ path, attempt, attempts, reason: retryReason(lastError) });
      await sleepMs(legacyBackoff(attempt));
    }
    try {
      const result = await apiOnce<T>(path, options, attempt > 0);
      if (attempt > 0) emitRetry(null);
      return result;
    } catch (error) {
      lastError = normalizeFetchFailure(error, null, attempt > 0);
      if (attempt + 1 >= attempts || !isNetworkFailure(lastError)) {
        emitRetry(null);
        throw lastError;
      }
    }
  }
  emitRetry(null);
  throw lastError;
}

/** 旧入口的重试次数（历史会话等只读数据，多试几次成本很低）。 */
const LEGACY_ATTEMPTS = 3;

function legacyBackoff(attempt: number): number {
  const base = attempt === 1 ? 600 : 1_400;
  return base + Math.floor(Math.random() * 200);
}

/** 把浏览器的 TypeError 归一成 ApiError，避免页面直接显示英文 "Failed to fetch"。 */
function normalizeFetchFailure(
  error: unknown,
  requestId: string | null,
  retried = false
): unknown {
  if (isApiError(error)) return error;
  if (error instanceof Error && error.name === "AbortError") return error;
  if (isNetworkFailure(error)) {
    return new ApiError(
      "NETWORK_ERROR",
      retried ? NETWORK_ERROR_MESSAGE : NETWORK_ERROR_UNRETRIED_MESSAGE,
      0,
      true,
      null,
      requestId,
      null
    );
  }
  return error;
}

function sleepMs(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function apiOnce<T>(
  path: string,
  options?: RequestInit,
  retried = false
): Promise<T> {
  // 旧入口此前完全没有超时：网关挂起时 fetch 会一直吊着，页面永远停在加载态。
  const guard = guardRequest(options?.signal ?? undefined, REQUEST_TIMEOUT_MS);
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      ...options,
      signal: guard.signal,
    });
  } catch (error) {
    if (guard.didTimeout()) {
      throw new ApiError("TIMEOUT", CODE_MESSAGES.TIMEOUT, 0, true, null, null, null);
    }
    throw normalizeFetchFailure(error, null, retried);
  } finally {
    guard.dispose();
  }
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
  method?: "GET" | "POST" | "PUT" | "DELETE" | "PATCH";
  body?: unknown;
  query?: Record<string, QueryValue>;
  /** 幂等请求 ID；写请求缺省自动生成 */
  requestId?: string;
  signal?: AbortSignal;
  /** 单请求超时覆盖（毫秒）；沙箱执行这类长任务需要 > 默认 15s */
  timeoutMs?: number;
  headers?: Record<string, string>;
  /**
   * 瞬时故障（fetch 失败 / 超时 / 502-504）是否自动重试。
   *
   * 默认 GET 开启、写请求关闭；携带 `Idempotency-Key` 的写请求（命令、建会话、
   * 取消、重试）可以显式打开——后端按 request_id 去重，重放不会产生副作用。
   */
  retryTransient?: boolean;
}

const WRITE_METHODS = new Set(["POST", "PATCH", "DELETE", "PUT"]);

/** 单次 HTTP 请求的默认超时；避免后端/网关挂起时按钮永久停在「提交中」。 */
export const REQUEST_TIMEOUT_MS = 15_000;

/**
 * 瞬时故障重试次数与退避（2026-11-26 线上实测：HF Space 前置代理约 5%~10% 的请求
 * 会挂起约 21 秒后直接断开，curl 返回 000、直连 Space 同样复现——这是传输层抖动，
 * 不是接口错误，重试即可恢复）。
 *
 * 第 42 批调参的理由：
 * 1. 首跳超时 12s < 网关 21s 断点：在网关掐断之前就主动放弃、换一条新连接，
 *    用户等待从「21s 后失败」变成「12s 内自动恢复」。挂起连接的 socket 不会复用。
 * 2. 4 次尝试 + 抖动退避（800/1600/3000ms ±250ms）：实测单次抖动持续时间很短，
 *    多给一次机会可把「偶发失败」压到接近 0；抖动避免同一波请求同时重试。
 * 3. 重试时超时收紧到 8s：重试意味着上一条连接已不可用，快速失败换新连接更划算。
 */
export const TRANSIENT_ATTEMPTS = 4;
/** 首次尝试的超时：必须早于网关 21s 断点。 */
export const FIRST_ATTEMPT_TIMEOUT_MS = 12_000;
const TRANSIENT_BACKOFF_MS = [800, 1_600, 3_000] as const;
const RETRY_BACKOFF_JITTER_MS = 250;
/** 重试时的单次超时（毫秒）：重试说明上一条连接已不可用，缩短等待快速换新连接。 */
const RETRY_TIMEOUT_MS = [8_000, 8_000, 8_000] as const;

function transientBackoff(attempt: number): number {
  const base = TRANSIENT_BACKOFF_MS[Math.min(attempt - 1, TRANSIENT_BACKOFF_MS.length - 1)];
  return base + Math.floor(Math.random() * RETRY_BACKOFF_JITTER_MS);
}

/** 判断该错误是否值得自动重试。 */
export function isTransientFailure(error: unknown): boolean {
  if (!isApiError(error)) return isNetworkFailure(error);
  if (error.code === "NETWORK_ERROR" || error.code === "TIMEOUT") return true;
  return [502, 503, 504].includes(error.status);
}

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
  const retry = options.retryTransient ?? !isWrite;
  const attempts = retry ? TRANSIENT_ATTEMPTS : 1;

  let lastError: unknown = null;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    if (attempt > 0) {
      if (options.signal?.aborted) throw lastError;
      emitRetry({ path, attempt, attempts, reason: retryReason(lastError) });
      await sleepMs(transientBackoff(attempt));
      if (options.signal?.aborted) {
        emitRetry(null);
        throw lastError;
      }
    }
    const attemptOptions: V2RequestOptions =
      attempt === 0
        ? { ...options, timeoutMs: options.timeoutMs ?? Math.min(REQUEST_TIMEOUT_MS, FIRST_ATTEMPT_TIMEOUT_MS) }
        : {
            ...options,
            timeoutMs: Math.min(
              options.timeoutMs ?? REQUEST_TIMEOUT_MS,
              RETRY_TIMEOUT_MS[Math.min(attempt - 1, RETRY_TIMEOUT_MS.length - 1)],
            ),
          };
    try {
      const result = await apiV2Once<T>(path, attemptOptions, isWrite, requestId, attempt > 0);
      if (attempt > 0) emitRetry(null);
      return result;
    } catch (error) {
      lastError = error;
      if (!retry || !isTransientFailure(error)) {
        if (retry || attempt > 0) emitRetry(null);
        throw error;
      }
    }
  }
  emitRetry(null);
  throw lastError;
}

async function apiV2Once<T>(
  path: string,
  options: V2RequestOptions,
  isWrite: boolean,
  requestId: string | undefined,
  retried: boolean
): Promise<T> {
  const method = options.method ?? "GET";

  const headers: Record<string, string> = { Accept: "application/json", ...(options.headers ?? {}) };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (isWrite && requestId) headers["Idempotency-Key"] = requestId;

  const guard = guardRequest(options.signal, options.timeoutMs ?? REQUEST_TIMEOUT_MS);
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
    // 只有真正的传输层失败才归一为 NETWORK_ERROR（这类错误值得自动重试）；
    // 代码里抛出的普通 Error 说明是本地逻辑问题，原样抛给调用方，避免被重试掩盖。
    if (isNetworkFailure(error)) {
      throw new ApiError(
        "NETWORK_ERROR",
        retried ? NETWORK_ERROR_MESSAGE : NETWORK_ERROR_UNRETRIED_MESSAGE,
        0,
        true,
        null,
        requestId ?? null,
        null,
      );
    }
    throw error;
  } finally {
    guard.dispose();
  }

  if (!res.ok) {
    // 第 42 批：必须先 text 再 JSON.parse。旧写法先 json() 再 text()，
    // 网关返回 HTML 502 页面时 body 已被消费，text() 抛错被吞 → 丢掉 code/request_id。
    let body: unknown = null;
    let text = "";
    try {
      text = await res.text();
    } catch {
      text = "";
    }
    if (text) {
      try {
        body = JSON.parse(text);
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
    // 200 但正文无法解析（网关错误页 / 代理注入 HTML）时必须抛 ApiError：
    // 旧实现 `return undefined as T` 会让调用方读 view.phase 抛 TypeError，
    // 而 isEndpointUnavailable 把 TypeError 当作「v2 端点不存在」→ 静默降级
    // legacy，工作台的审计/Loop/简报入口整体消失且无任何提示（FE-02 P1）。
    throw new ApiError(
      "INVALID_RESPONSE",
      CODE_MESSAGES.INVALID_RESPONSE,
      res.status,
      true,
      null,
      requestId ?? null,
      null
    );
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
    retryTransient: true,
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
    retryTransient: true,
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

export interface RunListPage {
  items: RunView[];
  next_cursor: string | null;
}

export function listSessionRuns(
  sessionId: string,
  params: { status?: string; limit?: number } = {}
): Promise<RunListPage> {
  return apiV2<RunListPage>(`/api/v2/sessions/${encodeURIComponent(sessionId)}/runs`, {
    query: { status: params.status, limit: params.limit },
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
    // 命令都带 Idempotency-Key，后端按 request_id 去重，重放安全。
    retryTransient: true,
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
    retryTransient: true,
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
    retryTransient: true,
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
