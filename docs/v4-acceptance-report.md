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
| WP-10 | 发布闭环 | 完成（除浏览器自动化） | `.github/workflows/ci.yml`、`scripts/v4_migration_drill.py`（演练实跑 DRILL_OK）、`/health/detail` 增加 Worker 诊断、`frontend/tests/e2e` |

## 2. 实际执行的验证命令与结果

```bash
# 后端全量（646 用例，含 M2/M3 + QA Loop 收口 + 闭环热修新增用例）
cd backend && ../.venv/Scripts/python.exe -m unittest discover -s tests
# → Ran 646 tests / OK / exit 0（含 tests/test_audit_runner.py 的 3 个真 runner 审计用例）

# 跨进程（真 subprocess + 共享 SQLite）
cd backend && ../.venv/Scripts/python.exe -m unittest tests.test_run_repo tests.test_cross_process
# → Ran 23 tests / OK（AC-06 幂等、AC-07 并发、AC-08 双 Worker、AC-28 验证码/限流）

# 前端（本机 registry.npmjs.org 不可达，用既有 node_modules）
cd frontend && npx tsc --noEmit      # exit 0
cd frontend && npm test              # 123/123 pass（tsc -p tsconfig.test.json + node --test）
cd frontend && npm run build         # exit 0，7 条路由（含 /session/[id]/audit 8.11 kB）
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
```

## 3. 逐条验收（§11 AC-01 … AC-42）

| AC | 状态 | 证据（可执行用例） | 说明 / 偏差 |
| --- | --- | --- | --- |
| AC-01 | 部分通过 | `frontend/tests/store.test.cjs`（createOnceGuard、按 message_id 去重）；`test_api_v2.py::test_create_session_and_views` | 刷新只读、无二次写路径；**未做真实浏览器 StrictMode 双挂载 e2e** |
| AC-02 | 通过 | `test_context_builder.py::test_sections_follow_the_declared_order`、`test_current_message_is_not_duplicated_in_conversation`；`test_coach.py::test_valid_output_produces_question_and_proposed_brief` | 注入含原问题、前问、Brief、当前输入且只出现一次 |
| AC-03 | 通过 | `test_command_service.py::test_confirm_brief_advances_to_brainstorming`、`test_skip_clarification_marks_brief_skipped` | 确认/跳过只建 1 条同步 Run，不自动触发四角色 |
| AC-04 | 通过（v2 路径） | `test_session_repo.py::test_1000_messages_paging_keeps_history`、`test_limit_is_clamped` | v2 仓储无截断、seq 单调、投影含新主张 |
| AC-05 | 部分通过 | `test_brainstorm.py`（role_code 必须等于当前角色、`extra=forbid`、修复仅一次、两次非法→MODEL_OUTPUT_INVALID）；`test_contracts.py`（RoleResult 上限/越权引用） | 协议与专家路径双重校验；**越权 evidence_id 的「归属校验」仍未逐条回查会话**（只做长度/格式校验） |
| AC-06 | 通过（跨进程） | `test_cross_process.py::test_same_request_id_in_two_processes_creates_one_run`；`test_command_service.py::test_same_request_id_with_different_body_conflicts` | 同键两进程 → 同 run/reused 一真一假/消息 seq [1,2]；异体同键 409 |
| AC-07 | 通过（跨进程） | `test_cross_process.py::test_concurrent_different_keys_same_session_gets_session_busy` | 一成一败（SESSION_BUSY/VERSION_CONFLICT），messages=2、runs=1 |
| AC-08 | 通过（跨进程） | `test_cross_process.py::test_two_worker_processes_claim_the_same_run_once`；`test_run_repo.py::test_old_worker_submit_after_takeover_is_rejected`；`test_runner.py::test_succeeded_step_is_skipped_without_model_call` | 条件 UPDATE 原子领取、epoch 递增、旧提交被拒、成功步骤不重跑 |
| AC-09 | 部分通过 | `test_run_repo.py::test_event_seq_is_monotonic_and_unique`；`test_runner.py::test_succeeded_step_is_skipped_without_model_call` | 步骤幂等 + 事件可回放；**未做「发事件前 kill -9」真崩溃注入** |
| AC-10 | 部分通过 | `test_runner.py::test_retryable_error_two_attempts_then_run_failed`；`test_command_service.py::test_apply_outcome_twice_applies_only_once`；`test_llm_client.py::test_sdk_retries_disabled` | 单步 ≤2 次、结果仅应用一次；**未验证崩溃后预留预算回收** |
| AC-11 | 通过 | `test_api_v2.py::test_sse_replays_from_after_seq_and_closes_on_terminal`；`test_legacy_compat.py::test_brainstorm_sse_translates_events_and_done_once`；`frontend/tests/sse.test.cjs` | 终结事件恰好一次（服务端 + 前端双保险） |
| AC-12 | 通过 | `frontend/tests/sse.test.cjs`（跨块 UTF-8/CRLF/多行 data/注释/意外 EOF）；`test_api_v2.py::test_keepalive_frame_is_comment_without_seq` | 心跳为注释不占序号；EOF 无终结只算断连 |
| AC-13 | 通过 | `frontend/tests/store.test.cjs`（切换会话/generation 守卫）；`test_api_v2.py::test_sse_disconnect_does_not_cancel_run` | 切 A→B 取消订阅但不取消服务器任务 |
| AC-14 | 部分通过 | `test_brainstorm.py::test_repeated_invalid_output_fails_role_but_run_partial`；`test_brainstorm_runner.py::test_invalid_role_output_marks_partial` | 一专家失败不中止、run 标 partial_succeeded；**重试仍是整 Run 重放，未做「只调失败专家」** |
| AC-15 | 通过 | `test_reducer.py::test_merge_is_independent_of_arrival_order`；`test_brainstorm_runner.py`（4 角色各自 `build_context(product_version=base)`，首轮互不可见） | 结果不依赖完成顺序、同基准产品版本 |
| AC-16 | 通过 | `backend/workflows/synthesis.py`（`MAX_REPAIR_ROUNDS=1`、`MAX_REVIEWERS=2`、冲突上限 2、只看冲突相关对方结论、degraded 回退）；`backend/tests/test_synthesis.py`(8)：归并/复核上限/复核专家不可再开复核/降级；`workflows/runner.py` 注册 `TaskKind.synthesis`；brainstorm 成功后由 `command_service._enqueue_follow_up` 同事务排入子 Run | 最多 1 轮修复 + 至多 2 位专家复核，冲突与分歧进 `SynthesisResult`；预算不足时降级并标注 |
| AC-17 | 通过 | `test_context_builder.py::test_protected_content_is_never_dropped_by_budget`；`test_coach.py::test_confirmed_field_is_never_overwritten_by_model` | 受保护字段不因预算丢弃；模型不可改写已确认项 |
| AC-18 | 通过 | `test_canvas_projector.py::test_two_calls_are_byte_identical`、`test_projection_does_not_mutate_state`；`test_reducer.py::test_merge_is_independent_of_arrival_order`；`frontend/tests/store.test.cjs`（acceptProductVersion 单调） | 投影纯函数、零模型调用、旧版本不覆盖；「待合并建议」UI 未单独做（角色结果仅在合并后进画布） |
| AC-19 | 部分通过 | `test_canvas_projector.py::test_branch_order_follows_declared_order`、`test_every_node_carries_refs_and_status`；`components/canvas/CanvasPanel.tsx` | 节点带 claim/option/decision/experiment 引用与状态；**8 分支展开未做浏览器 e2e** |
| AC-20 | 部分通过 | `test_retriever.py`（零相关→[]、同源去重、加权）；`test_context_builder_rag.py`（命中注入标「不可引用/未验证」、无命中不注入） | 检索已接入且不编造引用；**「模型是否编造数字」属模型行为，未做端到端人工标注** |
| AC-21 | 通过 | `test_audit_workflow.py::test_open_writes_first_question_and_activates_audit`、`test_open_uses_planned_dimension_when_model_disagrees`；`test_audit_runner.py::test_audit_open_via_runner_commits_question_and_activates_audit`；`.tmp/e2e_audit.py` | 开场输入锚定 ProductState，问题含维度与依据 |
| AC-22 | 通过 | `test_audit_workflow.py::test_open_is_idempotent_when_question_already_exists`；`test_api_v2.py::AuditEndpointTests`（真实 `GET /api/v2/audits/{id}` 返回 questions/active_question_id；他会话 403 且不泄漏 questions）；`test_audit_runner.py`（answer 后 active_question_id 指向第 2 问）；前端 `hydrateAuditById` | 同一 audit_id/问题可恢复，不重建、不二次开场 |
| AC-23 | 部分通过 | `test_audit_workflow.py::test_answer_ignored_when_audit_not_active`、`test_answer_rejects_unknown_question`；`test_command_service.py::test_answer_audit_requires_open_question` | 单进程语义已覆盖；**两进程同时回答同一问题未做真并发用例**（幂等框架层面由 AC-06 覆盖） |
| AC-24 | 通过 | `test_audit_workflow.py::test_open_uses_planned_dimension_when_model_disagrees`、`test_plan_next_dimension_prefers_uncovered_then_gaps`、`test_normalize_assessment_downgrades_supported_without_evidence` | 维度由问题指定与规划器决定，不按回答关键词归类；supported 必须有证据否则降级 |
| AC-25 | 通过 | `test_audit_workflow.py::test_answer_at_hard_limit_closes_and_never_asks_nineteenth`、`test_answer_at_question_target_closes_audit`；`test_audit_runner.py::test_closing_run_generates_model_report_and_completes_audit`；`.tmp/e2e_audit.py`（完成后 409 AUDIT_CLOSED） | 第 18 问后直接 closing、无第 19 问、完成后拒绝继续回答 |
| AC-26 | 通过 | `test_audit_workflow.py::test_report_falls_back_when_model_unavailable`、`test_report_recovers_from_failed_audit`、`test_build_fallback_report_marks_limitations`；`test_audit_runner.py::test_closing_run_generates_model_report_and_completes_audit` | 模型可用走 `generated_by="model"`，不可用确定性有限总结（`generated_by="fallback"` + limitations）；`_apply_report_draft` 在无 supported 维度时清空 supported_points 并记 warning（FR 不凭空补齐） |
| AC-27 | 通过 | `test_api_v2.py::test_session_ownership_and_missing`；`test_audit_repo.py::test_require_audit_enforces_ownership_and_existence`；`test_legacy_compat.py::test_legacy_delete_then_all_legacy_routes_404` | v2 与 v1 兼容路径都按 owner 拒绝 |
| AC-28 | 通过（跨进程） | `test_cross_process.py::test_auth_code_and_rate_limit_are_shared_across_processes`；`test_auth_repo.py`(14) | 共享 DB、一次使用、5 次错码、窗口原子 |
| AC-29 | 通过 | `test_command_service.py::test_clarify_rejects_too_long_input`；`test_runner_budget.py`(5)、`test_llm_client.py::test_missing_usage_falls_back_to_estimated`/`test_sdk_retries_disabled` | 8001 字拒绝、第 11 次上游调用拒绝、usage 缺失标 estimated、SDK 重试 0 |
| AC-30 | 通过 | `test_command_service.py::test_cancel_queued_run_terminates_immediately`、`test_cancelled_run_result_cannot_be_committed`；`test_runner.py::test_session_deleted_cancels_run_without_results` | 取消优先、旧结果不能提交、不复活会话 |
| AC-31 | 部分通过 | `frontend/tests/store.test.cjs`（登出后写回被丢弃）；`frontend/app/login/page.tsx` | store 层隔离已验证；**无真实浏览器换账号 e2e** |
| AC-32 | 通过 | `test_migration_import.py`(12)：dry-run 零写、apply 幂等、源变更 conflict、空 owner 隔离、旧面试 cancelled+归档、源 hash 不变；`scripts/v4_migration_drill.py` 实跑 | 迁移演练 5 阶段全通过 |
| AC-33 | 部分通过 | `test_voice.py`(7)；`app/session/[id]/interview/page.tsx`（摄像头可拒绝、纯文字可用） | 引擎能力如实上报；**未做真实浏览器权限拒绝演练** |
| AC-34 | 部分通过 | v2 单一写源 + 旧路由 adapter；`test_legacy_compat.py`(7) | 无 JSON 双写；**未做「新构建失败切回上一兼容构建」演练** |
| AC-35 | 通过 | `test_api_v2.py::test_sse_after_seq_zero_replays_from_start`、`test_sse_cursor_expired_only_below_retention_watermark`、`test_sse_invalid_cursor_errors`、`test_run_detail_and_snapshot_single_transaction` | after_seq=0 从头回放、410 仅低于水位、快照单事务 |
| AC-36 | 部分通过 | `test_command_service.py::test_end_audit_schedules_closing_run_when_idle`、`test_cancel_audit_supersedes_pending_end`；`.tmp/e2e_audit.py`（end→closing Run→completed）；`test_audit_workflow.py::test_report_recovers_from_failed_audit` | 意图先落库、唯一 closing Run、cancel 优先；**未做 kill/restart 集成验证** |
| AC-37 | 通过 | `test_command_service.py::test_retry_rejects_when_product_state_changed` | 409 RETRY_CONTEXT_CHANGED，不混新上下文 |
| AC-38 | 通过 | `test_reducer.py::test_same_local_id_in_different_roles_gets_distinct_server_ids`；`test_run_repo.py::test_reserve_candidate_id_is_stable` | 不同角色 local_id=1 → 服务器 ID 各异且引用一致 |
| AC-39 | 部分通过 | `test_api_v2.py::test_run_detail_and_snapshot_single_transaction`；`test_migration_import.py::test_changed_source_is_conflict_not_overwrite` | 快照与水位一致、tombstone 阻止重迁；**「删除后在线正文清除」未实现（软删保留正文）** |
| AC-40 | 部分通过 | `test_command_service.py`（revise/evidence/correct）；`test_audit_repo.py::test_assessment_revisions_are_append_only` | 同步命令零模型调用、修订可追溯、原报告保留；**人工更正后「重新生成报告」未在 workflow 落地** |
| AC-41 | 部分通过 | `test_audit_workflow.py::test_stale_audit_version_skips_writes`；`retry_run` 审计版本校验；`test_command_service.py::test_retry_failed_run_creates_child_run` | 陈旧版本不写；**「开场失败→新建审计→重试旧 Run」的端到端 409 未测** |
| AC-42 | 部分通过 | `test_command_service.py::test_cancel_audit_supersedes_pending_end`；`test_state_machine.py::test_cancel_supersedes_unfinished_end`；`test_audit_workflow.py::test_report_cancel_takes_priority_over_generation` | 语义与单进程路径覆盖；**并发竞争未做真并发用例** |

## 4. 已知偏差（如实列出，含影响与最小调整方案）

1. **主持人 synthesis/复核（AC-16）— 已解决（QA Loop 第 1 轮修复）**：新增 `backend/workflows/synthesis.py`（696 行）实现 `SynthesisResult`、冲突上限 2、「最多 1 轮修复 + 至多 2 位专家复核 + 复核专家不可再开复核」、预算不足 degraded 回退；brainstorm 成功后由 `command_service._enqueue_follow_up` 在同一事务排入 `TaskKind.synthesis` 子 Run；`backend/tests/test_synthesis.py` 8 例覆盖。
2. **重试粒度是整 Run（AC-14）**：`retry_run` 建子 Run 重放整个 action；已成功步骤会被跳过（不重复调模型），但失败专家的上下文仍按当前产品版本重建。影响：重试成本略高于「只调失败专家」。最小调整：在子 Run 上记录失败 step 列表，runner 只调度这些 logical_key。
3. **越权 evidence_id 只做格式/长度校验（AC-05）**：`RoleResult` 校验引用数量与形状，未逐条回查 evidence 表归属该会话。影响：模型可引用不存在的 ID（渲染层会显示为缺失）。最小调整：归并前用 `session_repo.get_evidence` 过滤未知/他会话引用并记 warning。
4. **前端测试用 `node --test` 而非 vitest/RTL**：本机 `registry.npmjs.org` 不可达，无法安装新依赖。影响：无组件渲染测试，AC-01/19/31/33 只能到 store/逻辑层。最小调整：网络恢复后补 vitest + RTL。
5. **软删不清正文（AC-39）**：`deleted_at` 置位后不可读，正文仍在库；无保留期物理清理作业。最小调整：加 `scripts/purge_deleted_sessions.py` 按保留期清理并保留 tombstone。
6. **同步命令也建 Run 行**：`confirm_brief` 等同步命令各建一条 `succeeded` Run 用于回执与幂等审计（有意设计，非缺陷）；跨进程测试按 run_id 而非 `COUNT(*)` 断言。
7. **`MigrationRecord` 无 detail 列**：迁移细节写入 `warnings_json`；12-hex 旧 ID 重映射为 32-hex 后无旧 URL 别名表。最小调整：加 `legacy_id_map` 表。
8. **brainstorm 的 step 事件粒度**：role 步骤不写 `step.started`（只有顶层 `brainstorm.generate` 有，角色完成以 `role.completed` 表达）；预算中止时个别 role step 可能停留 running（run 仍 failed 且恰好一个终结事件）。最小调整：role step 也发 `step.started`，并在 `_force_terminal` 中把残留 running step 标 interrupted。
9. **事件压缩作业未实现**：`api/sse.py` 已有保留水位（低于水位 410），但没有后台压缩任务；活动 Run 不压缩的规则尚未有实现主体。
10. **未登录访问未知 `/api/v2/*` 返回 401 而非 404**：鉴权是中间件，在路由匹配前短路；PRD 未要求，前端已按 401/404 双兼容处理。
11. **审计回答消息由命令层写入**：`command_service._submit_async` 在建 Run 前写 human 消息（stage=audit）并回填 `audit_questions.answer_message_id`，`workflows/audit.py` 只做评估与下一问。这是「用户消息+序号+Run+占位同事务」的必然结果，workflow 再写会双写，属设计说明而非偏差。
12. **`audit_repo` 缺评估修订读取 API**：只有 `add_assessment_revision`，前端暂不需要评估修订历史；将来要展示时由 storage 补 `list_assessment_revisions`（只读）。
13. **本机 8000 端口运行旧进程**：手工验证 `/api/v2` 前需重启后端；前端已做 404→legacy 回落。

## 5. NFR（§9）抽查

- 事务中不等待模型/网络：runner 提交阶段只做 DB 写入，模型调用在事务外（`test_runner` 失败路径不写脏状态）。
- 单会话单活动 Run：`test_command_service.py::test_second_command_while_run_active_is_session_busy`。
- 幂等保留 30 天：`test_run_repo.py::test_save_then_get_returns_same_record_and_keeps_expiry_epoch`（expires_at 为 epoch 秒）。
- 限额：`MAX_QUEUED_RUNS_PER_USER=3`、`COMMANDS_PER_MINUTE=20`、`MAX_UPSTREAM_CALLS_PER_RUN=10`、`max_output_tokens` 等均有对应用例。
- 可观测性：`/health/detail` 返回 `worker.db/running_runs/queued_runs/last_heartbeat_at`，DB 不可用时 degraded。
- 迁移可回退：`scripts/v4_migration_drill.py` 证明 dry-run 零写、apply 幂等、删库重迁一致、源文件零改动。

## 6. 结论

- M1（WP-00～05）、M2（WP-06～08）、M3（WP-09）与 WP-10 的发布件（CI、迁移演练、健康诊断、前端 e2e 冒烟）均已交付。
- 真实执行结果：后端 **646 用例 OK**、前端 **245 用例 pass** + `tsc` 0 错 + `build` 成功、`test:e2e` 1 pass、跨进程 4 用例 OK、真 Worker 审计端到端 **30 项 ALL PASS**、迁移演练 **DRILL_OK**。
- AC 统计（按 §3 表）：**通过 26 条**（AC-02/03/04/06/07/08/11/12/13/15/16/17/18/21/22/24/25/26/27/28/29/30/32/35/37/38）、**部分通过 15 条**（AC-01/05/09/10/14/19/20/23/31/33/34/36/39/40/41/42）、**未实现 0 条**。
- 剩余差距集中在：重试粒度（AC-14）、真实浏览器与崩溃注入类验证（AC-01/09/10/19/31/33/34）、软删正文清理与事件压缩作业（AC-39）、人工更正后重生成报告（AC-40）。每条均给出最小调整方案，未通过修改期望值或预填结果取得任何「通过」。

## 7. QA Loop Engineering 首次运行（用户要求，2026-11-19）

- 方法学与评分卡：`docs/qa-loop-engineering.md`；可复用脚本：`scripts/qa-loop.workflow.js`；完整逐轮记录：`docs/qa-loop-runs/README.md` 与 `docs/qa-loop-runs/round-1.md` … `round-5.md`。
- 执行方式：workflow 编排（37 个子 agent），每轮「四组并行审计 → 评测组打分 → 客户端/服务端并行修复 → 测试组复测」，写权限按 `frontend/**` 与 `backend/**` 隔离，评测组须先真跑命令再打分（未修 P0 上限 70、≥3 条 P1 上限 85）。
- 逐轮结果（真实分值）：**R1 62 → R2 64 → R3 71 → R4 67 → R5 72**，5 轮均未达 95 收敛线；R4 的结构化 payload 未落盘，仅保留 verdict 片段（无维度明细，如实标注）。
- 主要修复：AC-16 synthesis/复核（新增 `workflows/synthesis.py`）、AC-01 会话创建即排首个 clarify Run（`start_initial_run`）、前端 `phaseRouter`/审计页交互/「重新审计」入口、跨会话画布与版本串用、无限重连、乐观消息被剥离、BriefCard 草稿被清空、审计错误被吞。
- 收口后另修三项 P1：`backend/db/session_store.py` 原子写在 Windows 下的 `WinError 5` 丢消息（mkstemp + 有界重试）、`frontend/lib/api.ts` 把 410/502/503 误判为端点缺失导致永久 legacyMode、`sessionStore.refreshCanvas` 的 `product_version=0`。
- 结论：**达到「可交付」但未达「95 分收敛」**；未闭合项与最小调整方案见 `docs/qa-loop-runs/README.md` §未闭合项。
