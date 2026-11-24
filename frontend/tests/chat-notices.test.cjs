const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const build = path.join(__dirname, "..", "node_modules", ".cache", "test-build");
const { displayChatError, latestChatFailure, latestPartialBrainstormRun, queuedRunHint } = require(
  path.join(build, "lib", "chatNotices.js")
);

const run = (overrides) => ({
  sessionId: "s1",
  action: "brainstorm",
  status: "failed",
  errorMessage: "模型服务鉴权失败",
  createdAt: "2026-11-20T03:00:00Z",
  ...overrides,
});

test("当前请求错误优先于恢复到的历史失败", () => {
  const value = displayChatError("刚刚失败的原因", { run_1: run() }, "s1");
  assert.equal(value, "刚刚失败的原因");
});

test("刷新后没有内存 error 时用最近一条对话类失败的原因", () => {
  const value = displayChatError(null, { run_1: run() }, "s1");
  assert.equal(value, "模型服务鉴权失败");
});

test("闭环/审计的失败不挤进群聊，仍回退到更早的对话类失败", () => {
  const runs = {
    run_loop: run({ action: "start_loop", createdAt: "2026-11-20T05:00:00Z", errorMessage: "闭环失败" }),
    run_brainstorm: run({ createdAt: "2026-11-20T03:00:00Z" }),
  };
  const failure = latestChatFailure(runs, "s1");
  assert.equal(failure.runId, "run_brainstorm");
  assert.equal(displayChatError(null, runs, "s1"), "模型服务鉴权失败");
});

test("只有非对话类失败时群聊不提示", () => {
  const runs = { run_loop: run({ action: "start_loop" }) };
  assert.equal(latestChatFailure(runs, "s1"), null);
  assert.equal(displayChatError(null, runs, "s1"), null);
});

test("别的会话的失败不算本会话", () => {
  const runs = { run_1: run({ sessionId: "s2" }) };
  assert.equal(displayChatError(null, runs, "s1"), null);
});

test("失败任务缺 errorMessage 时给出默认提示", () => {
  const value = displayChatError(null, { run_1: run({ errorMessage: null }) }, "s1");
  assert.equal(value, "上一次任务执行失败，可重试上一步");
});

const partial = (overrides) => ({
  sessionId: "s1",
  action: "brainstorm",
  status: "partial_succeeded",
  errorMessage: null,
  createdAt: "2026-11-22T03:00:00Z",
  ...overrides,
});

test("部分成功的脑暴任务给出「重试失败专家」入口", () => {
  assert.equal(latestPartialBrainstormRun({ run_1: partial() }, "s1"), "run_1");
});

test("部分成功取最近一条，且忽略失败/成功/非脑暴任务", () => {
  const runs = {
    run_old: partial({ createdAt: "2026-11-22T01:00:00Z" }),
    run_new: partial({ createdAt: "2026-11-22T05:00:00Z" }),
    run_failed: run({ createdAt: "2026-11-22T09:00:00Z" }),
    run_ok: partial({ status: "succeeded", createdAt: "2026-11-22T10:00:00Z" }),
    run_loop: partial({ action: "start_loop", createdAt: "2026-11-22T11:00:00Z" }),
  };
  assert.equal(latestPartialBrainstormRun(runs, "s1"), "run_new");
});

test("别的会话的部分成功与本会话无关", () => {
  assert.equal(latestPartialBrainstormRun({ run_1: partial({ sessionId: "s2" }) }, "s1"), null);
});

test("没有会话或没有部分成功任务时入口不出现", () => {
  assert.equal(latestPartialBrainstormRun({ run_1: partial() }, null), null);
  assert.equal(latestPartialBrainstormRun({}, "s1"), null);
  assert.equal(latestPartialBrainstormRun({ run_1: run() }, "s1"), null);
});

const queued = (overrides) => ({
  sessionId: "s1",
  action: "clarify",
  status: "queued",
  errorMessage: null,
  createdAt: "2026-11-24T10:00:00Z",
  ...overrides,
});

test("排队未超过阈值时不给提示", () => {
  const nowMs = Date.parse("2026-11-24T10:00:10Z");
  assert.equal(queuedRunHint({ run_1: queued() }, "s1", { nowMs }), null);
});

test("排队超过阈值时提示 Worker 可能未启动并给出可执行命令", () => {
  const nowMs = Date.parse("2026-11-24T10:01:30Z");
  const value = queuedRunHint({ run_1: queued() }, "s1", { nowMs });
  assert.ok(value);
  assert.match(value, /任务已排队 90 秒/);
  assert.match(value, /start\.bat/);
  assert.match(value, /python -m workers\.run_worker/);
});

test("多条排队任务按最早那条计时", () => {
  const nowMs = Date.parse("2026-11-24T10:02:00Z");
  const runs = {
    run_new: queued({ createdAt: "2026-11-24T10:01:50Z" }),
    run_old: queued({ createdAt: "2026-11-24T10:00:00Z" }),
  };
  assert.match(queuedRunHint(runs, "s1", { nowMs }), /任务已排队 120 秒/);
});

test("别的会话、非排队状态、缺 createdAt 的 Run 都不触发提示", () => {
  const nowMs = Date.parse("2026-11-24T11:00:00Z");
  assert.equal(queuedRunHint({ run_1: queued({ sessionId: "s2" }) }, "s1", { nowMs }), null);
  assert.equal(queuedRunHint({ run_1: queued({ status: "running" }) }, "s1", { nowMs }), null);
  assert.equal(queuedRunHint({ run_1: queued({ createdAt: undefined }) }, "s1", { nowMs }), null);
  assert.equal(queuedRunHint({}, "s1", { nowMs }), null);
});

test("没有会话时排队提示不出现", () => {
  const nowMs = Date.parse("2026-11-24T11:00:00Z");
  assert.equal(queuedRunHint({ run_1: queued() }, null, { nowMs }), null);
});
