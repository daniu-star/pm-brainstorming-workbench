/**
 * 团队工作区（第 37 批）的前端两层测试。
 *
 * 1. `lib/teamGroups.ts` 的纯函数：名称归一化、成员组摘要、应用结果提示、错误映射；
 * 2. 服务端渲染结构：团队页首屏、`SharePanel` 的「从成员组一次性添加」区块。
 *
 * 真账号 + 真授权的交互流程由后端 `backend/tests/test_member_groups.py` 与浏览器手测覆盖；
 * 这里挡住「删掉分支仍然绿」的模板回退。
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
  if (request === "next/link") {
    return function Link(props) {
      const { href, children, ...rest } = props;
      return React.createElement("a", { href: typeof href === "string" ? href : "#", ...rest }, children);
    };
  }
  if (request === "next/navigation") {
    return {
      useParams: () => ({ id: "a".repeat(32) }),
      useSearchParams: () => new URLSearchParams(),
      useRouter: () => ({ push() {}, replace() {}, refresh() {}, prefetch() {}, back() {} }),
      usePathname: () => "/settings/team",
      redirect() {},
      notFound() {},
    };
  }
  return originalLoad.call(this, request, parent, isMain);
};

const { renderToStaticMarkup } = require("react-dom/server");
const teamGroups = require(path.join(TEST_ROOT, "lib", "teamGroups.js"));
const TeamPage = require(path.join(BUILD_ROOT, "app", "settings", "team", "page.js")).default;
const { SharePanel } = require(path.join(BUILD_ROOT, "components", "share", "SharePanel.js"));

test("normalizeGroupName 去空白压空格，空名与超长名返回 null", () => {
  assert.equal(teamGroups.normalizeGroupName("  核心   评审团 "), "核心 评审团");
  assert.equal(teamGroups.normalizeGroupName("评审组\tA"), "评审组 A");
  assert.equal(teamGroups.normalizeGroupName(""), null);
  assert.equal(teamGroups.normalizeGroupName("   "), null);
  assert.equal(teamGroups.normalizeGroupName(null), null);
  assert.equal(teamGroups.normalizeGroupName("x".repeat(81)), null);
  assert.equal(teamGroups.normalizeGroupName("x".repeat(80)), "x".repeat(80));
});

test("groupSummary 按角色计数，空组给明确空态", () => {
  assert.equal(teamGroups.groupSummary({ id: "g", name: "空组", owner_email: "o@x.com", members: [], created_at: "" }), "还没有成员");
  assert.equal(
    teamGroups.groupSummary({
      members: [
        { email: "a@x.com", role: "viewer", created_at: "" },
        { email: "b@x.com", role: "viewer", created_at: "" },
        { email: "c@x.com", role: "editor", created_at: "" },
      ],
    }),
    "3 人 · 2 只读 · 1 可操作"
  );
  assert.equal(
    teamGroups.groupSummary({ members: [{ email: "a@x.com", role: "editor", created_at: "" }] }),
    "1 人 · 1 可操作"
  );
});

test("applyGroupHint 区分空组、已在列表与新增成功", () => {
  assert.match(
    teamGroups.applyGroupHint({ group_id: "g", added: 0, already_shared: 0, members_total: 0 }),
    /先加人/
  );
  assert.match(
    teamGroups.applyGroupHint({ group_id: "g", added: 0, already_shared: 2, members_total: 2 }),
    /已经都在共享列表里/
  );
  const added = teamGroups.applyGroupHint({ group_id: "g", added: 2, already_shared: 0, members_total: 2 });
  assert.match(added, /已添加 2 人/);
  const partly = teamGroups.applyGroupHint({ group_id: "g", added: 1, already_shared: 2, members_total: 3 });
  assert.match(partly, /已添加 1 人/);
  assert.match(partly, /2 人原本就在/);
});

test("groupErrorHint 对同名给出换名建议，其余回落到通用文案", () => {
  assert.match(teamGroups.groupErrorHint(new Error("已有同名成员组：评审团")), /换个名字/);
  assert.match(teamGroups.groupErrorHint(new Error("一个成员组最多 50 人")), /最多 50 人/);
  assert.equal(teamGroups.groupErrorHint(new Error("")), "操作失败，请稍后重试");
  assert.equal(teamGroups.groupErrorHint(null), "操作失败，请稍后重试");
  assert.equal(teamGroups.groupErrorHint("boom"), "操作失败，请稍后重试");
});

test("团队页首屏：标题、建组表单与「正在读取成员组…」加载态", () => {
  const html = renderToStaticMarkup(React.createElement(TeamPage));
  assert.match(html, /团队工作区/);
  assert.match(html, /aria-label="成员组名称"/);
  assert.match(html, /创建成员组/);
  assert.match(html, /正在读取成员组…/);
  // SSR 里 effect 不执行，不能误显示「还没有成员组」
  assert.doesNotMatch(html, /还没有成员组，先在上方创建一个/);
});

test("SharePanel 渲染「从成员组一次性添加」区块与回退链接", () => {
  const html = renderToStaticMarkup(
    React.createElement(SharePanel, { sessionId: "s1", myEmail: "owner@example.com", onClose() {} })
  );
  assert.match(html, /从成员组一次性添加/);
  assert.match(html, /正在读取成员组…/);
  assert.match(html, /组内成员按各自的只读 \/ 可操作角色写入/);
  // 首屏同时在读取共享列表与成员组，不显示空态
  assert.doesNotMatch(html, /还没有成员组，先/);
});
