"use strict";

/**
 * Store 纯逻辑守门员（frontend/lib/stateGuards.ts）的单元测试。
 *
 * 覆盖 V4-M1 中三条硬约束：
 *  1. 消息按 message_id 去重（同 id 替换、不追加重复气泡）
 *  2. product_version 单调递增（旧版本不能覆盖新版本）
 *  3. generation / session_id 守卫（快速切会话时丢弃过期异步写回）
 *
 * 被测代码由 `tsc -p tsconfig.test.json` 编译到 `node_modules/.cache/test-build/lib/stateGuards.js`。
 */

const { test } = require("node:test");
const assert = require("node:assert/strict");

const {
  isGuardCurrent,
  mergeMessagesById,
  upsertMessageById,
  acceptProductVersion,
  shouldRefetchCanvas,
  eventDedupeKey,
  isNewEvent,
  createOnceGuard,
} = require("../node_modules/.cache/test-build/lib/stateGuards.js");

// --------------------------------------------------------------------------- //
// generation / session_id 守卫
// --------------------------------------------------------------------------- //

test("相同 session 且相同 generation 时守卫通过", () => {
  assert.equal(isGuardCurrent({ sessionId: "s1", generation: 3 }, { sessionId: "s1", generation: 3 }), true);
});

test("session 切换后旧请求的写回被丢弃", () => {
  assert.equal(isGuardCurrent({ sessionId: "s2", generation: 4 }, { sessionId: "s1", generation: 3 }), false);
});

test("generation 递增后旧请求的写回被丢弃（同会话重载）", () => {
  assert.equal(isGuardCurrent({ sessionId: "s1", generation: 5 }, { sessionId: "s1", generation: 4 }), false);
});

test("登出后 currentSessionId 为 null 时任何写回都被丢弃", () => {
  assert.equal(isGuardCurrent({ sessionId: null, generation: 9 }, { sessionId: "s1", generation: 8 }), false);
});

// --------------------------------------------------------------------------- //
// 消息按 message_id 去重
// --------------------------------------------------------------------------- //

test("mergeMessagesById 新 id 追加、已有 id 原位替换", () => {
  const existing = [{ id: "m1", content: "旧" }, { id: "m2", content: "二" }];
  const incoming = [{ id: "m2", content: "二-更新" }, { id: "m3", content: "三" }];
  const result = mergeMessagesById(existing, incoming);

  assert.deepEqual(result.items.map((m) => m.id), ["m1", "m2", "m3"]);
  assert.equal(result.items[1].content, "二-更新");
  assert.equal(result.added, 1);
  assert.equal(result.replaced, 1);
});

test("mergeMessagesById 重复推同一条消息不会产生重复气泡", () => {
  const existing = [{ id: "m1", content: "a" }];
  const result = mergeMessagesById(existing, [{ id: "m1", content: "a" }]);
  assert.equal(result.items.length, 1);
  assert.equal(result.added, 0);
  assert.equal(result.replaced, 1);
});

test("mergeMessagesById 全部带 seq 时按 seq 排序（乱序到达也能归位）", () => {
  const existing = [{ id: "m2", seq: 2, content: "b" }];
  const incoming = [
    { id: "m3", seq: 3, content: "c" },
    { id: "m1", seq: 1, content: "a" },
  ];
  const result = mergeMessagesById(existing, incoming);
  assert.deepEqual(result.items.map((m) => m.id), ["m1", "m2", "m3"]);
});

test("mergeMessagesById 部分消息缺 seq 时保持插入顺序", () => {
  const existing = [{ id: "m1", seq: 1, content: "a" }];
  const incoming = [{ id: "local-1", content: "乐观消息" }];
  const result = mergeMessagesById(existing, incoming);
  assert.deepEqual(result.items.map((m) => m.id), ["m1", "local-1"]);
});

test("upsertMessageById 更新流式草稿时替换而不是追加", () => {
  const existing = [{ id: "m1", seq: 1, content: "半句" }];
  const first = upsertMessageById(existing, { id: "m1", seq: 1, content: "半句" });
  assert.equal(first.added, false);
  assert.equal(first.items.length, 1);

  const second = upsertMessageById(first.items, { id: "m1", seq: 1, content: "半句，完整了" });
  assert.equal(second.added, false);
  assert.equal(second.items.length, 1);
  assert.equal(second.items[0].content, "半句，完整了");
});

// --------------------------------------------------------------------------- //
// product_version 单调
// --------------------------------------------------------------------------- //

test("acceptProductVersion 接受更高版本", () => {
  const result = acceptProductVersion(3, 4);
  assert.equal(result.accepted, true);
  assert.equal(result.version, 4);
  assert.equal(result.stale, false);
  assert.equal(result.warning, null);
});

test("acceptProductVersion 幂等接受相同版本（不重复重建画布）", () => {
  const result = acceptProductVersion(4, 4);
  assert.equal(result.accepted, true);
  assert.equal(result.version, 4);
  assert.equal(result.warning, null);
});

test("acceptProductVersion 丢弃旧版本并给出中文 warning", () => {
  const result = acceptProductVersion(5, 3);
  assert.equal(result.accepted, false);
  assert.equal(result.version, 5);
  assert.equal(result.stale, true);
  assert.match(result.warning, /丢弃旧版本 product_version=3（当前 5）/);
});

test("acceptProductVersion 拒绝非法版本（<1）", () => {
  const result = acceptProductVersion(5, 0);
  assert.equal(result.accepted, false);
  assert.equal(result.version, 5);
  assert.equal(result.stale, true);
  assert.match(result.warning, /忽略非法 product_version=0/);
});

test("shouldRefetchCanvas 仅在新版本严格更大时重取", () => {
  assert.equal(shouldRefetchCanvas(null, 1), true);
  assert.equal(shouldRefetchCanvas(undefined, 2), true);
  assert.equal(shouldRefetchCanvas(2, 3), true);
  assert.equal(shouldRefetchCanvas(3, 3), false);
  assert.equal(shouldRefetchCanvas(3, 2), false);
  assert.equal(shouldRefetchCanvas(3, 0), false);
});

// --------------------------------------------------------------------------- //
// 事件去重（run_id + event_seq）
// --------------------------------------------------------------------------- //

test("eventDedupeKey 由 run_id 与 event_seq 组成", () => {
  assert.equal(eventDedupeKey("run-a", 1), "run-a:1");
  assert.notEqual(eventDedupeKey("run-a", 1), eventDedupeKey("run-b", 1));
});

test("isNewEvent 首次为真、重复为假（跨 Run 同序号互不干扰）", () => {
  const seen = new Set();
  assert.equal(isNewEvent(seen, "run-a", 1), true);
  assert.equal(isNewEvent(seen, "run-a", 1), false);
  assert.equal(isNewEvent(seen, "run-b", 1), true);
  assert.equal(isNewEvent(seen, "run-a", 2), true);
  assert.equal(seen.size, 3);
});

// --------------------------------------------------------------------------- //
// once guard（终结事件只处理一次）
// --------------------------------------------------------------------------- //

test("createOnceGuard 只放行第一次", () => {
  const once = createOnceGuard();
  assert.equal(once(), true);
  assert.equal(once(), false);
  assert.equal(once(), false);
});
