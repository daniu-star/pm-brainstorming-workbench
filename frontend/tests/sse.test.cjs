"use strict";

/**
 * SSE 解析器 + Run 事件订阅的单元测试（node --test）
 *
 * 被测代码：frontend/lib/sse.ts → 由 `tsc -p tsconfig.test.json` 编译到
 * `node_modules/.cache/test-build/lib/sse.js`，这里用 require 加载（不引入额外依赖）。
 */

const { test } = require("node:test");
const assert = require("node:assert/strict");

const sse = require("../node_modules/.cache/test-build/lib/sse.js");
const { parseSSEChunk, eventKey, openRunEvents } = sse;

// --------------------------------------------------------------------------- //
// parseSSEChunk：分帧 / 行尾 / 多行 data / id / event / 注释
// --------------------------------------------------------------------------- //

test("LF 单帧解析", () => {
  const result = parseSSEChunk("", 'data: {"a":1}\n\n');
  assert.equal(result.events.length, 1);
  assert.equal(result.events[0].data, '{"a":1}');
  assert.equal(result.rest, "");
});

test("CRLF 分帧且去掉末尾 \\r", () => {
  const result = parseSSEChunk("", "id: 7\r\nevent: run.started\r\ndata: hello\r\n\r\n");
  assert.equal(result.events.length, 1);
  assert.deepEqual(result.events[0], { id: "7", event: "run.started", data: "hello" });
  assert.equal(result.rest, "");
});

test("多行 data 以 \\n 连接", () => {
  const result = parseSSEChunk("", "data: line1\ndata: line2\ndata: line3\n\n");
  assert.equal(result.events.length, 1);
  assert.equal(result.events[0].data, "line1\nline2\nline3");
});

test("注释行（: 开头）被忽略，不产生事件", () => {
  const result = parseSSEChunk("", ": keep-alive\n\n");
  assert.equal(result.events.length, 0);
  assert.equal(result.rest, "");
});

test("注释帧夹在事件之间不影响分帧", () => {
  const result = parseSSEChunk("", ": ping\ndata: a\n\n: ping2\ndata: b\n\n");
  assert.equal(result.events.length, 2);
  assert.equal(result.events[0].data, "a");
  assert.equal(result.events[1].data, "b");
});

test("只有 id 没有 data 的帧被丢弃", () => {
  const result = parseSSEChunk("", "id: 3\n\n");
  assert.equal(result.events.length, 0);
  assert.equal(result.rest, "");
});

test("末尾不完整帧留在 rest，下一块补齐后解析", () => {
  const first = parseSSEChunk("", 'data: {"partial":');
  assert.equal(first.events.length, 0);
  assert.equal(first.rest, 'data: {"partial":');

  const second = parseSSEChunk(first.rest, "true}\n\n");
  assert.equal(second.events.length, 1);
  assert.equal(second.events[0].data, '{"partial":true}');
  assert.equal(second.rest, "");
});

test("一个块里多个完整帧 + 一个残缺帧", () => {
  const result = parseSSEChunk("", "data: a\n\ndata: b\n\ndata: c");
  assert.equal(result.events.length, 2);
  assert.deepEqual(result.events.map((e) => e.data), ["a", "b"]);
  assert.equal(result.rest, "data: c");
});

test("chunk 为空串时解析 buffer 中已完整的帧", () => {
  const result = parseSSEChunk("data: a\n\n", "");
  assert.equal(result.events.length, 1);
  assert.equal(result.rest, "");
});

test("UTF-8 代理对跨块不切割（😀 拆分到两个 chunk）", () => {
  const frame = "data: 😀\n\n";
  // 在 UTF-16 代理对中间切开（😀 = U+1F600 = \uD83D\uDE00）
  const cut = frame.indexOf("😀") + 1;
  const a = parseSSEChunk("", frame.slice(0, cut));
  assert.equal(a.events.length, 0);
  const b = parseSSEChunk(a.rest, frame.slice(cut));
  assert.equal(b.events.length, 1);
  assert.equal(b.events[0].data, "😀");
});

test("中文跨块拼接不丢失", () => {
  const a = parseSSEChunk("", "data: 你好");
  const b = parseSSEChunk(a.rest, "，世界\n\n");
  assert.equal(b.events.length, 1);
  assert.equal(b.events[0].data, "你好，世界");
});

test("多个空格只剥离一个前导空格", () => {
  const result = parseSSEChunk("", "data:  two spaces\n\n");
  assert.equal(result.events[0].data, " two spaces");
});

test("data: 后无空格也能解析", () => {
  const result = parseSSEChunk("", "data:x\n\n");
  assert.equal(result.events[0].data, "x");
});

// --------------------------------------------------------------------------- //
// eventKey / 去重键
// --------------------------------------------------------------------------- //

test("eventKey 为 run_id:event_seq", () => {
  assert.equal(eventKey({ run_id: "run-1", event_seq: 3 }), "run-1:3");
});

// --------------------------------------------------------------------------- //
// openRunEvents：流式订阅 / 去重 / 终结 once guard / abort
// --------------------------------------------------------------------------- //

function frameFor(seq, type, extra) {
  const payload = {
    schema_version: 1,
    session_id: "s1",
    run_id: "r1",
    event_seq: seq,
    type,
    occurred_at: "2026-01-01T00:00:00Z",
    payload: extra || {},
  };
  return `id: ${seq}\nevent: ${type}\ndata: ${JSON.stringify(payload)}\n\n`;
}

function streamResponse(chunks) {
  const body = new ReadableStream({
    start(controller) {
      const encoder = new TextEncoder();
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      controller.close();
    },
  });
  return { ok: true, status: 200, body, json: async () => ({}), text: async () => "" };
}

function collect(url, opts) {
  return new Promise((resolve) => {
    const events = [];
    const errors = [];
    const closes = [];
    openRunEvents(url, {
      ...opts,
      onEvent: (event) => events.push(event),
      onError: (error) => errors.push(error),
      onClose: (reason) => {
        closes.push(reason);
        resolve({ events, errors, closes });
      },
    });
  });
}

test("openRunEvents 收到事件并在终结事件时关闭 terminal（只回调一次）", async () => {
  const chunk = frameFor(1, "run.started") + frameFor(2, "step.started") + frameFor(3, "run.completed");
  const result = await collect("/api/v2/runs/r1/events", {
    afterSeq: 0,
    fetchImpl: async () => streamResponse([chunk]),
  });

  assert.equal(result.events.length, 3);
  assert.deepEqual(result.events.map((e) => e.event_seq), [1, 2, 3]);
  assert.deepEqual(result.closes, ["terminal"]);
  assert.equal(result.errors.length, 0);
});

test("openRunEvents 按 run_id+event_seq 去重（重复帧只回调一次）", async () => {
  const chunk = frameFor(1, "run.started") + frameFor(1, "run.started") + frameFor(2, "run.started");
  const result = await collect("/api/v2/runs/r1/events", {
    fetchImpl: async () => streamResponse([chunk]),
  });
  assert.deepEqual(result.events.map((e) => e.event_seq), [1, 2]);
});

test("openRunEvents 终结事件之后不再处理后续事件", async () => {
  const chunk = frameFor(1, "run.started") + frameFor(9, "run.completed") + frameFor(10, "product_state.updated");
  const result = await collect("/api/v2/runs/r1/events", {
    fetchImpl: async () => streamResponse([chunk]),
  });
  assert.deepEqual(result.events.map((e) => e.event_seq), [1, 9]);
  assert.deepEqual(result.closes, ["terminal"]);
});

test("openRunEvents 在 EOF 且无终结事件时关闭为 eof", async () => {
  const result = await collect("/api/v2/runs/r1/events", {
    fetchImpl: async () => streamResponse([frameFor(1, "run.started")]),
  });
  assert.deepEqual(result.closes, ["eof"]);
});

test("openRunEvents 非 2xx 走 onError 并关闭 eof", async () => {
  const result = await collect("/api/v2/runs/r1/events", {
    fetchImpl: async () => ({
      ok: false,
      status: 409,
      body: null,
      json: async () => ({
        error: { code: "VERSION_CONFLICT", message: "conflict", retryable: false, request_id: "req-1" },
      }),
      text: async () => "",
    }),
  });
  assert.equal(result.errors.length, 1);
  assert.equal(result.errors[0].code, "VERSION_CONFLICT");
  assert.equal(result.errors[0].status, 409);
  assert.deepEqual(result.closes, ["eof"]);
});

test("openRunEvents 协议错误（坏 JSON）上报但继续读取后续帧", async () => {
  const bad = "id: 1\nevent: run.started\ndata: not-json\n\n";
  const good = frameFor(2, "run.completed");
  const result = await collect("/api/v2/runs/r1/events", {
    fetchImpl: async () => streamResponse([bad + good]),
  });
  assert.equal(result.errors.length, 1);
  assert.deepEqual(result.events.map((e) => e.event_seq), [2]);
  assert.deepEqual(result.closes, ["terminal"]);
});

test("openRunEvents 外部 signal 已 abort 时直接关闭为 abort", async () => {
  const controller = new AbortController();
  controller.abort();
  let closeReason = null;
  openRunEvents("/api/v2/runs/r1/events", {
    signal: controller.signal,
    onEvent: () => {},
    onError: () => {},
    onClose: (reason) => {
      closeReason = reason;
    },
    fetchImpl: async () => {
      throw new Error("should not be called");
    },
  });
  assert.equal(closeReason, "abort");
});

test("openRunEvents 返回的 cancel() 静默停止（不回调 onClose）", async () => {
  let onCloseCalled = false;
  const controller = new AbortController();
  const cancel = openRunEvents("/api/v2/runs/r1/events", {
    signal: controller.signal,
    onEvent: () => {},
    onError: () => {},
    onClose: () => {
      onCloseCalled = true;
    },
    fetchImpl: async () => {
      throw new Error("noop");
    },
  });
  cancel();
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(onCloseCalled, false);
});

test("openRunEvents 传递 Last-Event-ID 与 after_seq", async () => {
  let capturedUrl = null;
  let capturedInit = null;
  await collect("/api/v2/runs/r1/events", {
    afterSeq: 12,
    fetchImpl: async (url, init) => {
      capturedUrl = url;
      capturedInit = init;
      return streamResponse([frameFor(13, "run.completed")]);
    },
  });
  assert.match(String(capturedUrl), /after_seq=12/);
  assert.equal(capturedInit.headers["Last-Event-ID"], "12");
  assert.equal(capturedInit.method, "GET");
});

// --------------------------------------------------------------------------- //
// 与后端真实帧格式对齐（backend/api/sse.py 的 format_event/format_keepalive 输出）
// --------------------------------------------------------------------------- //

// 该字符串由 python 直接调用 backend/api/sse.py:format_event() +
// format_keepalive() 生成，确保前端解析器与后端序列化格式严格兼容。
const BACKEND_FRAMES =
  ": keepalive\n\n" +
  'id: 1\nevent: run.started\ndata: {"event_seq":1,"occurred_at":"2026-01-01T00:00:00Z","payload":{},"run_id":"r1","schema_version":1,"session_id":"s1","type":"run.started"}\n\n' +
  'id: 2\nevent: product_state.updated\ndata: {"event_seq":2,"occurred_at":"2026-01-01T00:00:00Z","payload":{"product_version":2,"session_version":3},"run_id":"r1","schema_version":1,"session_id":"s1","type":"product_state.updated"}\n\n' +
  'id: 3\nevent: run.completed\ndata: {"event_seq":3,"occurred_at":"2026-01-01T00:00:00Z","payload":{"product_version":2,"session_version":3,"status":"succeeded"},"run_id":"r1","schema_version":1,"session_id":"s1","type":"run.completed"}\n\n';

test("能解析后端真实 SSE 帧（含心跳注释与 product_state.updated）", () => {
  const result = parseSSEChunk("", BACKEND_FRAMES);
  assert.equal(result.rest, "");
  assert.equal(result.events.length, 3);
  assert.deepEqual(
    result.events.map((e) => e.id),
    ["1", "2", "3"],
  );
  assert.deepEqual(
    result.events.map((e) => e.event),
    ["run.started", "product_state.updated", "run.completed"],
  );
  const last = JSON.parse(result.events[2].data);
  assert.equal(last.type, "run.completed");
  assert.equal(last.payload.product_version, 2);
});

test("后端真实帧逐字节分块喂入也能完整重组（模拟 TCP 分片）", async () => {
  // 按 1 字符切片，最严苛的分片情形
  const chunks = [];
  for (let i = 0; i < BACKEND_FRAMES.length; i += 1) chunks.push(BACKEND_FRAMES[i]);

  let buffer = "";
  const events = [];
  for (const chunk of chunks) {
    const parsed = parseSSEChunk(buffer, chunk);
    buffer = parsed.rest;
    events.push(...parsed.events);
  }
  assert.equal(events.length, 3);
  assert.deepEqual(
    events.map((e) => e.event),
    ["run.started", "product_state.updated", "run.completed"],
  );
  assert.equal(buffer, "");
});

test("openRunEvents 端到端消费后端真实帧并在 run.completed 关闭 terminal", async () => {
  const result = await collect("/api/v2/runs/r1/events", {
    fetchImpl: async () =>
      streamResponse([
        BACKEND_FRAMES.slice(0, 60),
        BACKEND_FRAMES.slice(60, 200),
        BACKEND_FRAMES.slice(200),
      ]),
  });
  assert.deepEqual(result.closes, ["terminal"]);
  assert.equal(result.errors.length, 0);
  assert.deepEqual(
    result.events.map((e) => e.type),
    ["run.started", "product_state.updated", "run.completed"],
  );
});
