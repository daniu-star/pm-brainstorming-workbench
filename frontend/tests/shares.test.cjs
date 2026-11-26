/**
 * 会话共享（第 35 批前端接线）的两层测试。
 *
 * 1. `lib/shares.ts` 的纯函数：邮箱归一化、所有者判定、过滤自己；
 * 2. `SharePanel` 的首屏结构：字段无障碍标签、角色提示、空态文案。
 *
 * 有网络的页面交互（真账号、真共享）由 e2e/手测覆盖，这里只挡住「模板改坏仍绿」。
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
const shares = require(path.join(TEST_ROOT, "lib", "shares.js"));
const { SharePanel } = require(path.join(BUILD_ROOT, "components", "share", "SharePanel.js"));

test("normalizeShareEmail 去空白小写，挡住明显非法输入", () => {
  assert.equal(shares.normalizeShareEmail("  Mi@Example.COM "), "mi@example.com");
  assert.equal(shares.normalizeShareEmail(""), null);
  assert.equal(shares.normalizeShareEmail("   "), null);
  assert.equal(shares.normalizeShareEmail("no-at-sign"), null);
  assert.equal(shares.normalizeShareEmail("@example.com"), null);
  assert.equal(shares.normalizeShareEmail("a@"), null);
  assert.equal(shares.normalizeShareEmail("a b@example.com"), null);
});

test("canManageShares 只有所有者本人为真", () => {
  assert.equal(shares.canManageShares("owner@example.com", "owner@example.com"), true);
  assert.equal(shares.canManageShares("Owner@Example.com", " owner@example.com "), true);
  assert.equal(shares.canManageShares("owner@example.com", "other@example.com"), false);
  assert.equal(shares.canManageShares(null, "owner@example.com"), false);
  assert.equal(shares.canManageShares("owner@example.com", null), false);
});

test("withoutSelf 过滤掉自己那一行", () => {
  const items = [
    { email: "a@example.com", role: "viewer", created_at: "2026-11-26T00:00:00Z" },
    { email: "me@example.com", role: "editor", created_at: "2026-11-26T00:00:01Z" },
  ];
  const filtered = shares.withoutSelf(items, "ME@example.com");
  assert.equal(filtered.length, 1);
  assert.equal(filtered[0].email, "a@example.com");
  assert.equal(shares.withoutSelf(items, null).length, 2);
});

test("角色标签与提示都有中文文案", () => {
  assert.equal(shares.SHARE_ROLE_LABELS.viewer, "只读");
  assert.equal(shares.SHARE_ROLE_LABELS.editor, "可操作");
  assert.match(shares.SHARE_ROLE_HINTS.viewer, /不能提交/);
  assert.match(shares.SHARE_ROLE_HINTS.editor, /额度/);
});

test("SharePanel 首屏有邮箱输入、角色选择与角色说明", () => {
  const html = renderToStaticMarkup(
    React.createElement(SharePanel, {
      sessionId: "s1",
      myEmail: "owner@example.com",
      onClose() {},
    })
  );
  assert.match(html, /aria-label="会话共享"/);
  assert.match(html, /aria-label="共享对象邮箱"/);
  assert.match(html, /aria-label="共享角色"/);
  assert.match(html, /只读/);
  assert.match(html, /可操作/);
  assert.match(html, /关闭共享面板/);
});

test("SharePanel 初始为读取中，不误显示空列表", () => {
  const html = renderToStaticMarkup(
    React.createElement(SharePanel, {
      sessionId: "s1",
      myEmail: "owner@example.com",
      onClose() {},
    })
  );
  assert.match(html, /正在读取共享成员/);
  assert.doesNotMatch(html, /还没有共享给任何人/);
});
