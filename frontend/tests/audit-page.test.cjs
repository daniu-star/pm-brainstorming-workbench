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

  // PRD-08：结束审计只在 active 开放；created/opening 仍可取消（后端允许 →cancelled），
  // 但「结束并生成报告」不可用，避免点击后得到「非法审计状态迁移」。
  assert.equal(view.canControlAudit(view.normalizeAudit({ id: "a_active", status: "active" })), true);
  assert.equal(view.canEndAudit(view.normalizeAudit({ id: "a_active", status: "active" })), true);
  for (const status of ["opening", "created"]) {
    const preparing = view.normalizeAudit({ id: `a_${status}`, status });
    assert.equal(view.canControlAudit(preparing), true, `${status} 仍允许取消`);
    assert.equal(view.canEndAudit(preparing), false, `${status} 不应允许结束`);
    assert.equal(view.isAuditFinished(preparing), false);
  }
  assert.equal(view.canControlAudit(null), false);
  assert.equal(view.canEndAudit(null), false);
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
  // DX-02：超时与网络失败区分开，页面错误条能显示具体原因
  assert.equal(view.auditErrorText("TIMEOUT"), "请求超时，请稍后重试");
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

// ---------------------------------------------------------------------------
// 6. pending_continuation：上一问处理失败后的「重试继续 / 结束审计」
// ---------------------------------------------------------------------------
test("normalizeAudit 映射 pending_continuation，缺失或非布尔按 false（不臆造失败态）", () => {
  const failed = view.normalizeAudit({
    id: "aud_cont_1",
    status: "active",
    pending_continuation: true,
    active_question_id: null,
    questions: [{ id: "q_1", ordinal: 1, answer_message_id: "m_1" }],
  });
  assert.equal(failed.pendingContinuation, true);

  const normal = view.normalizeAudit({ id: "aud_cont_2", status: "active" });
  assert.equal(normal.pendingContinuation, false);

  // 后端 JSON 里若是字符串 "true" 也不当成 true，避免误报「继续失败」
  const stringy = view.normalizeAudit({ id: "aud_cont_3", status: "active", pending_continuation: "true" });
  assert.equal(stringy.pendingContinuation, false);
});

test("canRetryContinuation 只在 active + pending_continuation + 无当前问题时为 true", () => {
  const base = { id: "aud_retry", status: "active", pending_continuation: true, questions: [] };
  const retry = view.normalizeAudit(base);
  assert.equal(view.canRetryContinuation(retry, false), true);
  // 有当前问题时应正常作答，不显示「重试继续」
  assert.equal(view.canRetryContinuation(retry, true), false);

  for (const status of ["closing", "completed", "cancelled", "failed", "created"]) {
    const audit = view.normalizeAudit({ ...base, status });
    assert.equal(view.canRetryContinuation(audit, false), false, `${status} 不应显示重试继续`);
  }

  // pending_continuation 缺失 → 走原来的「等待审计收尾 / 暂无待回答的问题」
  const noFlag = view.normalizeAudit({ id: "aud_retry_none", status: "active", questions: [] });
  assert.equal(view.canRetryContinuation(noFlag, false), false);
  assert.equal(view.canRetryContinuation(null, false), false);
});

test("CLOSING_POLL_DELAYS_MS：退避递增且累计不超过 60s", () => {
  const delays = view.CLOSING_POLL_DELAYS_MS;
  assert.ok(Array.isArray(delays) && delays.length > 0);
  assert.deepEqual([...delays], [1000, 2000, 4000, 8000, 16000, 29000]);
  for (let index = 1; index < delays.length; index += 1) {
    assert.ok(delays[index] > delays[index - 1], "退避间隔必须递增");
  }
  const total = delays.reduce((sum, ms) => sum + ms, 0);
  assert.equal(total, 60000);
  assert.ok(total <= 60000, "轮询上限 60s");
});

// ---------------------------------------------------------------------------
// 7. 审计页关键文案：失败继续 / 收尾等待
// ---------------------------------------------------------------------------
test("审计页文案常量与产品口径一致（失败继续、收尾可刷新）", () => {
  assert.equal(view.CONTINUATION_NOTICE_TEXT, "上一问处理失败，可重试继续或结束审计");
  assert.equal(view.CLOSING_NOTICE_TEXT, "正在生成报告，可稍候或点刷新");
});

// ---------------------------------------------------------------------------
// 8. 快照读取失败 vs 「确实没有审计」：读取失败不得渲染成「开始审计」
// ---------------------------------------------------------------------------
test("resolveAuditEntryState：读取 500 时只渲染错误态 + 重试，canStart=false", () => {
  const state = view.resolveAuditEntryState({
    auditId: "a_fail",
    hasAudit: false,
    auditError: { message: "服务器内部错误", notFound: false },
    auditLoading: false,
    sessionLoading: false,
    legacyMode: false,
    starting: false,
  });
  assert.equal(state.showLoadError, true);
  assert.equal(state.showStartPanel, false, "读取失败时不能显示「开始审计」");
  assert.equal(state.canStart, false, "读取失败时不能点「开始审计」");
  assert.equal(state.loadErrorText, "服务器内部错误");
  assert.equal(state.showLoading, false);
});

test("resolveAuditEntryState：确认 404 后才允许「开始审计」", () => {
  const state = view.resolveAuditEntryState({
    auditId: "a_missing",
    hasAudit: false,
    auditError: { message: "审计不存在", notFound: true },
    auditLoading: false,
    sessionLoading: false,
    legacyMode: false,
    starting: false,
  });
  assert.equal(state.showLoadError, false);
  assert.equal(state.showStartPanel, true);
  assert.equal(state.canStart, true);
});

test("resolveAuditEntryState：没有 auditId 直接进入开始审计；加载中显示占位", () => {
  const fresh = view.resolveAuditEntryState({
    auditId: null,
    hasAudit: false,
    auditError: null,
    auditLoading: false,
    sessionLoading: false,
    legacyMode: false,
    starting: false,
  });
  assert.equal(fresh.showStartPanel, true);
  assert.equal(fresh.canStart, true);

  const loading = view.resolveAuditEntryState({
    auditId: "a_loading",
    hasAudit: false,
    auditError: null,
    auditLoading: true,
    sessionLoading: false,
    legacyMode: false,
    starting: false,
  });
  assert.equal(loading.showLoading, true);
  assert.equal(loading.showStartPanel, false, "快照未回来前不能先闪一下「开始审计」");
  assert.equal(loading.canStart, false);
});

test("resolveAuditEntryState：sessionLoading / legacy / starting 时禁止开始审计", () => {
  const base = {
    auditId: null,
    hasAudit: false,
    auditError: null,
    auditLoading: false,
    sessionLoading: false,
    legacyMode: false,
    starting: false,
  };
  assert.equal(view.resolveAuditEntryState({ ...base, sessionLoading: true }).canStart, false);
  assert.equal(view.resolveAuditEntryState({ ...base, legacyMode: true }).canStart, false);
  assert.equal(view.resolveAuditEntryState({ ...base, starting: true }).canStart, false);
});

test("AUDIT_LOAD_ERROR_TEXT 文案包含「读取失败」与「重试」", () => {
  assert.match(view.AUDIT_LOAD_ERROR_TEXT, /读取失败/);
  assert.match(view.AUDIT_LOAD_ERROR_TEXT, /重试/);
  assert.match(view.AUDIT_LOADING_TEXT, /读取/);
});

// ---------------------------------------------------------------------------
// 9. closing 超时 / 失败恢复入口
// ---------------------------------------------------------------------------
test("resolveClosingRecoveryState：轮询耗尽显示超时重试，未耗尽保持原提示", () => {
  const exhausted = view.resolveClosingRecoveryState({
    status: "closing",
    pollExhausted: true,
    hasFailedRun: false,
  });
  assert.equal(exhausted.showRecovery, true);
  assert.equal(exhausted.noticeText, view.CLOSING_TIMEOUT_TEXT);
  assert.match(exhausted.noticeText, /超时/);
  assert.match(exhausted.noticeText, /重试/);

  const waiting = view.resolveClosingRecoveryState({
    status: "closing",
    pollExhausted: false,
    hasFailedRun: false,
  });
  assert.equal(waiting.showRecovery, false);
  assert.equal(waiting.noticeText, view.CLOSING_NOTICE_TEXT);

  // 已终结的审计不再显示收尾恢复入口
  assert.equal(
    view.resolveClosingRecoveryState({
      status: "completed",
      pollExhausted: true,
      hasFailedRun: false,
    }).showRecovery,
    false
  );
});

test("resolveClosingRecoveryState：关联 Run 失败显示失败重试，重试目标为 retry-failed", () => {
  const state = view.resolveClosingRecoveryState({
    status: "closing",
    pollExhausted: false,
    hasFailedRun: true,
  });
  assert.equal(state.showRecovery, true);
  assert.equal(state.noticeText, view.CLOSING_FAILED_TEXT);
  assert.match(state.noticeText, /失败/);
  assert.match(state.noticeText, /重试/);
  // 有 failed run 时页面必须调用 retryFailedRun，而不是只刷新快照
  assert.equal(view.resolveClosingRetryTarget("run_failed"), "retry_failed_run");
  assert.equal(view.resolveClosingRetryTarget(null), "refresh_snapshot");
});

// ---------------------------------------------------------------------------
// 10. clarifying 阶段：审计入口必须让路给澄清引导
// ---------------------------------------------------------------------------
test("resolveAuditEntryState：clarifying 不显示「开始审计」，引导先完成澄清", () => {
  const state = view.resolveAuditEntryState({
    auditId: null,
    hasAudit: false,
    auditError: null,
    auditLoading: false,
    sessionLoading: false,
    legacyMode: false,
    starting: false,
    v2Phase: "clarifying",
  });
  assert.equal(state.showStartPanel, false);
  assert.equal(state.canStart, false);
  assert.equal(state.showNotReady, true);
  assert.equal(state.showLoading, false);
  assert.match(state.notReadyText, /澄清/);
  assert.match(state.notReadyText, /审计/);
});

test("resolveAuditEntryState：brainstorming 可开始；旧调用不传 v2Phase 行为不变", () => {
  const base = {
    auditId: null,
    hasAudit: false,
    auditError: null,
    auditLoading: false,
    sessionLoading: false,
    legacyMode: false,
    starting: false,
  };
  const brainstorming = view.resolveAuditEntryState({ ...base, v2Phase: "brainstorming" });
  assert.equal(brainstorming.showStartPanel, true);
  assert.equal(brainstorming.canStart, true);
  assert.equal(brainstorming.showNotReady, false);

  const legacyCaller = view.resolveAuditEntryState(base);
  assert.equal(legacyCaller.showStartPanel, true, "旧调用（无 v2Phase）保持原行为");

  // 已确认 404 但仍在 clarifying：同样不应放行「开始审计」
  const notFoundClarifying = view.resolveAuditEntryState({
    ...base,
    auditId: "a_missing",
    auditError: { message: "审计不存在", notFound: true },
    v2Phase: "clarifying",
  });
  assert.equal(notFoundClarifying.showNotReady, true);
  assert.equal(notFoundClarifying.showStartPanel, false);
});

test("AUDIT_NOT_READY 错误码映射为「先完成澄清」文案", () => {
  assert.match(view.auditErrorText("AUDIT_NOT_READY"), /澄清/);
  assert.match(view.auditErrorText("AUDIT_NOT_READY"), /审计/);
});

// ---------------------------------------------------------------------------
// UX-D3：会话快照未加载时不得放行「开始审计」
// ---------------------------------------------------------------------------
test("resolveAuditEntryState：sessionLoaded=false 时 canStart=false（会话状态未加载）", () => {
  const base = {
    auditId: null,
    hasAudit: false,
    auditError: null,
    auditLoading: false,
    sessionLoading: false,
    legacyMode: false,
    starting: false,
    v2Phase: "brainstorming",
  };
  const unloaded = view.resolveAuditEntryState({ ...base, sessionLoaded: false });
  assert.equal(unloaded.showStartPanel, true, "仍展示开始审计区块（含重新加载入口）");
  assert.equal(unloaded.canStart, false, "会话状态未加载时按钮必须禁用");

  // 旧调用不传 sessionLoaded 视为已加载，保持既有行为
  assert.equal(view.resolveAuditEntryState({ ...base }).canStart, true);
  assert.equal(view.resolveAuditEntryState({ ...base, sessionLoaded: true }).canStart, true);
});

// ---------------------------------------------------------------------------
// PROD-07：进度分母恒 18 会误导（六维都有结论即收尾，目标 12 问）
// ---------------------------------------------------------------------------
test("auditProgressLabel：显示六维覆盖 k/6，并把 18 标成上限而非目标", () => {
  const states = [
    { key: "problem_validity", assessment: "supported" },
    { key: "solution_effectiveness", assessment: "supported" },
    { key: "technical_risk", assessment: "gap" },
    { key: "business_loop", assessment: "gap" },
    { key: "user_adoption", assessment: "not_applicable" },
    { key: "execution_risk", assessment: "supported" },
  ];
  const label = view.auditProgressLabel(6, states);
  assert.ok(label.includes("已回答 6 问"));
  assert.ok(label.includes("六维覆盖 6/6"));
  assert.ok(label.includes("上限 18 问"));
  assert.ok(label.includes("即收尾"));
});

test("auditProgressLabel：未回答维度不计入覆盖，answered 非法值归零", () => {
  const states = [
    { key: "problem_validity", assessment: "supported" },
    { key: "solution_effectiveness", assessment: "awaiting_answer" },
    { key: "technical_risk", assessment: "not_discussed" },
    { key: "business_loop", assessment: "needs_clarification" },
  ];
  const label = view.auditProgressLabel(2, states);
  assert.ok(label.includes("已回答 2 问"));
  assert.ok(label.includes("六维覆盖 1/6"));
  assert.ok(view.auditProgressLabel(NaN, null).includes("已回答 0 问"));
  assert.ok(view.auditProgressLabel(-1, undefined).includes("六维覆盖 0/6"));
});

// ---------------------------------------------------------------------------
// DX-06 / DX-07：空态文案按阶段分支 + 错误分类去文案耦合
// ---------------------------------------------------------------------------
test("shouldResyncAuditAfterError 只按 error.code 判定是否需要重新同步（DX-07）", () => {
  assert.equal(view.shouldResyncAuditAfterError(null), false);
  assert.equal(view.shouldResyncAuditAfterError(undefined), false);
  assert.equal(view.shouldResyncAuditAfterError("AUDIT_NOT_READY"), false);
  assert.equal(view.shouldResyncAuditAfterError("VERSION_CONFLICT"), true);
  assert.equal(view.shouldResyncAuditAfterError("AUDIT_VERSION_CONFLICT"), true);
});

test("resolveAuditEntryState：auditing 阶段缺审计记录时不再提示「先完成澄清」（DX-06）", () => {
  const base = {
    auditId: null,
    hasAudit: false,
    auditError: null,
    auditLoading: false,
    sessionLoading: false,
    legacyMode: false,
    starting: false,
  };

  const auditing = view.resolveAuditEntryState({ ...base, v2Phase: "auditing" });
  assert.equal(auditing.showNotReady, true);
  assert.match(auditing.notReadyText, /重新进入审计/);
  assert.doesNotMatch(auditing.notReadyText, /澄清/);

  // clarifying 保持原文案（审计入口确实要让路给澄清引导）
  const clarifying = view.resolveAuditEntryState({ ...base, v2Phase: "clarifying" });
  assert.match(clarifying.notReadyText, /澄清/);
});

// ---------------------------------------------------------------------------
// 12. 失败审计：end_reason 不再折叠成 completed（DX-R3-01）
// ---------------------------------------------------------------------------
test("normalizeAudit 保留失败 end_reason 原文，不折叠成正常结束", () => {
  const audit = view.normalizeAudit({
    id: "aud_failed",
    status: "failed",
    end_reason: "MODEL_OUTPUT_INVALID",
    questions: [],
  });
  assert.equal(audit.endReason, null, "未知失败码不能被当成 EndReason");
  assert.equal(audit.endReasonRaw, "MODEL_OUTPUT_INVALID");
  assert.notEqual(view.endReasonLabel(audit.endReasonRaw), "正常结束");
  assert.match(view.endReasonLabel(audit.endReasonRaw), /MODEL_OUTPUT_INVALID/);
});

test("resolveAuditFailureRecoveryState 失败态给出原因与恢复指引", () => {
  const failed = view.resolveAuditFailureRecoveryState({
    status: "failed",
    endReasonRaw: "MODEL_OUTPUT_INVALID",
    hasFailedRun: true,
    runErrorText: "开场问题未能通过协议校验，审计未启动",
  });
  assert.equal(failed.isFailed, true);
  assert.equal(failed.showRecovery, true);
  assert.match(failed.reasonText, /协议校验/);
  assert.match(failed.hintText, /重试/);

  // 没有 Run 文案时回退到原始 end_reason 标签
  const rawOnly = view.resolveAuditFailureRecoveryState({
    status: "failed",
    endReasonRaw: "MODEL_OUTPUT_INVALID",
    hasFailedRun: false,
  });
  assert.match(rawOnly.reasonText, /MODEL_OUTPUT_INVALID/);

  const active = view.resolveAuditFailureRecoveryState({ status: "active", hasFailedRun: false });
  assert.equal(active.isFailed, false);
  assert.equal(active.showRecovery, false);
});
