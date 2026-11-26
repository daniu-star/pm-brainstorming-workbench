/**
 * `LlmConfigGate` 渲染回归（第 42 批线上复现的误导横幅）。
 *
 * 线上真机事实：匿名从 /ops 被重定向到 /login 后，登录页顶部挂着
 * 「模型配置读取失败（网络不稳），功能可能不可用」——因为 /ops 那次 401 的
 * 失败状态在路由切换后仍留在组件里，而旧渲染逻辑对白名单页面也走 bypass 横幅分支。
 *
 * 这里用 react-dom/server（本机装不了 jsdom/RTL）断言两条渲染分支：
 * - 白名单路径（/login、/product、/settings/llm）：直接渲染 children，不出现任何闸门文案；
 * - 其它路径首次渲染：显示「正在检查模型配置…」骨架。
 */
"use strict";

const path = require("node:path");
const Module = require("node:module");
const test = require("node:test");
const assert = require("node:assert/strict");

const BUILD_ROOT = path.join(process.cwd(), "node_modules", ".cache", "render-build");

let currentPath = "/login";

const originalResolve = Module._resolveFilename;
Module._resolveFilename = function (request, parent, ...rest) {
  if (typeof request === "string" && request.startsWith("@/")) {
    request = path.join(BUILD_ROOT, request.slice(2));
  }
  return originalResolve.call(this, request, parent, ...rest);
};

const React = require("react");
const originalLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (request === "next/navigation") {
    return { usePathname: () => currentPath };
  }
  if (request === "next/link") {
    return function Link(props) {
      const { href, children, ...rest } = props;
      return React.createElement("a", { href: typeof href === "string" ? href : "#", ...rest }, children);
    };
  }
  return originalLoad.call(this, request, parent, isMain);
};

const { renderToStaticMarkup } = require("react-dom/server");
const { LlmConfigGate } = require(path.join(BUILD_ROOT, "components", "llm", "LlmConfigGate.js"));

function renderGate() {
  return renderToStaticMarkup(
    React.createElement(LlmConfigGate, null, React.createElement("main", null, "GATE_CHILDREN"))
  );
}

test("白名单页面直接渲染 children，不挂「模型配置读取失败」横幅", () => {
  for (const p of ["/login", "/product", "/settings/llm"]) {
    currentPath = p;
    const html = renderGate();
    assert.match(html, /GATE_CHILDREN/, p);
    assert.doesNotMatch(html, /模型配置读取失败/, p);
    assert.doesNotMatch(html, /正在检查模型配置/, p);
  }
});

test("业务页面首次渲染显示检查中骨架", () => {
  currentPath = "/session/abc";
  const html = renderGate();
  assert.match(html, /正在检查模型配置/);
  assert.doesNotMatch(html, /GATE_CHILDREN/);
});
