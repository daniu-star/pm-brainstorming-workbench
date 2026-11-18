/**
 * M3 审计页纯逻辑测试（node --test，无第三方 runner）。
 *
 * 被测模块：frontend/lib/auditView.ts（经 tsconfig.test.json 编译到
 * node_modules/.cache/test-build/lib/auditView.js）。故意不渲染 React 组件：
 * 本机 npm registry 不可达，不能安装 @testing-library / vitest，
 * 因此把「页面里所有会写错文案/进度/兜底的判断」都抽到纯函数并在此覆盖。
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");

const view = require("../node_modules/.cache/test-build/lib/auditView.js");

// ---------------------------------------------------------------------------
// 1. 六维与 assessment 中文标签
// ---------------------------------------------------------------------------
test("六维 key 全部有中文标签，未知 key 回退原样", () => {
  const dimensions = [
    "problem_validity",
    "solution_effectiveness",
    "technical_risk",
    "business_loop",
    "user_adoption",
    "execution_risk",
  ];
  assert.equal(view.dimensionLabel("problem_validity"), "问题真实性");
  assert.equal(view.dimensionLabel("solution_effectiveness"), "方案有效性");
  assert.equal(view.dimensionLabel("technical_risk"), "技术风险");
  assert.equal(view.dimensionLabel("business_loop"), "商业闭环");
  assert.equal(view.dimensionLabel("user_adoption"), "用户采用");
  assert.equal(view.dimensionLabel("execution_risk"), "执行风险");
  // 六个都不允许出现英文 key 或 undefined
  for (const key of dimensions) {
    const label = view.dimensionLabel(key);
    assert.notEqual(label, key);
    assert.equal(typeof label, "string");
    assert.ok(label.length > 0);
  }
  // 未知 key：原样返回（仍是字符串，不是 undefined/空）
  assert.equal(view.dimensionLabel("mystery_dimension"), "mystery_dimension");
  assert.equal(view.dimensionLabel(null), "未知维度");
});

test("7 种 assessment 全部有中文标签且有对应徽标样式", () => {
  const expected = {
    not_discussed: "未讨论",
    awaiting_answer: "待回答",
    needs_clarification: "需澄清",
    supported: "已支撑",
    gap: "存在缺口",
    not_applicable: "不适用",
  };
  for (const [key, label] of Object.entries(expected)) {
    assert.equal(view.ASSESSMENT_LABELS[key], label);
    assert.equal(view.assessmentLabel(key), label);
    assert.equal(typeof view.assessmentClass(key), "string");
    assert.ok(view.assessmentClass(key).length > 0);
  }
  // 未知 assessment 回退到「未讨论」而不是裸露英文码
  assert.equal(view.assessmentLabel("bogus"), "bogus");
  assert.equal(view.assessmentLabel(undefined), "未讨论");
});

// ---------------------------------------------------------------------------
// 2. 审计快照归一化（后端额外返回 questions，contracts 里没有）
// ---------------------------------------------------------------------------
test("normalizeAudit 读取 questions 并按 ordinal 排序", () => {
  const raw = {
    id: "aud_1",
    session_id: "sess_1",
    status: "active",
    product_version: 3,
    audit_version: 2,
    question_count: 3,
    active_question_id: "q_2",
    dimension_states: [],
    report: null,
    created_at: "2026-11-19T00:00:00Z",
    updated_at: "2026-11-19T00:00:00Z",
    questions: [
      {
        id: "q_3",
        ordinal: 3,
        dimension: "business_loop",
        question_text: "第三个问题",
        assessment: "not_discussed",
        answer_message_id: null,
      },
      {
        id: "q_1",
        ordinal: 1,
        dimension: "problem_validity",
        question_text: "第一个问题",
        assessment: "supported",
        answer_message_id: "m_1",
      },
      {
        id: "q_2",
        ordinal: 2,
        dimension: "technical_risk",
        question_text: "",
        assessment: "awaiting_answer",
        answer_message_id: null,
      },
    ],
  };

  const audit = view.normalizeAudit(raw);
  assert.ok(audit);
  assert.equal(audit.id, "aud_1");
  assert.equal(audit.status, "active");
  assert.equal(audit.statusLabel, "进行中");
  assert.equal(audit.productVersion, 3);
  assert.deepEqual(
    audit.questions.map((question) => question.id),
    ["q_1", "q_2", "q_3"]
  );
  assert.equal(audit.questions[0].ordinalText, "第 1 / 18 问");
  assert.equal(audit.questions[0].dimensionLabel, "问题真实性");
  // question_text 为空 → hasText=false，页面显示「（问题文本未生成）」
  assert.equal(audit.questions[1].hasText, false);
  assert.equal(audit.questions[2].dimensionLabel, "商业闭环");
});

test("normalizeAudit 对垃圾输入返回 null 而不是伪审计", () => {
  assert.equal(view.normalizeAudit(null), null);
  assert.equal(view.normalizeAudit(undefined), null);
  assert.equal(view.normalizeAudit("oops"), null);
  assert.equal(view.normalizeAudit([]), null);
  assert.equal(view.normalizeAudit({}), null); // 没有 id
});

test("normalizeReport 缺字段时列进 missingFields，不编造内容", () => {
  const report = view.normalizeReport({
    question_count: 4,
    end_reason: "completed",
    supported_points: [],
    unresolved_gaps: [],
    recommended_experiments: [],
    limitations: "   ",
    dimension_states: [],
    human_revised: false,
    generated_by: "fallback",
  });

  assert.ok(report);
  assert.equal(report.hasReports, false);
  assert.equal(report.endReasonLabel, "正常结束");
  assert.equal(report.generatedBy, "fallback");
  assert.deepEqual(report.missingFields, [
    "支持论据",
    "未解决缺口",
    "建议实验",
    "局限性说明",
    "维度评估",
  ]);
  assert.equal(report.limitations, "");
  assert.equal(view.NOT_GENERATED, "未生成");
});

test("normalizeReport 完整报告 missingFields 为空", () => {
  const report = view.normalizeReport({
    question_count: 6,
    end_reason: "completed",
    supported_points: ["论据一"],
    unresolved_gaps: ["缺口一"],
    recommended_experiments: ["实验一"],
    limitations: "样本有限",
    dimension_states: [
      { key: "technical_risk", assessment: "gap", rationale: "缺少压测" },
    ],
    human_revised: true,
    generated_by: "model",
  });

  assert.ok(report);
  assert.equal(report.hasReports, true);
  assert.deepEqual(report.missingFields, []);
  assert.equal(report.humanRevised, true);
  assert.equal(report.dimensionStates[0].label, "技术风险");
  assert.equal(report.dimensionStates[0].assessmentLabel, "存在缺口");
});

test("normalizeReport 对非对象返回 null（页面显示报告未生成）", () => {
  assert.equal(view.normalizeReport(null), null);
  assert.equal(view.normalizeReport("nope"), null);
  assert.equal(view.normalizeReport([]), null);
});

// ---------------------------------------------------------------------------
// 3. 进度 `第 N / 18 问`
// ---------------------------------------------------------------------------
test("questionOrdinalText 输出 `第 N / 18 问`，非法 ordinal 不假装有进度", () => {
  assert.equal(view.questionOrdinalText(1), "第 1 / 18 问");
  assert.equal(view.questionOrdinalText(12), "第 12 / 18 问");
  assert.equal(view.questionOrdinalText(3, 12), "第 3 / 12 问");
  assert.equal(view.questionOrdinalText(0), "第 — / 18 问");
  assert.equal(view.questionOrdinalText(-2), "第 — / 18 问");
  assert.equal(view.questionOrdinalText(Number.NaN), "第 — / 18 问");
  assert.equal(view.AUDIT_QUESTION_HARD_LIMIT, 18);
});

// ---------------------------------------------------------------------------
// 4. 当前问题与提交可用性
// ---------------------------------------------------------------------------
test("pickCurrentQuestion 优先 active_question_id，退回首条未回答", () => {
  const raw = {
    id: "aud_2",
    status: "active",
    active_question_id: "q_2",
    questions: [
      { id: "q_1", ordinal: 1, answer_message_id: "m_1" },
      { id: "q_2", ordinal: 2, answer_message_id: null },
      { id: "q_3", ordinal: 3, answer_message_id: null },
    ],
  };
  const audit = view.normalizeAudit(raw);
  assert.ok(audit);
  assert.equal(view.pickCurrentQuestion(audit).id, "q_2");

  // active_question_id 指向不存在的问题 → 回退到首个未回答
  const fallback = view.normalizeAudit({
    id: "aud_3",
    status: "active",
    active_question_id: "q_missing",
    questions: [
      { id: "q_1", ordinal: 1, answer_message_id: null },
    ],
  });
  assert.equal(view.pickCurrentQuestion(fallback).id, "q_1");

  // 全部已回答 → null
  const done = view.normalizeAudit({
    id: "aud_4",
    status: "active",
    questions: [{ id: "q_1", ordinal: 1, answer_message_id: "m_1" }],
  });
  assert.equal(view.pickCurrentQuestion(done), null);
  assert.equal(view.pickCurrentQuestion(null), null);
});

test("canSubmitAnswer 只在 active 且有当前问题时为 true", () => {
  const active = view.normalizeAudit({
    id: "aud_5",
    status: "active",
    questions: [{ id: "q_1", ordinal: 1, answer_message_id: null }],
  });
  assert.equal(view.canSubmitAnswer(active), true);

  for (const status of ["closing", "completed", "cancelled", "failed", "created"]) {
    const audit = view.normalizeAudit({
      id: `aud_${status}`,
      status,
      questions: [{ id: "q_1", ordinal: 1, answer_message_id: null }],
    });
    assert.equal(view.canSubmitAnswer(audit), false, `${status} 不应允许提交`);
  }
  assert.equal(view.canSubmitAnswer(null), false);
});

test("answeredCount / isAuditFinished / canControlAudit", () => {
  const audit = view.normalizeAudit({
    id: "aud_6",
    status: "completed",
    questions: [
      { id: "q_1", ordinal: 1, answer_message_id: "m_1" },
      { id: "q_2", ordinal: 2, answer_message_id: null },
    ],
  });
  assert.equal(view.answeredCount(audit), 1);
  assert.equal(view.answeredCount(null), 0);
  assert.equal(view.isAuditFinished(audit), true);
  assert.equal(view.canControlAudit(audit), false);

  for (const status of ["active", "opening", "created"]) {
    const open = view.normalizeAudit({ id: `a_${status}`, status });
    assert.equal(view.canControlAudit(open), true, `${status} 应可控制`);
    assert.equal(view.isAuditFinished(open), false);
  }
  assert.equal(view.canControlAudit(null), false);
});

// ---------------------------------------------------------------------------
// 5. 回答校验：1..8000 字，超长截断并提示
// ---------------------------------------------------------------------------
test("validateAnswerInput 拒绝空回答、接受正常回答", () => {
  assert.equal(view.validateAnswerInput("").ok, false);
  assert.equal(view.validateAnswerInput("   ").ok, false);
  assert.equal(view.validateAnswerInput("").error, "请输入回答");
  assert.equal(view.validateAnswerInput("   ").error, "请输入回答");

  const ok = view.validateAnswerInput("  我的回答  ");
  assert.equal(ok.ok, true);
  assert.equal(ok.value, "我的回答");
  assert.equal(ok.error, null);
  assert.equal(ok.warning, null);
  assert.equal(view.MAX_ANSWER_CHARS, 8000);
});

test("validateAnswerInput 超长时截断到 8000 并给出明确提示", () => {
  const long = "字".repeat(8200);
  const result = view.validateAnswerInput(long);
  assert.equal(result.ok, true);
  assert.equal(result.value.length, view.MAX_ANSWER_CHARS);
  assert.equal(result.error, null);
  assert.ok(result.warning);
  assert.ok(result.warning.includes("8000"));
  assert.ok(result.warning.includes("8200"));
});

test("resolveAnswerText 找不到回答消息时返回 null（不编造）", () => {
  const messages = [
    { id: "m_1", content: "已加载的回答" },
    { id: "m_2", content: "   " },
  ];
  assert.equal(view.resolveAnswerText(messages, "m_1"), "已加载的回答");
  assert.equal(view.resolveAnswerText(messages, "m_2"), null);
  assert.equal(view.resolveAnswerText(messages, "m_missing"), null);
  assert.equal(view.resolveAnswerText(messages, null), null);
  assert.equal(view.resolveAnswerText([], "m_1"), null);
});

// ---------------------------------------------------------------------------
// 6. 错误码与控制回执文案（不得静默）
// ---------------------------------------------------------------------------
test("auditErrorText 覆盖后端审计相关错误码的中文文案", () => {
  assert.equal(view.auditErrorText("VERSION_CONFLICT"), "内容已在别处更新，请刷新后重试");
  assert.equal(view.auditErrorText("SESSION_BUSY"), "已有任务正在执行，请稍候再试");
  assert.equal(view.auditErrorText("AUDIT_CLOSED"), "审计已结束，无法继续操作");
  assert.equal(view.auditErrorText("AUDIT_ACTIVE"), "已有进行中的审计，请继续当前审计");
  assert.equal(view.auditErrorText("QUESTION_ALREADY_ANSWERED"), "该问题已经回答过了");
  assert.equal(view.auditErrorText("QUESTION_NOT_ACTIVE"), "该问题不是当前待回答的问题");
  assert.equal(view.auditErrorText("RATE_LIMITED"), "操作过于频繁，请稍后再试");
  assert.equal(view.auditErrorText("INPUT_TOO_LONG"), "回答过长（最多 8000 字）");
  assert.equal(view.auditErrorText("NETWORK_ERROR"), "网络连接失败，请检查网络后重试");
});

test("auditErrorText 对未知错误码回退到后端 message，再回退通用文案", () => {
  assert.equal(view.auditErrorText("WEIRD_CODE", "后端自定义说明"), "后端自定义说明");
  assert.equal(view.auditErrorText("WEIRD_CODE"), "操作失败（WEIRD_CODE）");
  assert.equal(view.auditErrorText(null, null), "操作失败，请稍后重试");
  assert.equal(view.auditErrorText(undefined), "操作失败，请稍后重试");
  assert.equal(view.auditErrorText("VERSION_CONFLICT", "后端原始英文"), "内容已在别处更新，请刷新后重试");
});

test("controlStatusHint：scheduled 必须提示「当前生成结束后处理」", () => {
  assert.equal(view.controlStatusHint("scheduled"), "当前生成结束后处理");
  assert.equal(view.controlStatusHint("applied"), "已立即生效");
  assert.equal(view.controlStatusHint("accepted"), "已受理，稍后处理");
  assert.equal(view.controlStatusHint("failed"), "处理失败");
  assert.equal(view.controlStatusHint(null), "请求已提交");
  assert.equal(view.controlStatusLabel("scheduled"), "排队中");
});

test("describeControlOutcome 区分结束/取消且 scheduled 不谎报已完成", () => {
  assert.equal(
    view.describeControlOutcome("end_audit", "scheduled"),
    "结束审计请求当前生成结束后处理"
  );
  assert.equal(view.describeControlOutcome("cancel_audit", "applied"), "取消审计请求已立即生效");
  assert.equal(view.describeControlOutcome("end_audit", null), "结束审计请求已提交");
});
