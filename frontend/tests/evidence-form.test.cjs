/**
 * 「补充来源」表单（第 29 批）的服务端渲染测试。
 *
 * 表单是受控组件，交互要靠浏览器；这里只验证**首屏结构**：该提示的数字、
 * 服务端建议、四个字段的无障碍标签、两个按钮都在，避免模板改坏却测试仍绿。
 */
"use strict";

const path = require("node:path");
const Module = require("node:module");
const test = require("node:test");
const assert = require("node:assert/strict");

const BUILD_ROOT = path.join(process.cwd(), "node_modules", ".cache", "render-build");

const originalResolve = Module._resolveFilename;
Module._resolveFilename = function (request, parent, ...rest) {
  if (typeof request === "string" && request.startsWith("@/")) {
    request = path.join(BUILD_ROOT, request.slice(2));
  }
  return originalResolve.call(this, request, parent, ...rest);
};

const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const { EvidenceForm } = require(path.join(BUILD_ROOT, "components", "evidence", "EvidenceForm.js"));

function render(props) {
  return renderToStaticMarkup(React.createElement(EvidenceForm, props));
}

test("表单展示要补来源的数字与服务端建议", () => {
  const html = render({
    numbers: ["2", "66"],
    suggestion: "未验证数字：2、66。请补充这些数字的来源与统计日期。",
    onCancel() {},
    onSubmit() {},
  });
  assert.match(html, /补充来源/);
  assert.match(html, /2、66/);
  assert.match(html, /统计日期/);
});

test("四个字段都有无障碍标签，摘录是 textarea", () => {
  const html = render({ numbers: [], onCancel() {}, onSubmit() {} });
  assert.match(html, /aria-label="证据标题"/);
  assert.match(html, /aria-label="证据摘录"/);
  assert.match(html, /<textarea[^>]*aria-label="证据摘录"/);
  assert.match(html, /aria-label="统计日期"/);
  assert.match(html, /aria-label="来源链接"/);
});

test("按钮文案与 busy 态", () => {
  const idle = render({ numbers: ["2"], onCancel() {}, onSubmit() {} });
  assert.match(idle, /保存证据/);
  assert.match(idle, /取消/);

  const busy = render({ numbers: ["2"], busy: true, onCancel() {}, onSubmit() {} });
  assert.match(busy, /保存中…/);
  assert.match(busy, /disabled/);
});
