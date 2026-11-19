/**
 * 第 5 轮验收缺口回归（R4：① 简报再确认 / ② 审计失败恢复与报告过期 /
 * ③ 闭环历史轮次回看与对比 / ⑤ 脑暴失败重试）。
 *
 * 与 page-wiring.test.cjs 同一策略：本机无法安装 jsdom / @testing-library，
 * tsconfig.test.json 也不编译页面 .tsx，因此：
 *   - 纯函数直接用 node:test 断言；
 *   - 页面接线读取源码，只断言「哪个值传给了哪个函数 / 哪个入口被渲染」，
 *     不做快照、不校验措辞细节；页面重构格式时应调整正则，而不是删用例。
 *
 * 说明：审计失败/过期与简报再确认的页面接线在本轮修复中已实现，这些用例是
 * 防止回退的护栏；闭环轮次切换与脑暴失败重试在用例落盘时仍未接线，因此先在
 * 当前代码上确认红灯，修复后转绿。
 */
"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const BUILD = path.join(__dirname, "..", "node_modules", ".cache", "test-build", "lib");
const auditView = require(path.join(BUILD, "auditView.js"));
const loopView = require(path.join(BUILD, "loopView.js"));

const FRONTEND = path.join(__dirname, "..");

function read(relativePath) {
  return fs.readFileSync(path.join(FRONTEND, relativePath), "utf8");
}

function between(source, startMarker, endMarker) {
  const start = source.indexOf(startMarker);
  assert.notEqual(start, -1, `源码里找不到起点标记：${startMarker}`);
  const end = source.indexOf(endMarker, start);
  assert.notEqual(end, -1, `源码里找不到终点标记：${endMarker}`);
  return source.slice(start, end);
}

function round(ordinal, score, verdict, artifacts = []) {
  return {
    id: `round-${ordinal}`,
    ordinal,
    status: "done",
    score,
    verdict,
    stages: [],
    artifacts,
    started_at: "2026-11-19T00:00:00Z",
    finished_at: "2026-11-19T00:10:00Z",
  };
}

function artifact(kind, id) {
  return {
    id,
    kind,
    title: kind,
    size_bytes: 1024,
    created_at: "2026-11-19T00:00:00Z",
  };
}

// --------------------------------------------------------------------------- //
// ② 审计开场/回答失败的可见原因与恢复入口（P1-1 / AUD-01）
// --------------------------------------------------------------------------- //

test("resolveAuditStepFailureState：opening/active 且失败 Run 属于当前审计时才展示", () => {
  const openFailure = auditView.resolveAuditStepFailureState({
    status: "opening",
    failedRunBelongsToAudit: true,
    runErrorText: "模型服务鉴权失败（401）：请更新 LLM_API_KEY",
  });
  assert.equal(openFailure.show, true);
  assert.match(openFailure.reasonText, /鉴权失败/);
  assert.match(openFailure.reasonText, /LLM_API_KEY/);
  assert.match(openFailure.hintText, /重试该步骤/);
  assert.match(openFailure.hintText, /已提交的回答不会丢失/);
});

test("resolveAuditStepFailureState：没有 Run 文案时回落到 end_reason，不编造原因", () => {
  const answerFailure = auditView.resolveAuditStepFailureState({
    status: "active",
    failedRunBelongsToAudit: true,
    endReasonRaw: "LLM_AUTH_ERROR",
  });
  assert.equal(answerFailure.show, true);
  assert.equal(answerFailure.reasonText, "LLM_AUTH_ERROR");
});

test("resolveAuditStepFailureState：其它审计/其它状态不得误报", () => {
  for (const input of [
    { status: "opening", failedRunBelongsToAudit: false, runErrorText: "x" },
    { status: "active", failedRunBelongsToAudit: false },
    { status: "failed", failedRunBelongsToAudit: true, runErrorText: "x" },
    { status: "closing", failedRunBelongsToAudit: true, runErrorText: "x" },
    { status: null, failedRunBelongsToAudit: true, runErrorText: "x" },
  ]) {
    assert.equal(
      auditView.resolveAuditStepFailureState(input).show,
      false,
      `不应展示：${JSON.stringify(input)}`
    );
  }
});

test("audit 页：开场/回答失败卡展示原因与指引，并接上 retryFailedRun", () => {
  const source = read("app/session/[id]/audit/page.tsx");
  assert.match(source, /resolveAuditStepFailureState\(\{/);
  assert.match(source, /failedRunBelongsToAudit/);
  assert.match(source, /stepFailure\.show/);
  assert.match(source, /stepFailure\.reasonText/);
  assert.match(source, /stepFailure\.hintText/);
  assert.match(source, /失败原因：/);
  assert.match(source, /重试该步骤/);
  assert.match(source, /retryFailedRun\(lastFailedRunId\)/);
});

// --------------------------------------------------------------------------- //
// ②' 旧版本报告过期提示（P1-4 / AUD-02）
// --------------------------------------------------------------------------- //

test("resolveAuditStaleness：旧版本报告必须标过期并给出当前版本", () => {
  const stale = auditView.resolveAuditStaleness({
    auditProductVersion: 1,
    currentProductVersion: 2,
  });
  assert.equal(stale.stale, true);
  assert.match(stale.text, /本报告基于 v1 生成/);
  assert.match(stale.text, /当前产品已到 v2/);
  assert.match(stale.text, /重新审计/);
});

test("resolveAuditStaleness：同版本/新版本/缺版本都不算过期", () => {
  for (const input of [
    { auditProductVersion: 2, currentProductVersion: 2 },
    { auditProductVersion: 3, currentProductVersion: 2 },
    { auditProductVersion: 0, currentProductVersion: 2 },
    { auditProductVersion: null, currentProductVersion: 2 },
    { auditProductVersion: 1, currentProductVersion: null },
    {},
  ]) {
    const state = auditView.resolveAuditStaleness(input);
    assert.equal(state.stale, false, `不应标过期：${JSON.stringify(input)}`);
    assert.equal(state.text, "");
  }
});

test("audit 页：报告过期提示渲染文案并复用「重新审计」", () => {
  const source = read("app/session/[id]/audit/page.tsx");
  assert.match(source, /resolveAuditStaleness\(\{/);
  assert.match(source, /staleness\.stale/);
  assert.match(source, /staleness\.text/);
  assert.match(source, /重新审计/);
  assert.match(source, /handleStartAudit/);
});

// --------------------------------------------------------------------------- //
// ① 已确认简报的「确认字段修订」再确认入口（P1-4 / BRIEF-01）
// --------------------------------------------------------------------------- //

test("BriefCard：已确认简报修订时显式提交 confirmed，不再产生「待确认」漂移", () => {
  const source = read("components/brief/BriefCard.tsx");
  assert.match(
    source,
    /const nextStatus[^=]*=\s*brief\.status === "confirmed" \? "confirmed" : "proposed"/,
    "已确认简报的编辑必须以 confirmed 提交"
  );
  assert.match(source, /briefFieldsAwaitingConfirm/);
});

test("BriefCard：历史漂移字段有真实的「确认字段修订」入口（revise_brief + confirmed）", () => {
  const source = read("components/brief/BriefCard.tsx");
  assert.match(source, /确认字段修订/);
  assert.match(source, /runCommand\("确认字段修订",\s*"revise_brief"/);
  const handler = between(source, "function handleConfirmDrift", "return (");
  assert.match(handler, /status:\s*"confirmed"/);
  assert.match(handler, /expected_product_version:\s*version/);
});

// --------------------------------------------------------------------------- //
// ③ 闭环历史轮次回看与多轮对比（P1-3 / LOOP-01）
// --------------------------------------------------------------------------- //

test("selectRound：优先选中轮，其次当前轮，越界回落到最后一轮", () => {
  const rounds = [round(1, 72, "iterate"), round(2, 78, "pass")];
  assert.equal(loopView.selectRound(rounds, 1, 2).ordinal, 1);
  assert.equal(loopView.selectRound(rounds, null, 2).ordinal, 2);
  assert.equal(loopView.selectRound(rounds, 9, 2).ordinal, 2, "越界选择回落到当前轮");
  assert.equal(loopView.selectRound(rounds, 9, 9).ordinal, 2, "再回落到最后一轮");
  assert.equal(loopView.selectRound([], 1, 1), null);
  assert.equal(loopView.selectRound(null, 1, 1), null);
});

test("roundSwitcherOptions：「第 N 轮 · 状态 · 分数」按 ordinal 升序且标记选中", () => {
  const options = loopView.roundSwitcherOptions(
    [round(2, 78, "pass"), round(1, 72, "iterate")],
    1
  );
  assert.deepEqual(
    options.map((option) => option.ordinal),
    [1, 2]
  );
  assert.equal(options[0].label, "第 1 轮");
  assert.equal(options[0].statusLabel, "已完成");
  assert.equal(options[0].scoreText, "72/100");
  assert.equal(options[0].active, true);
  assert.equal(options[1].active, false);
});

test("compareRounds：总分、verdict 与产物索引差异完整呈现", () => {
  const previous = round(1, 72, "iterate", [artifact("mvp_spec", "a1")]);
  const current = round(2, 78, "pass", [
    artifact("mvp_spec", "a1"),
    artifact("validation", "a2"),
  ]);
  const comparison = loopView.compareRounds(previous, current);

  assert.equal(comparison.hasPrevious, true);
  assert.equal(comparison.scoreDelta, 6);
  assert.match(comparison.scoreDeltaText, /72\/100 → 78\/100/);
  assert.match(comparison.scoreDeltaText, /\+6/);
  assert.equal(comparison.verdictText, "需迭代 → 通过");
  assert.equal(comparison.verdictChanged, true);
  assert.match(comparison.artifactDeltaText, /新增/);
  assert.match(comparison.artifactDeltaText, /假设检验/);
  assert.match(comparison.summary, /总分提升 \+6/);
  assert.equal(comparison.dimensionRows.length, 7);
  assert.equal(loopView.comparisonTone(comparison.scoreDelta), "emerald");
  assert.equal(loopView.comparisonTone(-3), "red");
  assert.equal(loopView.comparisonTone(0), "zinc");
});

test("compareRounds：没有上一轮时不编造对比数据", () => {
  const comparison = loopView.compareRounds(null, round(1, 72, "iterate"));
  assert.equal(comparison.hasPrevious, false);
  assert.equal(comparison.scoreDeltaText, "");
  assert.deepEqual(comparison.dimensionRows, []);
});

test("artifactTabs：历史轮产物按 round.artifacts 标记可用，不依赖快照 latest-of-kind", () => {
  const snapshot = {
    loop_id: "loop-1",
    current_round: 2,
    mvp_spec: null,
    validation: null,
    prototype: null,
    test_report: null,
    scorecard: null,
    rounds: [],
  };
  const historical = round(1, 72, "iterate", [artifact("mvp_spec", "a1")]);
  const tabs = loopView.artifactTabs(snapshot, historical);
  assert.equal(tabs.find((tab) => tab.kind === "mvp_spec").available, true);
  assert.equal(tabs.find((tab) => tab.kind === "validation").available, false);
  assert.equal(loopView.defaultArtifactTab(snapshot, historical), "mvp_spec");
});

test("loop 页：有轮次切换、按轮取产物与「对比上一轮」入口", () => {
  const source = read("app/session/[id]/loop/page.tsx");
  assert.match(source, /selectRound\(/, "选中轮次必须经 selectRound 回落");
  assert.match(source, /roundSwitcherOptions\(/, "轮次切换条");
  assert.match(source, /对比上一轮/);
  assert.match(source, /compareRounds\(/);
  assert.match(source, /getLoopRound\(/, "历史轮产物必须按轮读取");
  assert.match(source, /round\.artifacts|selectedRound\.artifacts/, "产物面板按轮取 artifacts");
  assert.match(source, /轮产物/, "产物标题标注轮次");
});

// --------------------------------------------------------------------------- //
// ⑤ 脑暴失败后的重试与「开始讨论」恢复（P2-1 / CHAT-01）
// --------------------------------------------------------------------------- //

test("ChatPanel：错误条在脑暴失败时给出「重试上一步」并复用 retryFailedRun", () => {
  const source = read("components/chat/ChatPanel.tsx");
  assert.match(source, /重试上一步/);
  assert.match(source, /retryFailedRun\(/);
  assert.match(source, /run\.action === "brainstorm"/);
});

test("ChatPanel：失败 Run 不再永久隐藏「开始讨论」", () => {
  const source = read("components/chat/ChatPanel.tsx");
  const declaration = between(source, "const hasBrainstormRun", "const showStartDiscussion");
  assert.match(declaration, /run\.action === "brainstorm"/);
  assert.match(
    declaration,
    /status\s*!==\s*"failed"|!==\s*"failed"/,
    "hasBrainstormRun 必须排除 failed 的 Run"
  );
});
