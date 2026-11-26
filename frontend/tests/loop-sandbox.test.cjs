/**
 * Loop Studio 执行沙箱（第 38 批前端接线）的两层测试。
 *
 * 1. `lib/loopSandbox.ts` 的纯函数：argv 解析、预校验、摘要与输出尾部；
 * 2. `SandboxPanel` 的首屏结构：命令输入、超时输入、运行按钮与「读取中」文案。
 *
 * 真进程执行由 `backend/tests/test_loop_sandbox.py` 覆盖，这里只挡住模板改坏。
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
const sandbox = require(path.join(TEST_ROOT, "lib", "loopSandbox.js"));
const { SandboxPanel } = require(path.join(BUILD_ROOT, "components", "loop", "SandboxPanel.js"));

test("parseCommandLine 支持引号与空行", () => {
  assert.deepEqual(sandbox.parseCommandLine(""), []);
  assert.deepEqual(sandbox.parseCommandLine("   "), []);
  assert.deepEqual(sandbox.parseCommandLine("python -c \"print(1 + 1)\""), [
    "python",
    "-c",
    "print(1 + 1)",
  ]);
  assert.deepEqual(sandbox.parseCommandLine("'C:\\Program Files\\node.exe' --version"), [
    "C:\\Program Files\\node.exe",
    "--version",
  ]);
  assert.deepEqual(sandbox.parseCommandLine('echo "hi"'), ["echo", "hi"]);
});

test("formatCommandLine 给含空格的参数补引号", () => {
  assert.equal(sandbox.formatCommandLine([]), "");
  assert.equal(sandbox.formatCommandLine(["node", "--version"]), "node --version");
  assert.equal(sandbox.formatCommandLine(["C:\\Program Files\\node.exe"]), '"C:\\Program Files\\node.exe"');
});

test("checkCommand 挡住空命令、超量与 shell 元字符", () => {
  assert.equal(sandbox.checkCommand([]).ok, false);
  assert.match(sandbox.checkCommand([]).reason, /先输入/);

  const tooMany = Array.from({ length: sandbox.MAX_COMMAND_ARGS + 1 }, (_, index) => `a${index}`);
  assert.match(sandbox.checkCommand(tooMany).reason, /参数太多/);

  const meta = sandbox.checkCommand(["python", "-c", "print(1) && echo 2"]);
  assert.equal(meta.ok, false);
  assert.match(meta.reason, /元字符/);

  assert.match(sandbox.checkCommand(["python", ""]).reason, /空字符串/);
  assert.match(sandbox.checkCommand(["python", "x".repeat(sandbox.MAX_COMMAND_ARG_LEN + 1)]).reason, /太长/);
  assert.equal(sandbox.checkCommand(["python", "-V"]).ok, true);
});

test("sandboxBlockedHint 区分未开启 / 空白名单 / 可用", () => {
  assert.match(sandbox.sandboxBlockedHint(null), /正在读取/);
  assert.match(
    sandbox.sandboxBlockedHint({ enabled: false, allowed_commands: ["python"], timeout_seconds: 20, max_output_bytes: 1024, note: "" }),
    /LOOP_SANDBOX_ENABLED/,
  );
  assert.match(
    sandbox.sandboxBlockedHint({ enabled: true, allowed_commands: [], timeout_seconds: 20, max_output_bytes: 1024, note: "" }),
    /LOOP_SANDBOX_ALLOWED_COMMANDS/,
  );
  assert.equal(
    sandbox.sandboxBlockedHint({ enabled: true, allowed_commands: ["python"], timeout_seconds: 20, max_output_bytes: 1024, note: "" }),
    null,
  );
});

test("sandboxRunSummary 覆盖通过/失败/超时", () => {
  const base = {
    id: "r1",
    loop_id: "l1",
    command_display: "python -V",
    exit_code: 0,
    duration_ms: 12,
    timeout_seconds: 20,
    stdout_tail: "Python 3.12",
    stderr_tail: "",
    truncated: false,
    created_at: "2026-11-26T00:00:00Z",
  };
  assert.equal(sandbox.sandboxRunSummary({ ...base, status: "passed" }), "退出码 0 · 耗时 12ms");
  assert.equal(sandbox.sandboxRunSummary({ ...base, status: "failed", exit_code: 2 }), "退出码 2 · 耗时 12ms");
  assert.match(
    sandbox.sandboxRunSummary({ ...base, status: "timeout", exit_code: null, duration_ms: 20050 }),
    /超过 20 秒被强制结束 · 耗时 20\.1s/,
  );
  assert.equal(sandbox.formatDuration(1500), "1.5s");
  assert.equal(sandbox.formatDuration(65000), "1m5s");
});

test("tailText 超长时保留尾部", () => {
  assert.deepEqual(sandbox.tailText("a\r\nb\n\n"), { text: "a\nb", clipped: false });
  const clipped = sandbox.tailText("x".repeat(50) + "TAIL", 10);
  assert.equal(clipped.clipped, true);
  assert.match(clipped.text, /TAIL$/);
  assert.equal(clipped.text.length, 11);
});

test("allowedCommandsText 空配置给出「未配置」", () => {
  assert.equal(sandbox.allowedCommandsText(null), "未配置");
  assert.equal(
    sandbox.allowedCommandsText({ enabled: true, allowed_commands: ["python", "node"], timeout_seconds: 20, max_output_bytes: 1024, note: "" }),
    "python、node",
  );
});

test("SandboxPanel 首屏有命令/超时输入与运行按钮", () => {
  const html = renderToStaticMarkup(React.createElement(SandboxPanel, { loopId: "loop-1" }));
  assert.match(html, /aria-label="执行沙箱"/);
  assert.match(html, /aria-label="沙箱命令"/);
  assert.match(html, /aria-label="超时秒数"/);
  assert.match(html, /运行命令/);
  assert.match(html, /正在读取沙箱配置/);
  assert.match(html, /读取中/);
});

test("SandboxPanel 只读闭环给出提示且不出现运行记录空态", () => {
  const html = renderToStaticMarkup(
    React.createElement(SandboxPanel, { loopId: "loop-1", readOnly: true }),
  );
  assert.match(html, /历史闭环只读回看/);
  assert.doesNotMatch(html, /还没有运行记录/);
});
