/**
 * 第 39 批「共享可见性与统一权限页」的前端测试。
 *
 * 1. `lib/access.ts` 的纯展示函数：汇总文案、角色徽标、时间格式、错误提示；
 * 2. `app/settings/access/page.tsx` 首屏结构（权限总览三个分区 + 入口链接）；
 * 3. `components/HistoryDrawer.tsx`：共享会话显示「共享给我 · 只读」，且不出现删除按钮
 *    （此前任何条目都显示删除，共享成员点了只会静默 403）。
 *
 * 渲染层仍走 `react-dom/server`：本机 npm registry 不可达，装不了 jsdom/RTL。
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
const originalLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (request === "next/navigation") {
    return {
      useParams: () => ({ id: "a".repeat(32) }),
      useSearchParams: () => new URLSearchParams(),
      useRouter: () => ({
        push() {},
        replace() {},
        refresh() {},
        prefetch() {},
        back() {},
      }),
      usePathname: () => "/settings/access",
      redirect() {},
      notFound() {},
    };
  }
  if (request === "next/link") {
    return function Link(props) {
      const { href, children, ...rest } = props;
      return React.createElement(
        "a",
        { href: typeof href === "string" ? href : "#", ...rest },
        children
      );
    };
  }
  return originalLoad.call(this, request, parent, isMain);
};

const originalUseSyncExternalStore = React.useSyncExternalStore;
React.useSyncExternalStore = (subscribe, getSnapshot) =>
  originalUseSyncExternalStore(subscribe, getSnapshot, getSnapshot);

const { renderToStaticMarkup } = require("react-dom/server");
const access = require(path.join(TEST_ROOT, "lib", "access.js"));
const AccessSettingsPage = require(
  path.join(BUILD_ROOT, "app", "settings", "access", "page.js")
).default;
const { HistoryDrawer } = require(path.join(BUILD_ROOT, "components", "HistoryDrawer.js"));
const { useSessionStore } = require(path.join(BUILD_ROOT, "store", "sessionStore.js"));

// -- 纯函数 ---------------------------------------------------------------

test("sharesSummary 区分空共享与只读/可操作人数", () => {
  assert.equal(access.sharesSummary([]), "还没有共享给别人");
  assert.equal(access.sharesSummary(null), "还没有共享给别人");
  assert.equal(
    access.sharesSummary([
      { email: "a@example.com", role: "viewer", created_at: "" },
      { email: "b@example.com", role: "viewer", created_at: "" },
      { email: "c@example.com", role: "editor", created_at: "" },
    ]),
    "已共享给 3 人（2 只读 / 1 可操作）"
  );
  assert.equal(
    access.sharesSummary([{ email: "c@example.com", role: "editor", created_at: "" }]),
    "已共享给 1 人（1 可操作）"
  );
});

test("accessTotalsText 汇总四类计数", () => {
  assert.match(access.accessTotalsText(null), /正在读取/);
  assert.equal(
    access.accessTotalsText({ owned: 3, shared_out: 1, incoming: 2, groups: 4 }),
    "我创建 3 · 已外发 1 · 共享给我 2 · 成员组 4"
  );
});

test("sessionRoleBadge 只给共享会话出徽标", () => {
  assert.equal(access.sessionRoleBadge(undefined), null);
  assert.equal(access.sessionRoleBadge("owner"), null);
  assert.equal(access.sessionRoleBadge("viewer"), "共享给我 · 只读");
  assert.equal(access.sessionRoleBadge("editor"), "共享给我 · 可操作");
});

test("phaseLabel 覆盖 v2 与旧相位，未知原样返回", () => {
  assert.equal(access.phaseLabel("clarifying"), "澄清中");
  assert.equal(access.phaseLabel("audit_completed"), "审计完成");
  assert.equal(access.phaseLabel("brainstorm"), "脑暴");
  assert.equal(access.phaseLabel("something-new"), "something-new");
});

test("formatUpdated 解析 ISO，坏值回落占位符", () => {
  assert.equal(access.formatUpdated(null), "—");
  assert.equal(access.formatUpdated("not-a-date"), "—");
  const rendered = access.formatUpdated("2026-11-26T14:21:00");
  assert.match(rendered, /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/);
});

test("accessErrorHint 把 401 翻译成登录提示", () => {
  assert.match(access.accessErrorHint(new Error("HTTP 401")), /请先登录/);
  assert.match(access.accessErrorHint(new Error("请先登录再访问")), /请先登录/);
  assert.equal(access.accessErrorHint(new Error("网络开小差了")), "网络开小差了");
  assert.equal(access.accessErrorHint(null), "");
});

// -- 页面渲染 -------------------------------------------------------------

test("权限总览页首屏：三个分区与入口齐全", () => {
  const html = renderToStaticMarkup(React.createElement(AccessSettingsPage));
  assert.match(html, /权限总览/);
  assert.match(html, /正在读取权限信息/);
  assert.match(html, /我创建的会话/);
  assert.match(html, /共享给我的会话/);
  assert.match(html, /我的成员组/);
  assert.match(html, /href="\/settings\/team"/);
  assert.match(html, /安全提醒/);
  assert.match(html, /正在读取权限信息/);
});

test("历史抽屉：共享会话带角色徽标且没有删除按钮", () => {
  useSessionStore.setState({
    historySessions: [
      {
        id: "s".repeat(32),
        problem_statement: "别人共享给我的会话",
        phase: "brainstorm",
        message_count: 4,
        created_at: "2026-11-26T10:00:00",
        my_role: "viewer",
        owner_email: "owner@example.com",
      },
      {
        id: "o".repeat(32),
        problem_statement: "我自己创建的会话",
        phase: "brainstorm",
        message_count: 2,
        created_at: "2026-11-26T09:00:00",
        my_role: "owner",
      },
    ],
    historyLoading: false,
    historyError: null,
  });

  const html = renderToStaticMarkup(
    React.createElement(HistoryDrawer, { isOpen: true, onClose() {} })
  );
  assert.match(html, /共享给我 · 只读/);
  assert.match(html, /别人共享给我的会话/);
  assert.match(html, /我自己创建的会话/);
  // 共享会话不出现删除按钮；自己创建的会话保留删除按钮。
  assert.equal(html.includes("删除会话：别人共享给我的会话"), false);
  assert.match(html, /删除会话：我自己创建的会话/);
});
