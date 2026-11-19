"use strict";

/**
 * Loop 工作室纯函数测试（node --test，无 RTL / 无 jsdom）。
 *
 * `lib/loopView.ts` 特意把「页面要展示成什么」全部抽成纯函数：
 * 标签、五阶段补位、评分卡七维、决策可用性、入口状态、错误文案……
 * 这里逐条覆盖，保证页面拿到的是中文、有兜底、绝不出现 undefined。
 */

const { test } = require("node:test");
const assert = require("node:assert/strict");

const view = require("../node_modules/.cache/test-build/lib/loopView.js");

/** 最小 LoopSnapshot 夹具：字段与 backend/domain/contracts.py 对齐。 */
function snapshot(overrides = {}) {
  return {
    loop_id: "loop_1",
    session_id: "s1",
    owner_email: "me@example.com",
    status: "running",
    scope: "mvp",
    current_round: 1,
    max_rounds: 3,
    target_score: 85,
    loop_version: 1,
    focus: "",
    rounds: [],
    mvp_spec: null,
    validation: null,
    prototype: null,
    test_report: null,
    scorecard: null,
    latest_run_id: null,
    created_at: "",
    updated_at: "",
    ...overrides,
  };
}

function round(ordinal, overrides = {}) {
  return {
    id: `round_${ordinal}`,
    ordinal,
    status: "running",
    score: null,
    verdict: "pending",
    stages: [],
    artifacts: [],
    started_at: "",
    finished_at: null,
    ...overrides,
  };
}

// --------------------------------------------------------------------------- //
// 标签
// --------------------------------------------------------------------------- //

test("五阶段与状态标签是中文，且未知值原样回退不显示 undefined", () => {
  assert.equal(view.stageLabel("ideate"), "创意发散");
  assert.equal(view.stageLabel("evaluate"), "评分复盘");
  assert.ok(view.STAGE_HINTS.evaluate.includes("七维"));
  assert.deepEqual(
    view.stageProgress(null).map((item) => item.stage),
    ["ideate", "validate", "develop", "test", "evaluate"]
  );
  assert.equal(view.stageLabel("unknown_stage"), "unknown_stage");
  assert.equal(view.stageLabel(null), "未知阶段");
  assert.equal(view.stageStatusLabel(undefined), "待开始");
  assert.equal(view.statusLabel("awaiting_decision"), "等待决策");
  assert.equal(view.statusLabel(undefined), "未知状态");
});

test("评分维度与产物标题用契约中文，effort 带上英文档位", () => {
  assert.equal(view.scoreKeyLabel("functionality"), "功能可用性");
  assert.equal(view.scoreKeyLabel("docs"), "文档与交接");
  assert.equal(view.artifactTitle("test_report"), "测试报告");
  assert.equal(view.artifactTitle("nope"), "nope");
  assert.equal(view.priorityLabel("must"), "必须");
  assert.equal(view.priorityLabel(undefined), "应该");
  assert.equal(view.effortLabel("S"), "S（小）");
  assert.equal(view.effortLabel("L"), "L（大）");
  assert.equal(view.checkResultLabel("pass"), "通过");
  assert.equal(view.verdictLabel("pass"), "通过");
  assert.equal(view.verdictLabel(null), "待评级");
});

// --------------------------------------------------------------------------- //
// 阶段补位 / 进度
// --------------------------------------------------------------------------- //

test("stageProgress 永远补满五个阶段，未开始的补 pending", () => {
  const stages = view.stageProgress(
    round(1, {
      stages: [
        { stage: "ideate", status: "done", summary: "拆出 3 个功能", error_code: null, updated_at: "" },
        { stage: "validate", status: "running", summary: "", error_code: null, updated_at: "" },
      ],
    })
  );
  assert.equal(stages.length, 5);
  assert.deepEqual(
    stages.map((item) => item.stage),
    ["ideate", "validate", "develop", "test", "evaluate"]
  );
  assert.equal(stages[0].status, "done");
  assert.equal(stages[0].tone, "emerald");
  assert.equal(stages[0].statusLabel, "已完成");
  assert.equal(stages[1].tone, "brand");
  assert.equal(stages[2].status, "pending");
  assert.equal(stages[2].statusLabel, "待开始");
  assert.equal(stages[2].tone, "zinc");
  assert.equal(stages[0].summary, "拆出 3 个功能");
  // 没有轮次数据也必须能渲染出 5 个占位节点，页面不 crash
  assert.equal(view.stageProgress(null).length, 5);
});

test("roundProgress 统计完成/失败比例，currentStageKey 优先运行中阶段", () => {
  const current = round(1, {
    stages: [
      { stage: "ideate", status: "done" },
      { stage: "validate", status: "done" },
      { stage: "develop", status: "running" },
      { stage: "test", status: "failed", error_code: "PROTOTYPE_BUILD_FAILED" },
    ],
  });
  const progress = view.roundProgress(current);
  assert.equal(progress.done, 2);
  assert.equal(progress.total, 5);
  assert.equal(progress.failed, 1);
  assert.equal(progress.running, 1);
  assert.equal(progress.percent, 40);
  assert.equal(view.currentStageKey(current), "develop");
});

test("pickCurrentRound 优先 current_round，缺失时退回最后一轮", () => {
  const snap = snapshot({
    current_round: 2,
    rounds: [round(1), round(2), round(3)],
  });
  assert.equal(view.pickCurrentRound(snap).ordinal, 2);
  assert.equal(view.pickCurrentRound(snapshot({ current_round: 9, rounds: [round(1), round(3)] })).ordinal, 3);
  assert.equal(view.pickCurrentRound(snapshot()), null);
  assert.equal(view.pickCurrentRound(null), null);
  assert.equal(view.formatRound(2, 3), "第 2 / 3 轮");
  assert.equal(view.formatRound(99, 99), "第 5 / 5 轮");
  assert.equal(view.roundScoreText(round(1, { score: 78 })), "78/100");
  assert.equal(view.roundScoreText(null), "未生成");
});

// --------------------------------------------------------------------------- //
// 评分卡
// --------------------------------------------------------------------------- //

test("formatScore 与七维评分条：有卡片显示分数，缺维度不补假数据", () => {
  assert.equal(view.formatScore(null), "未生成");
  assert.equal(view.formatScore({ total: 78 }), "78/100");
  assert.equal(view.scoreDimensionMaxTotal(), 100);

  const card = {
    total: 78,
    verdict: "iterate",
    generated_by: "model",
    hard_capped: false,
    blockers: [],
    highlights: [],
    next_actions: [],
    dimensions: [
      { key: "docs", score: 3, max: 5, reason: "交接文档待补" },
      { key: "functionality", score: 20, max: 25, reason: "主流程可跑通" },
      { key: "nope", score: 1, max: 1, reason: "" },
    ],
  };
  const rows = view.scoreDimensionRows(card);
  assert.deepEqual(
    rows.map((row) => row.key),
    ["functionality", "docs"]
  );
  assert.equal(rows[0].label, "功能可用性");
  assert.equal(rows[0].percent, 80);
  assert.equal(rows[1].label, "文档与交接");
  assert.equal(rows[1].score, 3);
  assert.equal(rows[1].max, 5);
  assert.deepEqual(view.scoreDimensionRows(null), []);
});

// --------------------------------------------------------------------------- //
// 决策可用性
// --------------------------------------------------------------------------- //

test("decisionAvailability：只有 awaiting_decision 才能决策", () => {
  const running = view.decisionAvailability(snapshot({ status: "running" }));
  assert.equal(running.awaiting, false);
  assert.equal(running.iterate.enabled, false);
  assert.equal(running.ship.enabled, false);
  assert.equal(running.stop.enabled, false);
  assert.ok(running.iterate.reason.includes("执行"));

  const shipped = view.decisionAvailability(snapshot({ status: "shipped" }));
  assert.equal(shipped.ship.enabled, false);
  assert.ok(shipped.ship.reason.includes("已结束"));

  const none = view.decisionAvailability(null);
  assert.equal(none.awaiting, false);
  assert.ok(none.iterate.reason.includes("尚未启动"));
});

test("decisionAvailability：轮次未耗尽 iterate 可点，耗尽后只能交付/停止", () => {
  const fresh = view.decisionAvailability(
    snapshot({ status: "awaiting_decision", current_round: 1, max_rounds: 3 })
  );
  assert.equal(fresh.awaiting, true);
  assert.equal(fresh.iterate.enabled, true);
  assert.equal(fresh.iterate.reason, "");
  assert.equal(fresh.ship.enabled, true);
  assert.equal(fresh.stop.enabled, true);

  const exhausted = view.decisionAvailability(
    snapshot({ status: "awaiting_decision", current_round: 3, max_rounds: 3 })
  );
  assert.equal(exhausted.iterate.enabled, false);
  assert.equal(exhausted.iterate.reason, "轮次已用尽（3/3），只能交付或停止");
  assert.equal(exhausted.ship.enabled, true);
  assert.equal(exhausted.stop.enabled, true);
  assert.equal(view.normalizeDecisionNote("  继续补测试  "), "继续补测试");
  assert.equal(view.normalizeDecisionNote(null), "");
});

// --------------------------------------------------------------------------- //
// 阻塞项 / 错误文案
// --------------------------------------------------------------------------- //

test("blockerText 过滤空白并给出数量，空列表明确提示", () => {
  assert.equal(view.blockerText([]), "暂无阻塞项");
  assert.equal(view.blockerText(null), "暂无阻塞项");
  assert.equal(
    view.blockerText(["支付回调未验证", "  ", "移动端未适配"]),
    "共 2 个阻塞项：支付回调未验证；移动端未适配"
  );
  assert.deepEqual(view.blockerList(["a", "", "b"]), ["a", "b"]);
});

test("loopErrorText 覆盖闭环错误码，未知码回退后端 message", () => {
  assert.ok(view.loopErrorText("LOOP_VERSION_CONFLICT").includes("闭环状态已在别处更新"));
  assert.ok(view.loopErrorText("LOOP_ROUNDS_EXHAUSTED").includes("只能交付或停止"));
  assert.ok(view.loopErrorText("LOOP_ACTIVE").includes("已有进行中的闭环"));
  assert.equal(view.loopErrorText("NOT_FOUND"), "闭环不存在或已删除");
  // DX-02：请求超时要有独立中文文案，不能只显示通用「操作失败」
  assert.equal(view.loopErrorText("TIMEOUT"), "请求超时，请稍后重试");
  assert.equal(view.loopErrorText("SOMETHING_ELSE", "后端原话"), "后端原话");
  assert.equal(view.loopErrorText(null), "操作失败，请稍后重试");
});

// --------------------------------------------------------------------------- //
// 产物 Tab
// --------------------------------------------------------------------------- //

test("artifactTabs 永远五类，available 跟随快照与本轮 artifacts", () => {
  const snap = snapshot({
    mvp_spec: { title: "t", summary: "", features: [], assumptions: [], not_in_scope: [] },
    scorecard: { total: 90 },
    validation: null,
  });
  const tabs = view.artifactTabs(snap, round(1, { artifacts: [{ kind: "prototype" }] }));
  assert.equal(tabs.length, 5);
  assert.deepEqual(
    tabs.map((tab) => tab.kind),
    ["mvp_spec", "validation", "prototype", "test_report", "scorecard"]
  );
  const byKind = new Map(tabs.map((tab) => [tab.kind, tab]));
  assert.equal(byKind.get("mvp_spec").available, true);
  assert.equal(byKind.get("scorecard").available, true);
  assert.equal(byKind.get("prototype").available, true);
  assert.equal(byKind.get("validation").available, false);
  assert.equal(byKind.get("test_report").title, "测试报告");

  assert.equal(view.defaultArtifactTab(snap), "mvp_spec");
  assert.equal(view.defaultArtifactTab(snapshot({ scorecard: { total: 1 } })), "scorecard");
  assert.equal(view.defaultArtifactTab(snapshot()), "mvp_spec");
  assert.equal(view.hasArtifact(snap, "mvp_spec"), true);
  assert.equal(view.hasArtifact(snap, "test_report"), false);
});

// --------------------------------------------------------------------------- //
// 启动表单
// --------------------------------------------------------------------------- //

test("validateStartForm 夹取轮次与目标分，空表单给默认值", () => {
  const form = view.validateStartForm({});
  assert.equal(form.ok, true);
  assert.equal(form.scope, "mvp");
  assert.equal(form.max_rounds, 3);
  assert.equal(form.target_score, 85);
  assert.equal(form.focus, "");
  assert.equal(form.error, null);

  const clamped = view.validateStartForm({
    scope: "optimize",
    maxRounds: "9",
    targetScore: 10,
    focus: "  只做单人流程  ",
  });
  assert.equal(clamped.scope, "optimize");
  assert.equal(clamped.max_rounds, 5);
  assert.equal(clamped.target_score, 60);
  assert.equal(clamped.focus, "只做单人流程");

  const tooLong = view.validateStartForm({ focus: "x".repeat(2001) });
  assert.equal(tooLong.ok, false);
  assert.ok(tooLong.error.includes("2000"));
});

// --------------------------------------------------------------------------- //
// 快照归一化 / 局部事件合并
// --------------------------------------------------------------------------- //

test("normalizeLoopSnapshot 没有 loop_id 视为未启动，拒绝脏数据", () => {
  assert.equal(view.normalizeLoopSnapshot(null), null);
  assert.equal(view.normalizeLoopSnapshot({}), null);
  assert.equal(view.normalizeLoopSnapshot({ loop_id: "" }), null);

  const normalized = view.normalizeLoopSnapshot({
    loop_id: "loop_9",
    session_id: "s1",
    status: "not_a_status",
    scope: "bogus",
    current_round: 99,
    max_rounds: 99,
    target_score: 10,
    loop_version: 0,
  });
  assert.equal(normalized.status, "running");
  assert.equal(normalized.scope, "mvp");
  assert.equal(normalized.current_round, 5);
  assert.equal(normalized.max_rounds, 5);
  assert.equal(normalized.target_score, 60);
  assert.equal(normalized.loop_version, 1);
});

test("normalizeLoopSnapshot 按 ordinal 排序去重，并归一化五类产物", () => {
  const normalized = view.normalizeLoopSnapshot({
    loop_id: "loop_1",
    rounds: [
      round(2, { status: "done", score: 80 }),
      round(1, { status: "done", score: 70 }),
      round(2, { status: "failed" }),
    ],
    mvp_spec: {
      title: "规格",
      features: [{ id: "f1", title: "登录", acceptance: "可登录" }],
      assumptions: [{ id: "a1", statement: "假设" }],
    },
    test_report: {
      total: 99,
      cases: [
        { id: "c1", title: "用例", steps: [], expected: "", status: "passed" },
        { id: "c2", title: "用例2", steps: [], expected: "", status: "failed" },
      ],
    },
  });
  assert.deepEqual(
    normalized.rounds.map((item) => item.ordinal),
    [1, 2]
  );
  assert.equal(normalized.rounds[1].score, 80);
  assert.equal(normalized.mvp_spec.features[0].priority, "should");
  assert.equal(normalized.mvp_spec.features[0].effort, "M");
  assert.equal(normalized.test_report.total, 2);
  assert.equal(normalized.test_report.passed, 1);
  assert.equal(normalized.test_report.failed, 1);
});

test("mergeLoopUpdate 只覆盖 payload 带上的字段，版本不回退", () => {
  const base = snapshot({
    status: "running",
    loop_version: 5,
    rounds: [
      round(1, {
        status: "running",
        stages: [
          { stage: "evaluate", status: "running", summary: "", error_code: null, updated_at: "" },
        ],
      }),
    ],
  });
  const merged = view.mergeLoopUpdate(base, {
    loop_id: "loop_1",
    status: "awaiting_decision",
    current_round: 1,
    loop_version: 1,
    stage: "evaluate",
    stage_status: "done",
    score: 82,
    verdict: "pass",
    artifact_ids: ["a1"],
  });
  assert.equal(merged.status, "awaiting_decision");
  assert.equal(merged.loop_version, 5);
  assert.equal(merged.rounds[0].score, 82);
  assert.equal(merged.rounds[0].verdict, "pass");
  assert.equal(merged.rounds[0].stages[0].status, "done");
  // 局部事件不携带 rounds/产物时，原快照的产物不能被清空
  assert.equal(merged.mvp_spec, null);

  const other = view.mergeLoopUpdate(base, { loop_id: "loop_other", status: "shipped" });
  assert.deepEqual(other, base);
  assert.deepEqual(view.mergeLoopUpdate(base, null), base);
});

// --------------------------------------------------------------------------- //
// 页面入口状态
// --------------------------------------------------------------------------- //

test("resolveLoopEntryState 覆盖加载/门槛/启动表单/闭环主体四种渲染", () => {
  const loading = view.resolveLoopEntryState({
    sessionLoading: true,
    sessionPhase: "clarifying",
    legacyMode: false,
    loop: null,
    loopLoading: false,
    loopError: null,
  });
  // AUD-06：会话加载中先渲染「加载中」，不能闪「暂不可用」（gate 只用于阶段不满足）。
  assert.equal(loading.showLoading, true);
  assert.equal(loading.showGate, false);
  assert.equal(loading.showStartPanel, false);
  assert.equal(loading.showLoop, false);

  const legacy = view.resolveLoopEntryState({
    sessionLoading: false,
    sessionPhase: "brainstorming",
    legacyMode: true,
    loop: null,
    loopLoading: false,
    loopError: null,
  });
  assert.equal(legacy.showGate, true);
  assert.ok(legacy.gateText.includes("旧版会话"));

  const start = view.resolveLoopEntryState({
    sessionLoading: false,
    sessionPhase: "brainstorming",
    legacyMode: false,
    loop: null,
    loopLoading: false,
    loopError: null,
  });
  assert.equal(start.showStartPanel, true);
  assert.equal(start.showLoop, false);

  const existing = view.resolveLoopEntryState({
    sessionLoading: false,
    sessionPhase: "audit_completed",
    legacyMode: false,
    loop: snapshot({ status: "awaiting_decision" }),
    loopLoading: false,
    loopError: null,
  });
  assert.equal(existing.showLoop, true);

  const notFound = view.resolveLoopEntryState({
    sessionLoading: false,
    sessionPhase: "brainstorming",
    legacyMode: false,
    loop: null,
    loopLoading: false,
    loopError: { message: "404", notFound: true },
  });
  assert.equal(notFound.showStartPanel, true);
  assert.ok(notFound.loadErrorText.includes("重新启动"));
});

test("shouldShowLoopEntry 只在 brainstorming / audit_completed 且非 legacy 时显示", () => {
  assert.equal(view.shouldShowLoopEntry({ v2Phase: "brainstorming", legacyMode: false }), true);
  assert.equal(view.shouldShowLoopEntry({ v2Phase: "audit_completed", legacyMode: false }), true);
  assert.equal(view.shouldShowLoopEntry({ v2Phase: "clarifying", legacyMode: false }), false);
  assert.equal(view.shouldShowLoopEntry({ v2Phase: "auditing", legacyMode: false }), false);
  assert.equal(view.shouldShowLoopEntry({ v2Phase: "brainstorming", legacyMode: true }), false);
  assert.equal(view.shouldShowLoopEntry({ v2Phase: null, legacyMode: false }), false);
});

// --------------------------------------------------------------------------- //
// 展示小工具
// --------------------------------------------------------------------------- //

test("formatTimestamp / formatBytes / testReportStats 有稳定兜底", () => {
  // AUD-05：按浏览器本地时区格式化，而不是把 UTC ISO 串截断后当本地时间展示。
  const instant = new Date("2026-01-02T03:04:05Z");
  const localText = new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
    hour12: false,
  }).format(instant);
  const shown = view.formatTimestamp("2026-01-02T03:04:05Z");
  assert.equal(shown, localText);
  assert.ok(!shown.includes("T") && !shown.includes("Z"), "不应残留 ISO 的 T/Z 标记");
  assert.notEqual(shown, "2026-01-02 03:04");
  assert.equal(view.formatTimestamp("not-a-date"), "not-a-date");
  assert.equal(view.formatTimestamp(""), "—");
  assert.equal(view.formatTimestamp(null), "—");
  assert.equal(view.formatBytes(0), "0 B");
  assert.equal(view.formatBytes(2048), "2.0 KB");
  assert.equal(view.formatBytes(null), "0 B");
  assert.deepEqual(view.testReportStats(null), {
    total: 0,
    passed: 0,
    failed: 0,
    skipped: 0,
    passRate: 0,
  });
  assert.equal(
    view.testReportStats({ total: 4, passed: 3, failed: 1, skipped: 0 }).passRate,
    75
  );
});
