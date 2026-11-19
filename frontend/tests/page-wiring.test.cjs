/**
 * 页面接线回归测试（DX-R3-03 / DX-R3-01 / PRD-07 / PRD-08）。
 *
 * 为什么读源码而不是渲染组件：本机 npm registry 不可达，无法安装
 * @testing-library / jsdom；而 tsconfig.test.json 只编译 lib/*.ts 与
 * store/sessionStore.ts，页面 .tsx 不进入测试构建。结果是「纯函数已修好、
 * 页面却忘了接线」这类缺陷（DX-R3-03 曾如此）在单测里全绿却线上复发。
 *
 * 这里只断言「页面把哪个值传给了哪个函数」这种稳定契约，不做快照、
 * 不校验文案措辞；页面重构格式时应同步调整正则而不是删用例。
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const APP_DIR = path.join(__dirname, "..", "app", "session", "[id]");

function readPage(name) {
  return fs.readFileSync(path.join(APP_DIR, name, "page.tsx"), "utf8");
}

test("loop 页：sessionLoaded 只表示已拿到会话，读取失败走 sessionLoadFailed", () => {
  const source = readPage("loop");
  assert.match(
    source,
    /sessionLoaded:\s*Boolean\(sessionView\)/,
    "sessionLoaded 必须是 Boolean(sessionView)，不能用 sessionSettled 冒充"
  );
  assert.match(
    source,
    /sessionLoadFailed:\s*sessionSettled\s*&&\s*!sessionView\s*&&\s*!sessionLoading/,
    "读取失败必须是「已结算 + 无 sessionView + 不在加载中」"
  );
  assert.match(
    source,
    /entry\.showLoadError/,
    "读取失败分支必须被渲染（否则永远只显示阶段门文案）"
  );
});

test("loop 页：失败轮次接上既有 retryFailedRun，而不是只能开新闭环", () => {
  const source = readPage("loop");
  assert.match(
    source,
    /retryFailedRun\(currentLoop\.latest_run_id\)/,
    "失败态必须用 latest_run_id 调 retryFailedRun（PRD-07）"
  );
});

test("audit 页：失败审计渲染原因与恢复入口", () => {
  const source = readPage("audit");
  assert.match(
    source,
    /resolveAuditFailureRecoveryState\(\{/,
    "必须用 resolveAuditFailureRecoveryState 计算失败原因/恢复入口（DX-R3-01）"
  );
  assert.match(source, /auditFailure\.isFailed/, "失败态必须渲染恢复块");
  assert.match(source, /auditFailure\.reasonText/, "恢复块必须展示具体失败原因");
  assert.match(source, /auditFailure\.hintText/, "恢复块必须给出下一步指引");
  assert.match(source, /失败原因：/, "失败态必须展示具体失败原因");
});

test("audit 页：结束审计前有二次确认，恢复路径复用 retryFailedRun", () => {
  const source = readPage("audit");
  assert.match(source, /confirmEnd/, "结束审计必须有二段确认（PRD-08）");
  assert.match(source, /确认结束并生成报告/, "确认按钮文案存在");
  assert.match(source, /retryFailedRun\(/, "恢复链必须真正调用 retryFailedRun");
});
