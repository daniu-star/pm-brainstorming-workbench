"""第 42 批：网络韧性升级 —— 更快放弃挂起连接、更多次重试、可靠的重试进度通知。"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent
path = ROOT / "lib" / "api.ts"
src = path.read_text(encoding="utf-8")


def patch(old: str, new: str, *, count: int = 1) -> None:
    actual = src.count(old)
    assert actual == count, f"expected {count} occurrence(s), found {actual}:\n{old[:160]}"
    return src.replace(old, new)


# ------------------------------------------------------------------ 1. 文案 + 通知基建
OLD_MSG = '''/** 网络层失败的统一文案（后端在唤醒、网关抖动、离线都走这里）。 */
export const NETWORK_ERROR_MESSAGE =
  "网络连接失败：服务端可能正在唤醒或网络抖动，已自动重试，请稍后再试一次";
'''

NEW_MSG = '''/** 网络层失败的统一文案（后端在唤醒、网关抖动、离线都走这里）。 */
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
'''
src = patch(OLD_MSG, NEW_MSG)

# ------------------------------------------------------------------ 2. errorMessage 尊重「未重试」文案
src = patch(
    '    if (error.code === "NETWORK_ERROR") return NETWORK_ERROR_MESSAGE;',
    '    if (error.code === "NETWORK_ERROR") return error.message || NETWORK_ERROR_MESSAGE;',
)

# ------------------------------------------------------------------ 3. 旧入口 api()：3 次尝试 + 超时保护 + 进度通知
OLD_API = '''export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const method = (options?.method ?? "GET").toUpperCase();
  const attempts = method === "GET" || method === "HEAD" ? 2 : 1;
  let lastError: unknown = null;

  for (let attempt = 0; attempt < attempts; attempt += 1) {
    if (attempt > 0) await sleepMs(700);
    try {
      return await apiOnce<T>(path, options);
    } catch (error) {
      lastError = normalizeFetchFailure(error, null);
      if (attempt + 1 >= attempts || !isNetworkFailure(lastError)) throw lastError;
    }
  }
  throw lastError;
}
'''

NEW_API = '''export async function api<T>(path: string, options?: RequestInit): Promise<T> {
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
      const result = await apiOnce<T>(path, options);
      if (attempt > 0) emitRetry(null);
      return result;
    } catch (error) {
      lastError = normalizeFetchFailure(error, null);
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
'''
src = patch(OLD_API, NEW_API)

# ------------------------------------------------------------------ 4. apiOnce：加超时保护
OLD_ONCE_CATCH = '''async function apiOnce<T>(path: string, options?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      ...options,
    });
  } catch (error) {
    throw normalizeFetchFailure(error, null);
  }
'''

NEW_ONCE_CATCH = '''async function apiOnce<T>(path: string, options?: RequestInit): Promise<T> {
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
    throw normalizeFetchFailure(error, null);
  } finally {
    guard.dispose();
  }
'''
src = patch(OLD_ONCE_CATCH, NEW_ONCE_CATCH)

# ------------------------------------------------------------------ 5. 重试策略常量
OLD_POLICY = '''/**
 * 瞬时故障重试次数与退避（2026-11-26 线上实测：HF Space 前置代理约 5%~10% 的请求
 * 会挂起约 21 秒后直接断开，curl 返回 000、直连 Space 同样复现——这是传输层抖动，
 * 不是接口错误，重试即可恢复）。
 */
export const TRANSIENT_ATTEMPTS = 3;
const TRANSIENT_BACKOFF_MS = [700, 1_800] as const;
/** 重试时的单次超时（毫秒）：重试说明上一条连接已不可用，缩短等待快速换新连接。 */
const RETRY_TIMEOUT_MS = [8_000, 6_000] as const;
'''

NEW_POLICY = '''/**
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
'''
src = patch(OLD_POLICY, NEW_POLICY)

# ------------------------------------------------------------------ 6. apiV2 主循环
OLD_LOOP = '''export async function apiV2<T>(path: string, options: V2RequestOptions = {}): Promise<T> {
  const method = options.method ?? "GET";
  const isWrite = WRITE_METHODS.has(method);
  const requestId = options.requestId ?? (isWrite ? newRequestId() : undefined);
  const retry = options.retryTransient ?? !isWrite;
  const attempts = retry ? TRANSIENT_ATTEMPTS : 1;

  let lastError: unknown = null;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    if (attempt > 0) {
      if (options.signal?.aborted) throw lastError;
      await sleepMs(TRANSIENT_BACKOFF_MS[Math.min(attempt - 1, TRANSIENT_BACKOFF_MS.length - 1)]);
      if (options.signal?.aborted) throw lastError;
    }
    const attemptOptions: V2RequestOptions =
      attempt === 0
        ? options
        : {
            ...options,
            timeoutMs: Math.min(
              options.timeoutMs ?? REQUEST_TIMEOUT_MS,
              RETRY_TIMEOUT_MS[Math.min(attempt - 1, RETRY_TIMEOUT_MS.length - 1)],
            ),
          };
    try {
      return await apiV2Once<T>(path, attemptOptions, isWrite, requestId);
    } catch (error) {
      lastError = error;
      if (!retry || !isTransientFailure(error)) throw error;
    }
  }
  throw lastError;
}
'''

NEW_LOOP = '''export async function apiV2<T>(path: string, options: V2RequestOptions = {}): Promise<T> {
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
'''
src = patch(OLD_LOOP, NEW_LOOP)

# ------------------------------------------------------------------ 7. apiV2Once：区分已重试/未重试文案
src = patch(
    '''async function apiV2Once<T>(
  path: string,
  options: V2RequestOptions,
  isWrite: boolean,
  requestId: string | undefined
): Promise<T> {''',
    '''async function apiV2Once<T>(
  path: string,
  options: V2RequestOptions,
  isWrite: boolean,
  requestId: string | undefined,
  retried: boolean
): Promise<T> {''',
)

src = patch(
    '''      throw new ApiError(
        "NETWORK_ERROR",
        NETWORK_ERROR_MESSAGE,''',
    '''      throw new ApiError(
        "NETWORK_ERROR",
        retried ? NETWORK_ERROR_MESSAGE : NETWORK_ERROR_UNRETRIED_MESSAGE,''',
)

path.write_text(src, encoding="utf-8", newline="")
print("patched lib/api.ts")
