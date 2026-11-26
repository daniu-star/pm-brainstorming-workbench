/**
 * SSE 解析与 Run 事件订阅（FR-06 / M1 §6）
 *
 * 设计要点（对应 `docs/v4-m1-interfaces.md` §6 与 PRD FR-06）：
 * - 解析层是**纯函数** `parseSSEChunk(buffer, chunk)`，不做任何 I/O，便于用
 *   `node --test` 直接单测（见 `frontend/tests/sse.test.cjs`）。
 * - 支持 LF / CRLF、多行 `data:`、`id:`、`event:`、注释行（`:` 开头）、
 *   UTF-8 跨块（字符串拼接，绝不切割代理对）、空行分帧；末尾不完整帧留在 `rest`。
 * - 订阅层 `openRunEvents` 用 fetch + ReadableStream：按 `run_id + event_seq` 去重，
 *   终结事件只回调一次（once guard），心跳注释忽略，`AbortError` 视为 abort 不报错。
 * - 旧导出 `createSSEConnection` / `SSEEvent` 保留（迁移期旧聊天流仍在使用）。
 */

import { apiUrl } from "./api";
import { type RunEvent, isTerminalEventType } from "./contracts.generated";

// --------------------------------------------------------------------------- //
// 纯解析器
// --------------------------------------------------------------------------- //

export interface ParsedSSEEvent {
  /** 服务端 `id:` 字段（v2 事件流中等于 event_seq） */
  id?: string;
  /** 服务端 `event:` 字段（v2 事件流中等于事件 type） */
  event?: string;
  /** 多行 data 以 "\n" 连接后的完整载荷 */
  data: string;
}

interface SSEFrame {
  id?: string;
  event?: string;
  dataLines: string[];
}

function emptyFrame(): SSEFrame {
  return { dataLines: [] };
}

/** 取冒号后的值，并按规范去掉**一个**前导空格 */
function fieldValue(line: string, colon: number): string {
  const raw = line.slice(colon + 1);
  return raw.startsWith(" ") ? raw.slice(1) : raw;
}

/**
 * 把新到的 chunk 拼到 buffer 后解析出所有**完整帧**，返回完整事件与剩余不完整内容。
 *
 * 调用方只需维护 `rest`：
 *   const { events, rest } = parseSSEChunk(buffer, chunk); buffer = rest;
 *
 * 约定：
 * - 只有空行才分发一帧；没有 `data` 的帧（注释块 / 仅有 id）不分发，直接丢弃。
 * - buffer 中若最后一帧没有结尾换行，整帧留在 `rest`（不猜测是否完整）。
 * - chunk 允许是空串：等价于「把 buffer 中已完整的帧全部解析出来」。
 */
export function parseSSEChunk(
  buffer: string,
  chunk: string
): { events: ParsedSSEEvent[]; rest: string } {
  const input = buffer + chunk;
  const events: ParsedSSEEvent[] = [];
  let frame = emptyFrame();
  let cursor = 0;
  // 当前「未终结帧」的起始偏移：帧未遇到空行就结束（TCP 分片）时，
  // 整个帧必须原样留在 rest 里，否则 id/event/data 跨块丢失。
  let frameStart = 0;

  while (true) {
    const newline = input.indexOf("\n", cursor);
    if (newline === -1) break;

    let line = input.slice(cursor, newline);
    cursor = newline + 1;
    if (line.endsWith("\r")) line = line.slice(0, line.length - 1);

    if (line === "") {
      // 空行 = 分帧
      if (frame.dataLines.length > 0) {
        const parsed: ParsedSSEEvent = { data: frame.dataLines.join("\n") };
        if (frame.id !== undefined) parsed.id = frame.id;
        if (frame.event !== undefined) parsed.event = frame.event;
        events.push(parsed);
      }
      frame = emptyFrame();
      frameStart = cursor;
      continue;
    }

    if (line.startsWith(":")) {
      // 注释行（心跳 / 保活），整行忽略
      continue;
    }

    const colon = line.indexOf(":");
    if (colon === -1) {
      // 无冒号的字段名：规范要求按空值处理，我们只关心已知字段，直接忽略
      continue;
    }
    const field = line.slice(0, colon);
    const value = fieldValue(line, colon);

    switch (field) {
      case "data":
        frame.dataLines.push(value);
        break;
      case "id":
        if (!value.includes("\u0000")) frame.id = value;
        break;
      case "event":
        frame.event = value;
        break;
      case "retry":
        // 浏览器原生 EventSource 的重连间隔，由我们自己控制重连，忽略
        break;
      default:
        // 未知字段：忽略（向前兼容）
        break;
    }
  }

  // 未终结帧必须整帧留在 rest 里（跨块 TCP 分片时 id/event/data 行不能丢）
  return { events, rest: input.slice(frameStart) };
}

// --------------------------------------------------------------------------- //
// Run 事件订阅（GET /api/v2/runs/{rid}/events）
// --------------------------------------------------------------------------- //

export type RunStreamCloseReason = "terminal" | "eof" | "abort" | "idle";

/**
 * 事件流空闲看门狗（FE-04）：TCP 半开时 reader.read() 可能永远不返回，
 * 页面会永久「正在生成」且输入框禁用；服务端心跳注释会刷新计时。
 */
export const DEFAULT_IDLE_TIMEOUT_MS = 45_000;

export interface OpenRunEventsOptions {
  /** 断点续读：>=1 的 event_seq，服务端与 Last-Event-ID 取较大值 */
  afterSeq?: number;
  onEvent: (event: RunEvent) => void;
  onError: (error: unknown) => void;
  onClose: (reason: RunStreamCloseReason) => void;
  /** 外部取消信号（页面卸载 / 切换会话） */
  signal?: AbortSignal;
  /** 跨连接复用的去重集合；同一 Run 重连时应传同一个 Set */
  seen?: Set<string>;
  /** 空闲超时（毫秒）：超过该时间没有收到任何字节即视为断线，默认 45s */
  idleTimeoutMs?: number;
  /** 测试注入用；默认为全局 fetch */
  fetchImpl?: typeof fetch;
}

export function eventKey(event: Pick<RunEvent, "run_id" | "event_seq">): string {
  return `${event.run_id}:${event.event_seq}`;
}

function isAbortError(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    ((error as { name?: string }).name === "AbortError" ||
      (error as { code?: string }).code === "ABORT_ERR")
  );
}

function isIdleTimeout(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    (error as { name?: string }).name === "IdleTimeoutError"
  );
}

function withAfterSeq(url: string, afterSeq?: number): string {
  const absolute = url.startsWith("/") ? apiUrl(url) : url;
  if (afterSeq === undefined || afterSeq === null || afterSeq < 1) return absolute;
  const separator = absolute.includes("?") ? "&" : "?";
  return `${absolute}${separator}after_seq=${afterSeq}`;
}

/**
 * 打开一个 Run 的事件流（JSON 事件，不是浏览器 EventSource）。
 *
 * 返回 `cancel()`：静默停止读取（不触发 onClose），用于组件卸载 / 切会话。
 * `opts.signal` 取消时触发 `onClose("abort")`；`cancel()` 不触发回调。
 * 终结事件（run.completed / run.failed / run.cancelled）先 onEvent 再 onClose("terminal")，
 * 且两者都经过同一个 once guard，保证不会重复。
 */
export function openRunEvents(url: string, opts: OpenRunEventsOptions): () => void {
  const fetchImpl = opts.fetchImpl ?? fetch;
  const controller = new AbortController();
  const seen = opts.seen ?? new Set<string>();
  let closed = false;
  let stopped = false;

  let idleTimer: ReturnType<typeof setTimeout> | null = null;
  const clearIdleTimer = (): void => {
    if (idleTimer) {
      clearTimeout(idleTimer);
      idleTimer = null;
    }
  };

  const close = (reason: RunStreamCloseReason): void => {
    if (closed) return;
    closed = true;
    clearIdleTimer();
    try {
      opts.onClose(reason);
    } catch {
      /* 回调异常不应中断清理 */
    }
  };

  const reportError = (error: unknown): void => {
    if (stopped) return;
    try {
      opts.onError(error);
    } catch {
      /* ignore */
    }
  };

  const handleExternalAbort = (): void => {
    stopped = true;
    controller.abort();
    close("abort");
  };

  if (opts.signal) {
    if (opts.signal.aborted) {
      handleExternalAbort();
      return () => undefined;
    }
    opts.signal.addEventListener("abort", handleExternalAbort, { once: true });
  }

  const run = async (): Promise<void> => {
    const headers: Record<string, string> = { Accept: "text/event-stream" };
    if (opts.afterSeq !== undefined && opts.afterSeq > 0) {
      headers["Last-Event-ID"] = String(opts.afterSeq);
    }

    // 传输层抖动（后端唤醒中 / 网关 21s 断开）时，事件流会在「一个字都还没收到」
    // 的阶段直接 EOF。此时重试是安全的：订阅是幂等的，after_seq 保证不会重复回放。
    const CONNECT_ATTEMPTS = 3;
    const CONNECT_BACKOFF_MS = [1_200, 3_000];
    // 网关在 Space 唤醒期会直接回 502/503/504 HTML，这类响应必须和网络异常一样重试；
    // 连接阶段也要自己的超时，否则只能等网关 21s 断开才动（第 42 批）。
    const CONNECT_TIMEOUT_MS = 10_000;
    const RETRYABLE_STATUS = [502, 503, 504];
    let response: Response | null = null;
    let lastError: unknown = null;

    for (let attempt = 0; attempt < CONNECT_ATTEMPTS; attempt += 1) {
      if (attempt > 0) {
        await new Promise((resolve) =>
          setTimeout(resolve, CONNECT_BACKOFF_MS[Math.min(attempt - 1, CONNECT_BACKOFF_MS.length - 1)]),
        );
        if (stopped || controller.signal.aborted) {
          close("abort");
          return;
        }
      }
      const timeoutController = new AbortController();
      const timeoutTimer = setTimeout(() => timeoutController.abort(), CONNECT_TIMEOUT_MS);
      const onExternalAbort = (): void => timeoutController.abort();
      controller.signal.addEventListener("abort", onExternalAbort, { once: true });
      try {
        response = await fetchImpl(withAfterSeq(url, opts.afterSeq), {
          method: "GET",
          credentials: "include",
          headers,
          cache: "no-store",
          signal: timeoutController.signal,
        });
        if (RETRYABLE_STATUS.includes(response.status) && attempt + 1 < CONNECT_ATTEMPTS) {
          lastError = new Error(`事件流连接失败（HTTP ${response.status}）`);
          response = null;
          continue;
        }
        break;
      } catch (error) {
        if (stopped || controller.signal.aborted || isAbortError(error)) {
          close("abort");
          return;
        }
        lastError = error;
      } finally {
        clearTimeout(timeoutTimer);
        controller.signal.removeEventListener("abort", onExternalAbort);
      }
    }

    if (!response) {
      reportError(lastError ?? new Error("事件流连接失败"));
      close("eof");
      return;
    }

    if (!response.ok) {
      // 延迟 require 错误构造，避免 sse.ts 与 api.ts 形成强耦合
      const { errorFromResponse } = await import("./api");
      let payload: unknown = null;
      try {
        payload = await response.json();
      } catch {
        try {
          payload = await response.text();
        } catch {
          payload = null;
        }
      }
      reportError(errorFromResponse(response.status, payload));
      close("eof");
      return;
    }

    const reader = response.body?.getReader();
    if (!reader) {
      reportError(new Error("事件流不可读（响应缺少 body）"));
      close("eof");
      return;
    }

    const readWithIdleTimeout = async (): Promise<ReadableStreamReadResult<Uint8Array>> => {
      if (idleTimeoutMs <= 0) return reader.read();
      return new Promise<ReadableStreamReadResult<Uint8Array>>((resolve, reject) => {
        idleTimer = setTimeout(() => {
          idleTimer = null;
          const timeoutError = new Error("事件流空闲超时");
          timeoutError.name = "IdleTimeoutError";
          reject(timeoutError);
        }, idleTimeoutMs);
        reader.read().then(
          (result) => {
            clearIdleTimer();
            resolve(result);
          },
          (error) => {
            clearIdleTimer();
            reject(error);
          }
        );
      });
    };

    const decoder = new TextDecoder("utf-8");
    let buffer = "";
    const idleTimeoutMs = opts.idleTimeoutMs ?? DEFAULT_IDLE_TIMEOUT_MS;

    try {
      while (!stopped) {
        const { done, value } = await readWithIdleTimeout();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const parsed = parseSSEChunk(buffer, "");
        buffer = parsed.rest;

        for (const raw of parsed.events) {
          if (!raw.data) continue;

          let data: Record<string, unknown>;
          try {
            const decoded = JSON.parse(raw.data);
            if (typeof decoded !== "string" && typeof decoded !== "object") continue;
            data = typeof decoded === "string" ? { payload: decoded } : (decoded as Record<string, unknown>);
          } catch (error) {
            // 协议错误：记录但不结束连接（服务端会继续发后续帧）
            reportError(error);
            continue;
          }

          if (data.event_seq === undefined && raw.id !== undefined) {
            const seq = Number(raw.id);
            if (Number.isFinite(seq)) data.event_seq = seq;
          }
          if (data.type === undefined && raw.event !== undefined) {
            data.type = raw.event;
          }

          const key = eventKey({
            run_id: String(data.run_id ?? ""),
            event_seq: Number(data.event_seq ?? 0),
          });
          if (seen.has(key)) continue;
          seen.add(key);

          const event = data as unknown as RunEvent;
          try {
            opts.onEvent(event);
          } catch {
            /* 渲染层异常不应打断事件流 */
          }

          if (isTerminalEventType(event.type)) {
            try {
              await reader.cancel();
            } catch {
              /* ignore */
            }
            close("terminal");
            return;
          }
        }
      }
    } catch (error) {
      if (stopped || isAbortError(error)) {
        close("abort");
        return;
      }
      if (isIdleTimeout(error)) {
        try {
          await reader.cancel();
        } catch {
          /* ignore */
        }
        close("idle");
        return;
      }
      reportError(error);
      close("eof");
      return;
    }

    if (!stopped && !closed) close("eof");
  };

  void run();

  return () => {
    // 静默清理：调用方主动取消时不再回调任何 onClose
    stopped = true;
    clearIdleTimer();
    if (opts.signal) opts.signal.removeEventListener("abort", handleExternalAbort);
    controller.abort();
    closed = true;
  };
}

// --------------------------------------------------------------------------- //
// 旧版聊天流（迁移期兼容，勿在新代码使用）
// --------------------------------------------------------------------------- //

export interface SSEEvent {
  type: string;
  role?: string;
  role_name?: string;
  token?: string;
  phase?: string;
  message?: string;
  action?: string;
  node?: unknown;
  covered?: string[];
  /** role_done 携带的全量回复内容，前端优先于 token 流拼接（B110）。 */
  content?: string;
}

/**
 * 旧接口：POST 一个 JSON 请求体，逐行解析 `data: {...}` 的聊天事件。
 * 保留给尚未迁移的 `/api/brainstorm/*`、`/api/interview/*` 调用。
 */
export function createSSEConnection(
  endpoint: string,
  body: unknown,
  onEvent: (event: SSEEvent) => void,
  onDone: () => void,
  onError: (err: string) => void
): AbortController {
  const controller = new AbortController();
  let doneCalled = false;

  const finish = (): void => {
    if (doneCalled) return;
    doneCalled = true;
    onDone();
  };

  fetch(apiUrl(endpoint), {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: controller.signal,
  })
    .then(async (response) => {
      if (!response.ok) {
        const { statusMessage } = await import("./api");
        onError(statusMessage(response.status));
        return;
      }

      const reader = response.body?.getReader();
      if (!reader) {
        onError("网络连接失败，请检查网络后重试");
        return;
      }

      const decoder = new TextDecoder("utf-8");
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const parsed = parseSSEChunk(buffer, "");
        buffer = parsed.rest;

        for (const frame of parsed.events) {
          if (!frame.data) continue;
          try {
            const data = JSON.parse(frame.data) as SSEEvent & { type?: string };
            if (data.type === "done") {
              finish();
            } else {
              onEvent(data);
            }
          } catch {
            // 忽略无法解析的行（旧服务端会发空行/心跳）
          }
        }
      }
      finish();
    })
    .catch((err: unknown) => {
      if (!isAbortError(err)) {
        onError("网络连接失败，请检查网络后重试");
      }
    });

  return controller;
}
