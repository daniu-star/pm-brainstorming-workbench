/**
 * `/ops` 运维健康页（第 38 批）的两层测试。
 *
 * 1. `lib/ops.ts` 的展示口径纯函数；
 * 2. `/ops` 页首屏结构（读取中文案、卡片标题、排查清单）。
 *
 * 真实健康数据由 `backend/tests/test_ops_health.py` 覆盖。
 */
"use strict";

const path = require("node:path");
const Module = require("node:module");
const test = require("node:test");
const assert = require("node:assert/strict");

const BUILD_ROOT = path.join(process.cwd(), "node_modules", ".cache", "render-build");
const TEST_ROOT = path.join(process.cwd(), "node_modules", ".cache", "test-build");

const originalResolve = Module._resolveFilename;
Module._resolveFilename = function (request, parent, ...rest) {
  if (typeof request === "string" && request.startsWith("@/")) {
    request = path.join(BUILD_ROOT, request.slice(2));
  }
  return originalResolve.call(this, request, parent, ...rest);
};

const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const ops = require(path.join(TEST_ROOT, "lib", "ops.js"));
const OpsPage = require(path.join(BUILD_ROOT, "app", "ops", "page.js")).default;

test("statusTone 把整体状态映射成色调", () => {
  assert.equal(ops.statusTone("ok"), "emerald");
  assert.equal(ops.statusTone("degraded"), "amber");
  assert.equal(ops.statusTone("error"), "red");
  assert.equal(ops.statusTone(undefined), "zinc");
  assert.equal(ops.STATUS_LABELS.degraded, "降级");
});

test("storageRows 输出库大小与各表行数", () => {
  const rows = ops.storageRows({
    db: true,
    db_bytes: 2048,
    counts: { sessions: 3, messages: 1200, runs: 7 },
  });
  const map = Object.fromEntries(rows.map((row) => [row.key, row.value]));
  assert.equal(map.db_bytes, "2.0 KB");
  assert.equal(map.sessions, "3");
  assert.equal(map.messages, "1,200");
  assert.equal(map.runs, "7");
  assert.deepEqual(ops.storageRows(null), []);
});

test("queueRows 无排队时最久排队显示「无」", () => {
  const rows = ops.queueRows({ queued: 2, running: 1, oldest_queued_age_seconds: 130 });
  const map = Object.fromEntries(rows.map((row) => [row.key, row.value]));
  assert.equal(map.queued, "2");
  assert.equal(map.running, "1");
  assert.equal(map.oldest, "2 分 10 秒");
  const idle = ops.queueRows({ queued: 0, running: 0, oldest_queued_age_seconds: null });
  assert.equal(idle.find((row) => row.key === "oldest").value, "无");
});

test("formatSeconds 覆盖秒/分/小时", () => {
  assert.equal(ops.formatSeconds(0), "0 秒");
  assert.equal(ops.formatSeconds(59.4), "59 秒");
  assert.equal(ops.formatSeconds(120), "2 分钟");
  assert.equal(ops.formatSeconds(3725), "1 小时 2 分");
  assert.equal(ops.formatSeconds(null), "—");
});

test("workerSummary 分辨 stalled / 排队 / 空闲", () => {
  assert.equal(
    ops.workerSummary({ db: true, running_runs: 0, queued_runs: 3, heartbeat_age_seconds: 900, stalled: true, hint: "启动 Worker" }).tone,
    "red",
  );
  assert.match(
    ops.workerSummary({ db: true, running_runs: 1, queued_runs: 2, heartbeat_age_seconds: 5, stalled: false, hint: null }).text,
    /2 个任务排队中/,
  );
  assert.equal(
    ops.workerSummary({ db: true, running_runs: 0, queued_runs: 0, heartbeat_age_seconds: 12, stalled: false, hint: null }).tone,
    "emerald",
  );
  assert.equal(ops.workerSummary(null).tone, "zinc");
});

test("reportQualitySummary 与 sandboxSummary 给出可操作摘要", () => {
  assert.equal(
    ops.reportQualitySummary({ db: true, window: "7d", verdict: "frequent", hint: null }).tone,
    "red",
  );
  assert.match(
    ops.reportQualitySummary({ db: true, window: "1d", verdict: "clean", hint: null }).text,
    /没有未验证数字/,
  );
  assert.match(ops.sandboxSummary({ enabled: false, allowed_commands: [], timeout_seconds: 20, max_output_bytes: 8192, note: "" }).text, /fail-closed/);
  assert.equal(
    ops.sandboxSummary({ enabled: true, allowed_commands: ["python"], timeout_seconds: 20, max_output_bytes: 8192, note: "" }).tone,
    "emerald",
  );
});

test("llmSummary 与 budgetSummary 不含密钥", () => {
  const rows = ops.llmSummary({
    model: "deepseek-flash",
    base_url_host: "api.deepseek.com",
    disable_reasoning: true,
    timeout_seconds: 60,
    max_retries: 2,
  });
  const map = Object.fromEntries(rows.map((row) => [row.key, row.value]));
  assert.equal(map.model, "deepseek-flash");
  assert.equal(map.reasoning, "是");
  assert.equal(map.retries, "2");
  assert.match(ops.budgetSummary("lean", { max_upstream_calls: 6, max_output_tokens: 900 }), /lean · 上游调用 6 次 · 输出 900 tokens/);
  assert.equal(ops.budgetSummary(undefined, undefined), "standard（契约默认）");
});

test("/ops 首屏有标题、读取中状态与排查清单", () => {
  const html = renderToStaticMarkup(React.createElement(OpsPage));
  assert.match(html, /运维健康/);
  assert.match(html, /读取中/);
  assert.match(html, /GET \/health\/detail|health\/detail/);
  for (const title of ["Worker", "队列", "存储", "沙箱", "模型", "报告质量"]) {
    assert.match(html, new RegExp(title));
  }
  assert.match(html, /排查顺序/);
  assert.match(html, /刷新/);
});
