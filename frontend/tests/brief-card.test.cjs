"use strict";

/**
 * BriefCard 本地草稿保护纯逻辑测试（node --test，无第三方 runner）。
 *
 * 被测模块：frontend/lib/briefDraft.ts（经 tsconfig.test.json 编译到
 * node_modules/.cache/test-build/lib/briefDraft.js）。本机 npm registry 不可达，
 * 不能安装 @testing-library / jsdom，所以把「服务端版本变化时怎么处理本地草稿」
 * 抽成纯函数，页面组件（frontend/components/brief/BriefCard.tsx）在
 * productState?.version 变化时调用它。
 *
 * 回归点：原实现依赖全局 productVersion 且无条件 setEdits({})，用户正在输入时
 * 收到 product_state.updated 会把草稿静默抹掉。
 */

const { test } = require("node:test");
const assert = require("node:assert/strict");

const draft = require("../node_modules/.cache/test-build/lib/briefDraft.js");

test("hasBriefEdits：只有真正输入过的字段才算草稿", () => {
  assert.equal(draft.hasBriefEdits({}), false);
  assert.equal(draft.hasBriefEdits(null), false);
  assert.equal(draft.hasBriefEdits(undefined), false);
  assert.equal(draft.hasBriefEdits({ target_user: "" }), true);
  assert.equal(draft.hasBriefEdits({ target_user: "独立开发者" }), true);
});

test("服务端版本更新时：有草稿必须保留并提示「覆盖 / 丢弃」", () => {
  const decision = draft.decideBriefDraftOnServerVersion({ target_user: "独立开发者" });
  assert.equal(decision.keepEdits, true, "不能静默清空用户输入");
  assert.equal(decision.notice, draft.BRIEF_STALE_NOTICE);
  assert.match(decision.notice, /服务端已更新/);
  assert.match(decision.notice, /覆盖/);
  assert.match(decision.notice, /丢弃/);
});

test("服务端版本更新时：没有草稿才允许清空", () => {
  const decision = draft.decideBriefDraftOnServerVersion({});
  assert.equal(decision.keepEdits, false);
  assert.equal(decision.notice, null);
});
