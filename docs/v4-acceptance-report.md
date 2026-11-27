# PM Brainstorm V4.0 验收报告（M1–M3 交付）

- 分支：`feat/prd-v4-m1`（基于 `ad77bd2`）
- 提交：`5915020`（基础设施与领域契约）、`f7b4d59`（原子幂等/原子领取/跨进程验收）、`e252410`（检索接入/前端 e2e/README）、`34254fc`（M2 专家收敛 + M3 审计闭环 + 发布件 + 验收报告）、`c0ac89a`（契约同步）、`f4b8f8d`（Windows 一键启动：start.bat/stop.bat）；QA Loop 收口改动见 §7（提交号以 `git log -1` 为准）
- 报告日期：2026-11-19
- PRD：`docs/superpowers/specs/2026-11-18-pm-brainstorm-agent-architecture-prd-v4.md`（V4.0，807 行）
- 本报告**只记录真实执行过的命令与结果**；未实现项标「未实现」，不预填通过、不改期望值迁就现状。

## 1. 工作包状态（§10.2 依赖顺序）

| WP | 内容 | 状态 | 落点 |
| --- | --- | --- | --- |
| WP-00 | 基线回归 | 完成 | 旧测试保留为基线；`test_session_store` 的 200 截断用例标注为「旧 JSON store 已退役」 |
| WP-01 | 领域协议 | 完成 | `backend/domain/{contracts,state_machine,errors}.py`（39+30 用例） |
| WP-02 | 存储与迁移 | 完成 | `backend/repositories/{database,session_repo,run_repo,audit_repo,auth_repo}.py`、`backend/migrations/import_json_sessions.py`（16/19/11/14/12 用例） |
| WP-03 | 持久执行 | 完成 | `backend/workflows/runner.py`、`backend/workers/run_worker.py`、租约/预算/终结/恢复（17+13 用例） |
| WP-04 | 前端与 v1 兼容 | 完成 | `backend/api/v2/*`（21 路由）、`backend/api/sse.py`、旧 4 路由 adapter、`frontend/lib|store|components`（23+7+123 用例） |
| WP-05 | 教练与上下文 | 完成 | `backend/services/{context_builder,canvas_projector}.py`、`backend/core/role_prompts.py`、`backend/workflows/coach.py`（25+23+25 用例） |
| WP-06 | 检索与证据 | 完成 | `backend/rag/{contracts,retriever}.py`（47 用例）+ 已接入 `context_builder`（5 用例，背景知识标「不可引用」） |
| WP-07 | 专家收敛 | 完成 | `backend/services/reducer.py`（34）、`backend/workflows/brainstorm.py`（23）、`backend/workflows/synthesis.py`（696 行；`MAX_REPAIR_ROUNDS=1`/`MAX_REVIEWERS=2`，8 用例）、runner 集成（7） |
| WP-08 | 画布结论 | 完成 | 确定性投影 + Reducer 归并 + 前端 `CanvasPanel`（23 用例） |
| WP-09 | 审计闭环 | 完成 | `backend/workflows/audit.py`（28 用例）、前端审计页（19 用例）、控制意图与 closing Run 链路 |
| WP-10 | 发布闭环 | 完成 | `.github/workflows/ci.yml`、`scripts/v4_migration_drill.py`（演练实跑 DRILL_OK）、`/health/detail` 增加 Worker 诊断、`frontend/tests/e2e` |

## 2. 实际执行的验证命令与结果

```bash
# 后端全量（841 用例，含 M2/M3 + QA Loop 收口 + 闭环/数据生命周期/崩溃注入/旧 ID 别名/AC-14 多失败重试/AC-10 预算预留热修/AC-20 数字守卫/结项报告形状容错/Worker 维护性、中间载荷清理与报告数字标注新增用例）
cd backend && ../.venv/Scripts/python.exe -m unittest discover -s tests
# → Ran 913 tests / OK / exit 0（含 tests/test_llm_config.py 的 11 个「账户自带模型密钥」用例（加密/脱敏 2 + 服务闸门 4 + 接口 3 + 执行器兜底 2）、tests/test_report_quality.py 的 9 个报告质量统计用例（阈值判定 4 + 接口 1 + 窗口回落 4）、tests/test_audit_report_recompute.py 的 3 个「补充来源后读取时重算」用例、tests/test_worker_maintenance.py 的 5 个维护清理用例（幂等记录 7 天窗口、过期验证码、按间隔触发、中间载荷 90 天窗口）、tests/test_payload_retention.py 的 3 个中间载荷保留用例、tests/test_db_maintenance.py 的 3 个增长报告/VACUUM 用例、tests/test_health_detail.py 的 6 个诊断用例（模型层 3 + Worker 卡死 3）、tests/test_audit_report_draft.py 的 5 个报告形状容错用例、tests/test_audit_runner.py 的 3 个真 runner 审计用例、tests/test_control_crash.py 的 3 个真 kill/restart 用例、tests/test_session_alias.py 的 6 个旧 ID 别名用例、tests/test_brainstorm_runner.py 的 AC-14 多失败重试用例、tests/test_budget_crash.py 的 4 个预算预留崩溃用例、tests/test_number_guard.py 的 18 个数字可追溯性用例、tests/test_evidence_guard.py 的 3 个「#seq → 真实消息 ID」规范化用例、tests/test_account_quota.py 的 9 个账户级配额用例（计量口径 2 + 门禁 3 + 接口 3 + 建会话受限 1））

# 跨进程（真 subprocess + 共享 SQLite）
cd backend && ../.venv/Scripts/python.exe -m unittest tests.test_run_repo tests.test_cross_process
# → Ran 26 tests / OK（AC-06 幂等、AC-07 并发、AC-08 双 Worker、AC-23/42 审计并发、AC-28 验证码/限流）

# 崩溃注入 / 取消接管（独立进程 + 真 kill）
cd backend && ../.venv/Scripts/python.exe -m unittest tests.test_control_crash tests.test_crash_atomicity
# → Ran 4 tests / OK（AC-36 kill/restart、AC-41 审计上下文 409、AC-30 取消接管、AC-09 崩溃原子性）

# 旧会话 ID 别名表（PRD §8.1：12 hex 旧链接 → 32 hex 当前会话）
cd backend && ../.venv/Scripts/python.exe -m unittest tests.test_session_alias
# → Ran 6 tests / OK（迁移写别名、旧链接重定向 200、未知 ID 404、他人会话 403、软删后 404、幂等与参数校验）

# 前端（本机 registry.npmjs.org 不可达，用既有 node_modules）
cd frontend && npx tsc --noEmit      # exit 0
cd frontend && npm test              # 334/334 pass（tsc -p tsconfig.test.json + tsconfig.render.json + node --test）
cd frontend && npm run build         # exit 0，12 条路由（/settings/access 4.41 kB、/ops 5.01 kB、/settings/team 4.66 kB、/session/[id]/loop 15.4 kB）
cd frontend && npm run test:e2e      # 1 pass（真起 uvicorn，401/404 语义断言）

# 真 Worker + 假 LLM 端到端（独立进程，走生产 runner/仓储/命令服务）
.venv/Scripts/python.exe backend/.tmp/e2e_audit.py
# → ALL PASS（30 项）：开场 active/ordinal=1/维度合法；回答后 question_count=2、revision 落库；
#   end_audit 建 closing Run → report_json + report revision → audit=completed、会话 audit_completed；
#   取消路径 audit=cancelled 且不写报告、会话回 brainstorming；完成后回答 409 AUDIT_CLOSED

# 迁移发布演练（只读复制源文件 + 临时库）
../.venv/Scripts/python.exe scripts/v4_migration_drill.py
# → DRILL_OK：dry-run scanned=19 planned={sessions:19,messages:65,product_states:19,
#   legacy_audits:1,legacy_artifacts:11} 且不建库；apply 计数一致；重复 apply skipped=19；
#   删库重迁 identical=True；源 19 文件 sha256 全部不变

# 真实浏览器 E2E（CDP + 本机 Chrome，零新依赖；前置：start.bat 已拉起 8001/3001）
cd backend && ../.venv/Scripts/python.exe ../scripts/browser_e2e/run_browser_e2e.py
# → [PASS] AC-01 / [PASS] AC-19 / [PASS] AC-31，failures=[]，cleanup=3（种子会话硬删）
#   截图 docs/browser-qa/e2e/ac-01-refresh-x3.png、ac-19-eight-branches.png、
#   ac-31-account-a.png、ac-31-account-b.png

# 语音/摄像头降级场景（AC-33；会触发一次真实模型生成审计开场问题，约 6–8K tokens）
cd backend && ../.venv/Scripts/python.exe ../scripts/browser_e2e/run_browser_e2e.py AC-33
# → [PASS] AC-33，elapsed_seconds≈18，cleanup=1（种子会话硬删）

# 迁移回滚演练（AC-34；生产库只读快照到临时副本，模拟坏构建后回滚，不碰 backend/data/app.db）
cd backend && ../.venv/Scripts/python.exe ../scripts/ac34_rollback_drill.py
# → ROLLBACK_DRILL_OK：注入的排队任务回滚后仍可被领取、会话/消息/任务计数不变、
#   integrity_check=ok 且外键无悬挂、data/sessions JSON 指纹不变（无 JSON 双写）
#   截图 docs/browser-qa/e2e/ac-33-camera-denied.png、ac-33-voice-playing.png、
#   ac-33-text-answer.png

# AC-20 数字可追溯性端到端（真模型四角色 brainstorm，约 25K tokens；生成 docs/ac20-number-provenance.md）
cd backend && ../.venv/Scripts/python.exe ../scripts/ac20_number_provenance.py
# → run_status=succeeded，19 条论断，7 个画布节点被标注「未验证数字」（50/65/70/80/30/60/75/2/3/5/10）
#   有来源数字 12/6/4 未被标注；人工核对结论见 docs/ac20-number-provenance.annotation.md
```

## 3. 逐条验收（§11 AC-01 … AC-42）

| AC | 状态 | 证据（可执行用例） | 说明 / 偏差 |
| --- | --- | --- | --- |
| AC-01 | 通过（真实浏览器） | `scripts/browser_e2e/run_browser_e2e.py::scenario_ac01`（CDP 驱动真 Chrome，打开会话页后 `reload()` ×3 并比对库状态）+ 截图 `docs/browser-qa/e2e/ac-01-refresh-x3.png`；`frontend/tests/store.test.cjs`（createOnceGuard、按 message_id 去重） | 刷新 3 次后：原始用户消息在页面上恰好 1 条、库内 `messages=2`、`runs=1`、`session_version` 与 `product_version` 均未推进；无未捕获页面异常 |
| AC-02 | 通过 | `test_context_builder.py::test_sections_follow_the_declared_order`、`test_current_message_is_not_duplicated_in_conversation`；`test_coach.py::test_valid_output_produces_question_and_proposed_brief` | 注入含原问题、前问、Brief、当前输入且只出现一次 |
| AC-03 | 通过 | `test_command_service.py::test_confirm_brief_advances_to_brainstorming`、`test_skip_clarification_marks_brief_skipped` | 确认/跳过只建 1 条同步 Run，不自动触发四角色 |
| AC-04 | 通过（v2 路径） | `test_session_repo.py::test_1000_messages_paging_keeps_history`、`test_limit_is_clamped` | v2 仓储无截断、seq 单调、投影含新主张 |
| AC-05 | 通过 | `test_brainstorm.py`（role_code 必须等于当前角色、`extra=forbid`、修复仅一次、两次非法→MODEL_OUTPUT_INVALID）；`test_contracts.py`（RoleResult 上限）；`tests/test_evidence_guard.py`(7)（逐条回查 evidence/message 归属）；`test_brainstorm_runner.py::test_foreign_evidence_ref_fails_after_one_repair`、`::test_session_evidence_and_messages_are_referenceable` | 协议 + 归属双校验：越权引用先回喂修复一次，仍失败 → MODEL_OUTPUT_INVALID，绝不混入他会话引用 |
| AC-06 | 通过（跨进程） | `test_cross_process.py::test_same_request_id_in_two_processes_creates_one_run`；`test_command_service.py::test_same_request_id_with_different_body_conflicts` | 同键两进程 → 同 run/reused 一真一假/消息 seq [1,2]；异体同键 409 |
| AC-07 | 通过（跨进程） | `test_cross_process.py::test_concurrent_different_keys_same_session_gets_session_busy` | 一成一败（SESSION_BUSY/VERSION_CONFLICT），messages=2、runs=1 |
| AC-08 | 通过（跨进程） | `test_cross_process.py::test_two_worker_processes_claim_the_same_run_once`；`test_run_repo.py::test_old_worker_submit_after_takeover_is_rejected`；`test_runner.py::test_succeeded_step_is_skipped_without_model_call` | 条件 UPDATE 原子领取、epoch 递增、旧提交被拒、成功步骤不重跑 |
| AC-09 | 通过（崩溃注入） | `test_run_repo.py::test_event_seq_is_monotonic_and_unique`；`test_runner.py::test_succeeded_step_is_skipped_without_model_call`；`tests/test_crash_atomicity.py`（子进程 `os._exit(9)` 真崩溃注入：步骤成功与结果同事务，重启后无 succeeded-无结果残留） | 步骤幂等 + 事件可回放 + 崩溃后不产生假成功 |
| AC-10 | 通过（崩溃注入） | `test_runner.py::test_retryable_error_two_attempts_then_run_failed`；`test_command_service.py::test_apply_outcome_twice_applies_only_once`；`test_llm_client.py::test_sdk_retries_disabled`；`tests/test_budget_crash.py`（子进程 `os._exit(9)` 真崩溃：`BudgetedLLM` 在发请求前把预留写进 `run.usage_json`（`reserved_prompt_tokens` / `reserved_completion_tokens`），接管者 `_restore_usage` 把上一 epoch 的预留按已消耗计入 → 崩溃一次 + 重跑一次 `upstream_calls == 2`） | 单步 ≤2 次、结果仅应用一次、预留成本不丢（崩溃后不会从旧账本重算而多花次数与 token） |
| AC-11 | 通过 | `test_api_v2.py::test_sse_replays_from_after_seq_and_closes_on_terminal`；`test_legacy_compat.py::test_brainstorm_sse_translates_events_and_done_once`；`frontend/tests/sse.test.cjs` | 终结事件恰好一次（服务端 + 前端双保险） |
| AC-12 | 通过 | `frontend/tests/sse.test.cjs`（跨块 UTF-8/CRLF/多行 data/注释/意外 EOF）；`test_api_v2.py::test_keepalive_frame_is_comment_without_seq` | 心跳为注释不占序号；EOF 无终结只算断连 |
| AC-13 | 通过 | `frontend/tests/store.test.cjs`（切换会话/generation 守卫）；`test_api_v2.py::test_sse_disconnect_does_not_cancel_run` | 切 A→B 取消订阅但不取消服务器任务 |
| AC-14 | 通过 | `test_brainstorm.py::test_repeated_invalid_output_fails_role_but_run_partial`；`test_brainstorm_runner.py::test_invalid_role_output_marks_partial`；`test_brainstorm_runner.py::test_retry_only_reruns_failed_role_and_reuses_successes`（单失败）；`test_brainstorm_runner.py::test_retry_reruns_all_failed_roles_and_enqueues_synthesis`（一个超时 + 一个结构失败 → partial_succeeded，重试子 Run 只调这两个专家并复用另外两个，成功后同事务排入 synthesis） | 单专家瞬时错误不中止整轮（`LLMUnavailableError` 记该角色 failed、其余继续）；部分成功不自动排 synthesis（避免占住 active_run_id 阻断重试）；`retry_run` 允许在 partial Run 的结果版本上重试；复用角色不重复发对外消息 |
| AC-15 | 通过 | `test_reducer.py::test_merge_is_independent_of_arrival_order`；`test_brainstorm_runner.py`（4 角色各自 `build_context(product_version=base)`，首轮互不可见） | 结果不依赖完成顺序、同基准产品版本 |
| AC-16 | 通过 | `backend/workflows/synthesis.py`（`MAX_REPAIR_ROUNDS=1`、`MAX_REVIEWERS=2`、冲突上限 2、只看冲突相关对方结论、degraded 回退）；`backend/tests/test_synthesis.py`(8)：归并/复核上限/复核专家不可再开复核/降级；`workflows/runner.py` 注册 `TaskKind.synthesis`；brainstorm 成功后由 `command_service._enqueue_follow_up` 同事务排入子 Run | 最多 1 轮修复 + 至多 2 位专家复核，冲突与分歧进 `SynthesisResult`；预算不足时降级并标注 |
| AC-17 | 通过 | `test_context_builder.py::test_protected_content_is_never_dropped_by_budget`；`test_coach.py::test_confirmed_field_is_never_overwritten_by_model` | 受保护字段不因预算丢弃；模型不可改写已确认项 |
| AC-18 | 通过 | `test_canvas_projector.py::test_two_calls_are_byte_identical`、`test_projection_does_not_mutate_state`；`test_reducer.py::test_merge_is_independent_of_arrival_order`；`frontend/tests/store.test.cjs`（acceptProductVersion 单调） | 投影纯函数、零模型调用、旧版本不覆盖；「待合并建议」UI 未单独做（角色结果仅在合并后进画布） |
| AC-19 | 通过（真实浏览器） | `scripts/browser_e2e/run_browser_e2e.py::scenario_ac19`（播种出 8 个分支的产品状态）+ 截图 `docs/browser-qa/e2e/ac-19-eight-branches.png`；`test_canvas_projector.py::test_branch_order_follows_declared_order`、`test_every_node_carries_refs_and_status` | 8 个分支（技术/设计/商业/用户/方案/决策/未决/实验）全部渲染，无「+N 条更多」折叠；第 8 个分支节点原文可见、页面含「v2」版本徽标与「证据/消息」引用计数 |
| AC-20 | 通过（端到端人工标注） | `services/number_guard.py`（`extract_numbers` / `number_is_traceable` / `verification_suggestion` / `audit_role_result`，只标注不否决）；`test_number_guard.py`(20，含报告侧 2)、`test_canvas_projector.py::UnverifiedNumberTests`(2)、`test_brainstorm_runner.py::test_numbers_absent_from_sources_are_marked_unverified`；真模型 E2E `scripts/ac20_number_provenance.py` + `docs/ac20-number-provenance.md`（19 条论断、7 个未验证节点，人工核对）；第 29 批「补充来源」闭环：报告徽标可点开录入证据，`audit_repo._remark_report_numbers` 在读取时按最新消息/证据重算标注（`test_audit_report_recompute.py` 3 例，零模型成本） | 无来源数字 100% 标注为「未验证数字」并给出补来源/统计日期的验证建议（画布橙色徽标 + detail 末行 + `verification_suggestion`），有来源数字（12/6/4）100% 未被误标；论断 `evidence_refs` 全为空数组，未编造引用（零相关时检索返回空列表由 `test_retriever.py` 覆盖） |
| AC-21 | 通过 | `test_audit_workflow.py::test_open_writes_first_question_and_activates_audit`、`test_open_uses_planned_dimension_when_model_disagrees`；`test_audit_runner.py::test_audit_open_via_runner_commits_question_and_activates_audit`；`.tmp/e2e_audit.py` | 开场输入锚定 ProductState，问题含维度与依据 |
| AC-22 | 通过 | `test_audit_workflow.py::test_open_is_idempotent_when_question_already_exists`；`test_api_v2.py::AuditEndpointTests`（真实 `GET /api/v2/audits/{id}` 返回 questions/active_question_id；他会话 403 且不泄漏 questions）；`test_audit_runner.py`（answer 后 active_question_id 指向第 2 问）；前端 `hydrateAuditById` | 同一 audit_id/问题可恢复，不重建、不二次开场 |
| AC-23 | 通过（跨进程） | `test_audit_workflow.py::test_answer_ignored_when_audit_not_active`、`test_answer_rejects_unknown_question`；`test_command_service.py::test_answer_audit_requires_open_question`；`test_cross_process.py::test_concurrent_answers_to_same_question_accept_exactly_one`（两进程真并发：恰好一个 accepted，问题只绑一条回答消息，Run 只多一条） | 单问题只接受一次回答（DB 层原子，跨进程成立） |
| AC-24 | 通过 | `test_audit_workflow.py::test_open_uses_planned_dimension_when_model_disagrees`、`test_plan_next_dimension_prefers_uncovered_then_gaps`、`test_normalize_assessment_downgrades_supported_without_evidence` | 维度由问题指定与规划器决定，不按回答关键词归类；supported 必须有证据否则降级 |
| AC-25 | 通过 | `test_audit_workflow.py::test_answer_at_hard_limit_closes_and_never_asks_nineteenth`、`test_answer_at_question_target_closes_audit`；`test_audit_runner.py::test_closing_run_generates_model_report_and_completes_audit`；`.tmp/e2e_audit.py`（完成后 409 AUDIT_CLOSED） | 第 18 问后直接 closing、无第 19 问、完成后拒绝继续回答 |
| AC-26 | 通过 | `test_audit_workflow.py::test_report_falls_back_when_model_unavailable`、`test_report_recovers_from_failed_audit`、`test_build_fallback_report_marks_limitations`；`test_audit_runner.py::test_closing_run_generates_model_report_and_completes_audit` | 模型可用走 `generated_by="model"`，不可用确定性有限总结（`generated_by="fallback"` + limitations）；`_apply_report_draft` 在无 supported 维度时清空 supported_points 并记 warning（FR 不凭空补齐） |
| AC-27 | 通过 | `test_api_v2.py::test_session_ownership_and_missing`；`test_audit_repo.py::test_require_audit_enforces_ownership_and_existence`；`test_legacy_compat.py::test_legacy_delete_then_all_legacy_routes_404` | v2 与 v1 兼容路径都按 owner 拒绝 |
| AC-28 | 通过（跨进程） | `test_cross_process.py::test_auth_code_and_rate_limit_are_shared_across_processes`；`test_auth_repo.py`(14) | 共享 DB、一次使用、5 次错码、窗口原子 |
| AC-29 | 通过 | `test_command_service.py::test_clarify_rejects_too_long_input`；`test_runner_budget.py`(5)、`test_llm_client.py::test_missing_usage_falls_back_to_estimated`/`test_sdk_retries_disabled` | 8001 字拒绝、第 11 次上游调用拒绝、usage 缺失标 estimated、SDK 重试 0 |
| AC-30 | 通过 | `test_command_service.py::test_cancel_queued_run_terminates_immediately`、`test_cancelled_run_result_cannot_be_committed`；`test_runner.py::test_session_deleted_cancels_run_without_results` | 取消优先、旧结果不能提交、不复活会话 |
| AC-31 | 通过（真实浏览器） | `scripts/browser_e2e/run_browser_e2e.py::scenario_ac31`（点真实「退出登录」按钮后换 Cookie 登录）+ 截图 `docs/browser-qa/e2e/ac-31-account-a.png`、`ac-31-account-b.png`；`frontend/tests/store.test.cjs`（登出后写回被丢弃） | B 登录后历史抽屉只有 B 的会话、页面无 A 的消息；localStorage/sessionStorage 无 A 会话 ID；B 直接打开 A 的会话链接显示「加载失败」且不渲染内容 |
| AC-32 | 通过 | `test_migration_import.py`(12)：dry-run 零写、apply 幂等、源变更 conflict、空 owner 隔离、旧面试 cancelled+归档、源 hash 不变；`scripts/v4_migration_drill.py` 实跑 | 迁移演练 5 阶段全通过 |
| AC-33 | 通过（真实浏览器） | `test_voice.py`(7)；`scripts/browser_e2e/run_browser_e2e.py::scenario_ac33`（真 Chrome：摄像头拒绝→真实原因浮层→「继续仅文字面试」→文字输入可用；TTS 播放中麦克风禁用、切「文字」立即停止播放；麦克风被拒→真实原因浮层；文字回答经 Worker 真实处理后审计推进到第 2 问）；截图 `docs/browser-qa/e2e/ac-33-camera-denied.png`、`ac-33-voice-playing.png`、`ac-33-text-answer.png` | 三种降级路径都在真浏览器验证：引擎说明如实（无服务端 STT 时不显示「AI 高精度转写」）、播放停止、麦克风状态跟随实际音频；TTS 用本地静音 WAV 替换外部语音服务（只替换外部依赖，不伪造被测界面） |
| AC-34 | 通过（回滚演练） | v2 单一写源 + 旧路由 adapter；`test_legacy_compat.py`(7)；`scripts/ac34_rollback_drill.py`（生产库一致性快照 → 临时副本上模拟坏构建 → 回滚） | 切换后消息/任务不丢（注入的排队 Run 回滚后仍被 Worker 领取）、14 个会话全部可读、`integrity_check=ok` 且外键无悬挂、`data/sessions` JSON 指纹前后一致（无 JSON 双写） |
| AC-35 | 通过 | `test_api_v2.py::test_sse_after_seq_zero_replays_from_start`、`test_sse_cursor_expired_only_below_retention_watermark`、`test_sse_invalid_cursor_errors`、`test_run_detail_and_snapshot_single_transaction` | after_seq=0 从头回放、410 仅低于水位、快照单事务 |
| AC-36 | 通过（真 kill/restart） | `test_command_service.py::test_end_audit_schedules_closing_run_when_idle`、`test_cancel_audit_supersedes_pending_end`；`test_control_crash.py::test_end_audit_receipt_survives_worker_kill_and_closing_run_resumes`（真子进程跑到 `audit.answer` 生成中途 `kill()`：结束意图落库 → 旧 Run `cancel_requested` → 租约过期后新 Worker 接管 → 旧生成零写入、唯一 `end_audit` closing Run `succeeded`、审计 `completed`、阶段 `audit_completed`；报告摘要 `summary` 落库并在前端「报告摘要」区展示，模型漏写时用结构化兜底摘要，真模型验证 562 字） | 意图持久 + 旧生成不提交 + 唯一 closing Run 接续 + 最终阶段正确 |
| AC-37 | 通过 | `test_command_service.py::test_retry_rejects_when_product_state_changed` | 409 RETRY_CONTEXT_CHANGED，不混新上下文 |
| AC-38 | 通过 | `test_reducer.py::test_same_local_id_in_different_roles_gets_distinct_server_ids`；`test_run_repo.py::test_reserve_candidate_id_is_stable` | 不同角色 local_id=1 → 服务器 ID 各异且引用一致 |
| AC-39 | 通过 | `test_api_v2.py::test_run_detail_and_snapshot_single_transaction`；`test_migration_import.py::test_changed_source_is_conflict_not_overwrite`；`test_data_lifecycle.py`（软删默认清正文：消息/证据/产品状态/审计问答与报告/闭环产物全清，只留 tombstone 与版本链） | 快照与水位一致、tombstone 阻止复活、删除后在线正文已清除 |
| AC-40 | 通过 | `test_command_service.py`（revise/evidence/correct）；`test_audit_repo.py::test_assessment_revisions_are_append_only`；`test_command_service.py::test_correct_assessment_rebuilds_report_with_human_flag`（人工更正后从结构化记录重建报告、`human_revised=True` 并追加 human 修订）；`test_audit_workflow.py` 断言模型摘要原样落库、兜底摘要含结构化计数（第 18 批） | 同步命令零模型调用、修订可追溯、原报告保留、更正后重建且标记人工修订 |
| AC-41 | 通过 | `test_audit_workflow.py::test_stale_audit_version_skips_writes`；`retry_run` 的 `base_audit_version` 校验 + 新增「`session.current_audit_id != original.audit_id` 即拒绝」守卫（`services/command_service.py`）；`test_control_crash.py::test_retry_rejected_when_newer_audit_same_product_version`（开场失败 → 同产品版本真实 `start_audit` 建新实例 → 重试旧 Run 得 409 `RETRY_AUDIT_CONTEXT_CHANGED`，Run 数不变、旧审计仍 failed） | 审计上下文变化（版本或当前实例）→ 409，不复活旧实例 |
| AC-42 | 通过（跨进程） | `test_command_service.py::test_cancel_audit_supersedes_pending_end`；`test_state_machine.py::test_cancel_supersedes_unfinished_end`；`test_audit_workflow.py::test_report_cancel_takes_priority_over_generation`；`test_cross_process.py::test_end_and_cancel_audit_race_has_single_control_chain`（两进程同时 end/cancel：控制意图 ≤2、end Run ≤1、cancel 生效时 audit 必 cancelled 且无 closing Run） | 取消优先、end 与 cancel 竞争只留一条控制链 |

## 4. 已知偏差（如实列出，含影响与最小调整方案）

1. **主持人 synthesis/复核（AC-16）— 已解决（QA Loop 第 1 轮修复）**：新增 `backend/workflows/synthesis.py`（696 行）实现 `SynthesisResult`、冲突上限 2、「最多 1 轮修复 + 至多 2 位专家复核 + 复核专家不可再开复核」、预算不足 degraded 回退；brainstorm 成功后由 `command_service._enqueue_follow_up` 在同一事务排入 `TaskKind.synthesis` 子 Run；`backend/tests/test_synthesis.py` 8 例覆盖。
2. **重试粒度 — 已解决（第 11 批，AC-14）**：`runner._retry_scope_for` 读子 Run 的 `failed_steps_json`，把父 Run 已成功 step 的 `RoleResult`/staging 候选交给 `brainstorm._reused_role_run` 直接复用，只对失败角色调模型；`brainstorm._run_role` 区分两类上游错误——`retryable=True`（超时/限流/5xx）记为角色级失败并继续其余角色（整轮以 `partial_succeeded` 收口），账号级（401/402 等不可重试、影响所有角色）才整轮失败并保留真实错误码；所有角色都因瞬时错误失败时仍上抛给 runner 做步骤级重试。部分成功不再自动排 synthesis（否则占住 `active_run_id` 让重试撞 `SESSION_BUSY`），重试成功后才由该 Run 排入「必要汇总」；`retry_run` 额外允许 `partial_succeeded` 且 `result_product_version == session.current_product_version` 的 Run 重试（归并按 claim 文本去重）。覆盖 `tests/test_brainstorm_runner.py` 3 例。
3. **越权 evidence_id 归属校验 — 已解决（第 9 批）**：`services/evidence_guard.py::check_role_result_refs` / `assert_role_result_refs` + `session_repo.list_referenceable_evidence_ids` / `list_session_message_ids`，接到 `workflows/brainstorm.py::_validate_role_result`：逐条回查会话已有消息与已登记证据（含公开知识域 `session_id IS NULL`），越权引用先回喂修复一次，仍失败 → `MODEL_OUTPUT_INVALID`。覆盖 `tests/test_evidence_guard.py`(7) 与 `test_brainstorm_runner.py` 2 例。
4. **前端单测仍是 `node --test` 而非 vitest/RTL**：本机 `registry.npmjs.org` 不可达，无法安装新依赖。影响：无交互式组件测试。缓解：① 第 13/14 批新增 `scripts/browser_e2e/`（CDP + 本机 Chrome，零新依赖）提供真实浏览器验收，AC-01/19/31/33 从「store/逻辑层」升级为真浏览器证据；② 第 25 批新增 `frontend/tests/pages-render.test.cjs`，用 `react-dom/server` 直接渲染 **audit / loop 两个页面组件**（替身 `next/navigation`、`next/link`），断言完成态报告摘要/维度评估、读取失败错误态、未加载禁用提示、闭环阶段门与启动面板——页面级条件渲染分支被删会立刻变红。仍缺：点击/输入等交互（由浏览器 e2e 覆盖）。最小调整：网络恢复后补 vitest + RTL。
5. **软删不清正文 — 已解决（第 8 批）**：`repositories/session_repo.py::purge_session_content` 在软删时清空消息正文、证据摘要、产品状态（回骨架但保留版本链）、审计问答与报告、闭环产物，只保留 tombstone 与会话骨架；`soft_delete_session(..., purge_content=True)` 为默认。物理清理仍依赖运维脚本，属保留期策略而非功能缺口。
6. **同步命令也建 Run 行**：`confirm_brief` 等同步命令各建一条 `succeeded` Run 用于回执与幂等审计（有意设计，非缺陷）；跨进程测试按 run_id 而非 `COUNT(*)` 断言。
7. **`MigrationRecord` 无 detail 列**：迁移细节写入 `warnings_json`。12-hex 旧 ID 的旧链接兼容 — 已解决（第 10 批）：`session_id_aliases` 表（`repositories/database.py::SessionIdAliasRow`）由迁移器在重映射时写入，`session_repo.resolve_session_id` 只读解析，`api/v2/common.py::require_owned_session` 在归属校验前解析别名；别名不参与写路径、不放宽权限、软删后同样 404。覆盖 `tests/test_session_alias.py` 6 例。
8. **brainstorm 的 step 事件粒度**：role 步骤不写 `step.started`（只有顶层 `brainstorm.generate` 有，角色完成以 `role.completed` 表达）；预算中止时个别 role step 可能停留 running（run 仍 failed 且恰好一个终结事件）。最小调整：role step 也发 `step.started`，并在 `_force_terminal` 中把残留 running step 标 interrupted。
9. **事件压缩作业 — 已解决（第 8 批）**：`repositories/run_repo.py::compact_events` 只删终态 Run 的旧事件并保留每 Run 最后一个（活动 Run 不动），`workers/run_worker.py` 每小时驱动一次且异常不阻塞任务；`api/sse.py::resolve_start_cursor` 按「第一个仍可回放的 seq」判定 410，`api/v2/runs.py` 取 `max(全局水位, 该 Run 最早保留)`。覆盖 `tests/test_data_lifecycle.py` 5 例。
10. **维护性数据只增不减 — 已解决（第 24 批）**：`idempotency_records`（每条命令一行）与 `auth_codes`（每次登录一行）都没有过期读者；`run_repo.prune_idempotency`（按 `created_at` 清 7 天前）与 `auth_repo.prune_expired_codes`（按 `expires_at < now`）由 Worker 每 6 小时驱动一次，同样失败只记日志不阻塞任务。覆盖 `tests/test_worker_maintenance.py` 4 例。
10. **未登录访问未知 `/api/v2/*` 返回 401 而非 404**：鉴权是中间件，在路由匹配前短路；PRD 未要求，前端已按 401/404 双兼容处理。
11. **审计回答消息由命令层写入**：`command_service._submit_async` 在建 Run 前写 human 消息（stage=audit）并回填 `audit_questions.answer_message_id`，`workflows/audit.py` 只做评估与下一问。这是「用户消息+序号+Run+占位同事务」的必然结果，workflow 再写会双写，属设计说明而非偏差。
12. **`audit_repo` 缺评估修订读取 API**：只有 `add_assessment_revision`，前端暂不需要评估修订历史；将来要展示时由 storage 补 `list_assessment_revisions`（只读）。

13. **推理型模型的 completion 预算问题 — 已解决（第 16 批，AC-20 真模型联调发现）**：`deepseek-flash` 会把 `max_tokens` 先花在 `reasoning_content` 上，导致结构化输出的 `content` 为空（实测 1500 tokens 全为 reasoning、`content=""`、耗时 16s），四角色因此全部 `MODEL_OUTPUT_INVALID`。修复：`Settings.llm_disable_reasoning`（默认 `True`，`LLM_DISABLE_REASONING=false` 可关闭）→ `LLMClient._reasoning_extra_body()` 对含 `deepseek` 的模型发送 `extra_body={"thinking": {"type": "disabled"}}`（实测 completion 1500→380、content 恢复）；同时 `SCHEMA_REMINDER` 在对话末尾复述精确字段名与长度上限（display_text ≤320 字、claims 3–5 条、每条 ≤100 字），把单角色输出压到 ~750–1050 tokens。覆盖 `test_context_builder.py` / `test_brainstorm.py`（48 例）。

14. **供应商不支持 `json_schema` → 自动降级并缓存结论；模型会写 `#seq` 序号引用 — 已解决（第 16 批）**：`https://api.deepseek.com/v1` 对 `response_format={"type": "json_schema"}` 返回 400（`This response_format type is unavailable now`），原实现每次调用都先吃一次 400 再降级，四角色即白花 4 次上游调用并触发 `BUDGET_EXCEEDED`；现 `LLMClient._json_schema_supported` 缓存「该供应商不支持」，同进程后续直接走 `json_object`（`test_llm_client.py::test_json_schema_downgrade_is_cached_per_client`）。另外上下文把消息渲染成 `[#seq]`，模型会把 `#3` 写进 `source_message_ids` 而被归属校验拒绝；现 `evidence_guard.normalize_message_refs` + `session_repo.list_session_message_refs` 把序号确定性解析为本会话真实消息 ID（只映射本会话，映射不到仍拒绝），覆盖 `test_evidence_guard.py::NormalizeMessageRefsTests`(3)。
15. **本机 8000 端口运行旧进程**：手工验证 `/api/v2` 前需重启后端；前端已做 404→legacy 回落。

16. **结项报告曾 100% 落兜底 — 已解决（第 17 批，完整用户旅程真模型联调发现）**：`workflows/audit.py::ReportDraft` 继承 `StrictModel`（`extra="forbid"`），模型常额外输出 `type` 之类的元字段、并把 `limitations` 写成数组，于是 `_structured` 里 `model_validate` 抛 `ValidationError`，回喂一次仍失败 → `build_fallback_report`，真实报告（`supported_points`/`unresolved_gaps`）从不落模型路径（旅程里 `end_audit` 已花 3 次调用、8326 tokens 仍 `generated_by=fallback`）。修复：`ReportDraft` 加 `@model_validator(mode="before")` 有界归一（只保留声明的字段；数组字段接受单字符串/None/list；文本字段接受 list 合并/None/强转 str；长度上限仍由 `max_length` 强制），并在 `_report_instruction` 末尾明确「不要额外字段、`limitations`/`summary` 是单个字符串」。覆盖 `tests/test_audit_report_draft.py` 5 例 + 真实模型验收 `scripts/audit_report_e2e.py`（`generated_by=model`、`unresolved_gaps=9`、成本 8,078 tokens）。

## 5. NFR（§9）抽查

- 事务中不等待模型/网络：runner 提交阶段只做 DB 写入，模型调用在事务外（`test_runner` 失败路径不写脏状态）。
- 单会话单活动 Run：`test_command_service.py::test_second_command_while_run_active_is_session_busy`。
- 幂等保留 30 天：`test_run_repo.py::test_save_then_get_returns_same_record_and_keeps_expiry_epoch`（expires_at 为 epoch 秒）。
- 限额：`MAX_QUEUED_RUNS_PER_USER=3`、`COMMANDS_PER_MINUTE=20`、`MAX_UPSTREAM_CALLS_PER_RUN=10`、`max_output_tokens` 等均有对应用例。
- 可观测性：`/health/detail` 返回 `worker.db/running_runs/queued_runs/last_heartbeat_at/heartbeat_age_seconds/stalled/hint` 与 `llm` 诊断块（模型名/主机名/是否关推理）；DB 不可用、或「有排队任务但 Worker 无心跳」时 `status=degraded` 并给出可执行启动命令；前端聊天区在任务排队超过 45 秒时提示「Worker 可能未启动」（第 20 批）；第 30 批新增 `report_quality` 块（最近 24h 报告里含无来源数字的份数/数字个数/阈值判定 `no_data|insufficient|clean|occasional|frequent` + 处置建议，样本不足时不下结论；第 31 批增加 7 天窗口回落——24h 内样本不足时改用 7 天统计并标注 `fallback_from`，避免安静一天就只能答 `no_data`）。
- 迁移可回退：`scripts/v4_migration_drill.py` 证明 dry-run 零写、apply 幂等、删库重迁一致、源文件零改动。

## 6. 结论

- M1（WP-00～05）、M2（WP-06～08）、M3（WP-09）与 WP-10 的发布件（CI、迁移演练、健康诊断、前端 e2e 冒烟）均已交付。
- 真实执行结果：后端 **913 用例 OK**、会话共享（所有者可把会话授予其它账号 viewer/editor：viewer 只能读，editor 可提交命令但 Run 与模型 Key 仍记在所有者名下；共享只授权不复制数据，撤销即失效；`/sessions/{id}/shares` 增删查，9 用例）、账户级配额（滚动 24 小时窗口，`ACCOUNT_DAILY_TOKEN_LIMIT` / `ACCOUNT_DAILY_CALL_LIMIT`，默认 0=不限；超额返回 429 `ACCOUNT_QUOTA_EXCEEDED`，改稿/登记证据/取消等同步命令不受限；`GET /api/v2/quota` 可查已用与预占，9 用例；前端在「模型 API 设置」页把已用/上限画成进度条，顶栏在设了上限时显示额度徽标，超过 80% 变琥珀色、超额变红并提示仍可做同步操作）、幂等记录与过期验证码由 Worker 每 6 小时清理一次（5 用例，含中间载荷 90 天保留）、`scripts/db_maintenance.py report|vacuum` 可查增长并回收空闲页（3 用例）、前端 **334 用例 pass**（第 41 批新增网络重试 7 例）（含 audit / loop 两页的服务端真实渲染 8 例、证据表单渲染 3 例、额度视图 6 例）+ `tsc` 0 错 + `build` 成功、`test:e2e` 1 pass、真实浏览器 E2E **AC-01/19/31/33 全部 PASS**（CDP，零新依赖，7 张截图）、跨进程 6 用例 OK + 崩溃注入 3 用例 OK、旧 ID 别名 6 用例 OK、AC-14 多失败重试 1 用例 OK、预算预留崩溃 4 用例 OK、真 Worker 审计端到端 **30 项 ALL PASS**、迁移演练 **DRILL_OK**、新构建回滚演练 **ROLLBACK_DRILL_OK**（临时副本上模拟坏构建→回滚：注入的排队任务仍可被领取、消息/任务计数不变、`integrity_check=ok`、`data/sessions` JSON 指纹不变）、AC-20 数字可追溯性真模型端到端（19 条论断中 7 个无来源数字节点全部被标注，人工核对）、完整用户旅程真模型端到端 **53,910 tokens / 18 条论断 / 8 个分支 / 5 个未验证节点**（`scripts/full_journey_e2e.py` → `docs/full-journey-e2e.md`）、结项报告真实模型路径 **`generated_by=model`**（`scripts/audit_report_e2e.py` → `docs/audit-report-e2e.md`）、报告正文里的无来源数字由服务端标注为「数字未验证」并在报告顶部给出补充建议（与画布同一套 `number_guard` 规则）、账户自带模型密钥（BYOK）：平台前期免费但不代付模型费，每个账户需在「模型 API 设置」里填写自己的 `base_url`/`model`/`api_key`（Fernet 加密落库、只回显末 4 位）；未配置时建会话、提交需要模型的命令、Worker 执行 Run 三处均返回 428 `LLM_CONFIG_REQUIRED`，前端整屏引导到配置页（11 用例）；并用真 Chrome（CDP）实测：未配置账户打开首页看到整屏「先配置你自己的模型 API」→ 配置页填写后刷新即放行（`BYOK_GATE_CDP_OK`；用 `scripts/browser_e2e/cdp.py` 的同一 CDP 驱动执行，一次性检查脚本未入库）。
- AC 统计（按 §3 表）：**通过 42 条**（AC-01 … AC-42 全部）、**部分通过 0 条**、**未实现 0 条**。
- 无未闭合验收项：AC-20 已由真模型端到端 + 人工标注覆盖（第 16 批），AC-34 由回滚演练覆盖（第 15 批）。仍未纳入自动化 CI 的是「真模型调用类」与「真浏览器/回滚演练」验收（需本机 Chrome、需消耗模型额度、需独占 8001/3001 栈），各自都有可复现命令与留痕；未通过修改期望值或预填结果取得任何「通过」。

## 7. QA Loop Engineering 首次运行（用户要求，2026-11-19）

- 方法学与评分卡：`docs/qa-loop-engineering.md`；可复用脚本：`scripts/qa-loop.workflow.js`；完整逐轮记录：`docs/qa-loop-runs/README.md` 与 `docs/qa-loop-runs/round-1.md` … `round-5.md`。
- 执行方式：workflow 编排（37 个子 agent），每轮「四组并行审计 → 评测组打分 → 客户端/服务端并行修复 → 测试组复测」，写权限按 `frontend/**` 与 `backend/**` 隔离，评测组须先真跑命令再打分（未修 P0 上限 70、≥3 条 P1 上限 85）。
- 逐轮结果（真实分值）：**R1 62 → R2 64 → R3 71 → R4 67 → R5 72**，5 轮均未达 95 收敛线；R4 的结构化 payload 未落盘，仅保留 verdict 片段（无维度明细，如实标注）。
- 主要修复：AC-16 synthesis/复核（新增 `workflows/synthesis.py`）、AC-01 会话创建即排首个 clarify Run（`start_initial_run`）、前端 `phaseRouter`/审计页交互/「重新审计」入口、跨会话画布与版本串用、无限重连、乐观消息被剥离、BriefCard 草稿被清空、审计错误被吞。
- 收口后另修三项 P1：`backend/db/session_store.py` 原子写在 Windows 下的 `WinError 5` 丢消息（mkstemp + 有界重试）、`frontend/lib/api.ts` 把 410/502/503 误判为端点缺失导致永久 legacyMode、`sessionStore.refreshCanvas` 的 `product_version=0`。
- 结论：**达到「可交付」但未达「95 分收敛」**；未闭合项与最小调整方案见 `docs/qa-loop-runs/README.md` §未闭合项。
