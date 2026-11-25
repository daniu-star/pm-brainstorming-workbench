const test = require("node:test");
const assert = require("node:assert/strict");
const Module = require("node:module");
const path = require("node:path");

const build = path.join(__dirname, "..", "node_modules", ".cache", "test-build");

// 编译产物保留了 `@/` 别名，这里按测试构建目录解析（与 llm-config.test.cjs 同一套做法）。
const originalResolve = Module._resolveFilename;
Module._resolveFilename = function (request, ...rest) {
  if (request.startsWith("@/")) {
    return originalResolve.call(this, path.join(build, request.slice(2)), ...rest);
  }
  return originalResolve.call(this, request, ...rest);
};

const { formatTokens, quotaSummary, resetInText, QUOTA_WARNING_PERCENT } = require(
  path.join(build, "lib", "quota.js")
);

const view = (overrides) => ({
  enabled: true,
  window_seconds: 86400,
  window_hours: 24,
  reset_at: "2026-11-26T03:00:00Z",
  token_limit: 30000,
  call_limit: 10,
  used_tokens: 0,
  reserved_tokens: 0,
  total_tokens: 0,
  used_calls: 0,
  reserved_calls: 0,
  total_calls: 0,
  scanned_runs: 3,
  exceeded: false,
  ...overrides,
});

test("未设上限时显示「未设上限」且不算百分比", () => {
  const summary = quotaSummary(view({ enabled: false, token_limit: 0, call_limit: 0, total_tokens: 1234 }));
  assert.equal(summary.kind, "unlimited");
  assert.equal(summary.label, "未设上限");
  assert.equal(summary.percent, 0);
  assert.match(summary.detail, /1\.2k tokens/);
});

test("按 token 与调用次数取较高百分比，并给出用量明细", () => {
  const summary = quotaSummary(view({ total_tokens: 12600, total_calls: 2 }));
  assert.equal(summary.tokenPercent, 42);
  assert.equal(summary.callPercent, 20);
  assert.equal(summary.percent, 42);
  assert.equal(summary.kind, "ok");
  assert.equal(summary.label, "额度 42%");
  assert.match(summary.detail, /12\.6k \/ 30k tokens/);
  assert.match(summary.detail, /调用 2 \/ 10 次/);
});

test("达到警戒线（80%）标成 warning", () => {
  const summary = quotaSummary(view({ total_tokens: 24000 }));
  assert.equal(summary.percent, 80);
  assert.equal(summary.kind, "warning");
  assert.equal(QUOTA_WARNING_PERCENT, 80);
});

test("超额时固定 100% 且标签为「额度已用尽」", () => {
  const summary = quotaSummary(view({ total_tokens: 40000, exceeded: true }));
  assert.equal(summary.kind, "exceeded");
  assert.equal(summary.percent, 100);
  assert.equal(summary.label, "额度已用尽");
});

test("formatTokens 在千/百万处进位", () => {
  assert.equal(formatTokens(0), "0");
  assert.equal(formatTokens(999), "999");
  assert.equal(formatTokens(1000), "1k");
  assert.equal(formatTokens(12345), "12.3k");
  assert.equal(formatTokens(200000), "200k");
  assert.equal(formatTokens(1500000), "1.5m");
});

test("resetInText 给出恢复倒计时，过去或非法时间有兜底", () => {
  const now = Date.parse("2026-11-26T00:48:00Z");
  assert.equal(resetInText("2026-11-26T03:00:00Z", now), "约 2 小时 12 分钟后恢复");
  assert.equal(resetInText("2026-11-26T00:50:00Z", now), "约 2 分钟后恢复");
  assert.equal(resetInText("2026-11-26T00:00:00Z", now), "即将恢复");
  assert.equal(resetInText("2026-11-26T00:59:59Z", now), "约 12 分钟后恢复");
  assert.equal(resetInText(null, now), null);
  assert.equal(resetInText("not-a-date", now), null);
});
