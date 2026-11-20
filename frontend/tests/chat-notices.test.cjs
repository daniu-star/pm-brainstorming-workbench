const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const build = path.join(__dirname, "..", "node_modules", ".cache", "test-build");
const { displayChatError, latestChatFailure } = require(path.join(build, "lib", "chatNotices.js"));

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
