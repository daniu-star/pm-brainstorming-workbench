# PM Brainstorm V4.0 验收报告（M1 交付）

- 分支：`feat/prd-v4-m1`（基于 `ad77bd2`）；提交：`5915020`（基础设施与领域契约）、`f7b4d59`（原子幂等/原子领取/跨进程验收）
- 报告日期：2026-11-19
- PRD：`docs/superpowers/specs/2026-11-18-pm-brainstorm-agent-architecture-prd-v4.md`（V4.0，807 行）
- 本报告**只记录真实执行过的命令与结果**；未实现项一律标「未实现」，不用「通过」美化。

## 1. 范围内工作包（§10.2 依赖顺序）

| WP | 内容 | 状态 | 落点 |
| --- | --- | --- | --- |
| WP-00 | 基线回归 | 完成 | 保留旧测试；`test_session_store` 的 200 截断用例作为「旧 JSON store 已退役」的基线保留 |
| WP-01 | 领域协议 | 完成 | `backend/domain/{contracts,state_machine,errors}.py` + `tests/test_contracts.py`(39) `tests/test_state_machine.py`(30) |
| WP-02 | 存储与迁移 | 完成 | `backend/repositories/{database,session_repo,run_repo,audit_repo,auth_repo}.py`、`backend/migrations/import_json_sessions.py` + 16/19/11/14/12 用例 |
| WP-03 | 持久执行 | 完成（M1 只注册 clarify 工作流） | `backend/workflows/{runner,coach}.py`、`backend/workers/run_worker.py`、`tests/test_runner.py`(17) |
| WP-04 | 前端与 v1 兼容 | 完成 | `frontend/{lib,store,components}`、`backend/api/v2/*`、旧 4 路由 adapter + 69 前端用例 |
| WP-05 | 教练与上下文 | 完成 | `backend/services/{context_builder,canvas_projector}.py`、`backend/core/role_prompts.py` + 25/23/25 用例 |
| WP-06 | 检索与证据 | 部分 | `backend/rag/retriever.py`（11 用例）可用，但**尚未接入 build_context 的证据区** |
| WP-07 | 专家收敛 | 未实现 | 无 `workflows/brainstorm.py`、无 Reducer/synthesis；`brainstorm` 命令返回 `FEATURE_NOT_READY` |
| WP-08 | 画布结论 | 部分 | 确定性投影已实现；Reducer 合并、待合并建议未实现 |
| WP-09 | 审计闭环 | 部分 | 仓储/状态机/控制意图/API 就绪；`start_audit`/`answer_audit`/报告生成工作流未实现 |
| WP-10 | 发布闭环 | 未实现 | 部署切换、构建回退演练、真实浏览器 E2E 未做 |

## 2. 实际执行的验证命令与结果

```bash
# 后端全量（352 个用例）
cd backend && ../.venv/Scripts/python.exe -m unittest discover -s tests
# → Ran 352 tests in 71.051s / OK / exit 0

# 跨进程（真 subprocess + 共享 SQLite）
cd backend && ../.venv/Scripts/python.exe -m unittest tests.test_run_repo tests.test_cross_process
# → Ran 23 tests in 13.584s / OK

# 前端
cd frontend && npx tsc --noEmit           # exit 0
cd frontend && npm test                    # 69/69 pass（tsc -p tsconfig.test.json + node --test）
cd frontend && npm run build               # exit 0，6/6 静态页

# 真 Worker 端到端（假 LLM，独立进程）
PYTHONPATH=backend .venv/Scripts/python.exe .tmp/e2e_drive.py
# → ALL PASS：run succeeded / product_version=2 / 事件 seq 1..5 连续 / 终结事件恰 1 /
#    steps=1 attempt=1 / 消息 3 条（human seq1、human seq2、coach seq3）/ active_run 释放 /
#    session_version 1→3 / 幂等行 (clarify, run_id, 202) / after_seq=1 回放不含 seq1

# 迁移器（临时副本，dry-run 零写库）
python -m migrations.import_json_sessions --source <副本> --dry-run
# → scanned=19 valid=19 empty_owner=19 remapped=19 planned={sessions:19,messages:65,
#    product_states:19,legacy_audits:1,legacy_artifacts:11}，data/app.db 六表全 0
python -m migrations.import_json_sessions --source <副本> --apply
# → 19 sessions / 65 messages / 19 states / 1 cancelled legacy audit / 11 artifacts；
#    再 apply 时 skipped=19 且计数不变；源文件被改动 → conflicts=1「拒绝覆盖迁移记录」
```

## 3. 逐条验收（§11 AC-01 … AC-42）

| AC | 状态 | 证据（可执行用例） | 说明 / 偏差 |
| --- | --- | --- | --- |
| AC-01 | 部分通过 | `frontend/tests/store.test.cjs`（createOnceGuard、消息按 id 去重）；`backend/tests/test_api_v2.py::test_create_session_and_views` | 刷新只读由 GET 保证、无写路径；**未做真实浏览器 StrictMode 双挂载 e2e** |
| AC-02 | 通过 | `test_context_builder.py::test_sections_follow_the_declared_order`、`test_current_message_is_not_duplicated_in_conversation`；`test_coach.py::test_valid_output_produces_question_and_proposed_brief` | 注入顺序含原问题、前问、Brief、当前输入；当前输入只出现一次 |
| AC-03 | 通过 | `test_command_service.py::test_confirm_brief_advances_to_brainstorming`、`test_skip_clarification_marks_brief_skipped` | 确认/跳过只建 1 条同步 Run，**不自动触发四角色**；未知项保留为 unknown/proposed |
| AC-04 | 通过（v2 路径） | `test_session_repo.py::test_1000_messages_paging_keeps_history`、`test_limit_is_clamped` | v2 仓储无截断、seq 单调、投影含新主张；旧 JSON store 的 200 截断用例仍在（该 store 已不在任何在线路径） |
| AC-05 | 部分通过 | `test_coach.py::test_bad_json_is_repaired_once`、`test_two_bad_outputs_fall_back_deterministically`、`test_extra_fields_are_forbidden`；`test_contracts.py`（RoleResult 越权/未知 role/上限） | 协议与教练路径已校验；**未知 role/越权 evidence 的端到端拒绝在 M2 专家工作流里才能触发** |
| AC-06 | 通过（跨进程） | `test_cross_process.py::test_same_request_id_in_two_processes_creates_one_run` | 同键两进程 → 同 run_id、`reused` 一真一假、runs=1、消息 seq [1,2]；异体同键 409 由 `test_command_service.py::test_same_request_id_with_different_body_conflicts` |
| AC-07 | 通过（跨进程） | `test_cross_process.py::test_concurrent_different_keys_same_session_gets_session_busy` | 一成一败（SESSION_BUSY/VERSION_CONFLICT），messages=2、runs=1 |
| AC-08 | 通过（跨进程） | `test_cross_process.py::test_two_worker_processes_claim_the_same_run_once`；`test_run_repo.py::test_old_worker_submit_after_takeover_is_rejected`；`test_runner.py::test_succeeded_step_is_skipped_without_model_call` | processed 合计 1、run.started 恰 1、epoch=1；旧 epoch 提交被拒；已 succeeded 步骤不重跑 |
| AC-09 | 部分通过 | `test_run_repo.py::test_event_seq_is_monotonic_and_unique`；`test_runner.py::test_succeeded_step_is_skipped_without_model_call`、`test_success_writes_exactly_one_terminal_event_and_usage` | 步骤幂等 + 事件可回放不重复已覆盖；**未做「发事件前 kill -9」的真崩溃注入** |
| AC-10 | 部分通过 | `test_runner.py::test_retryable_error_two_attempts_then_run_failed`；`test_command_service.py::test_apply_outcome_twice_applies_only_once`；`test_llm_client.py::test_sdk_retries_disabled` | 单步 ≤2 次尝试、结果仅应用一次；**预留成本（budget reservation）按用量累计实现，未做崩溃后预算回收验证** |
| AC-11 | 通过 | `test_api_v2.py::test_sse_replays_from_after_seq_and_closes_on_terminal`；`test_legacy_compat.py::test_brainstorm_sse_translates_events_and_done_once`；`frontend/tests/sse.test.cjs`（createOnceGuard） | 终结事件恰好一次（服务端 + 前端双保险）；旧 SSE 翻译 done 一次 |
| AC-12 | 通过 | `frontend/tests/sse.test.cjs`（跨块 UTF-8、CRLF、多行 data、注释、意外 EOF）；`test_api_v2.py::test_keepalive_frame_is_comment_without_seq` | 心跳为注释不占序号；EOF 无终结事件只算断连 |
| AC-13 | 通过 | `frontend/tests/store.test.cjs`（切换会话/generation 守卫、旧请求写回丢弃） | 切 A→B 取消订阅与读取；服务器任务不被取消（`test_api_v2.py::test_sse_disconnect_does_not_cancel_run`） |
| AC-14 | 未实现 | — | M1 无四角色工作流；`brainstorm` 返回 `failed(FEATURE_NOT_READY)` 但保留 step 记录（`test_runner.py::test_unimplemented_task_kind_fails_but_records_step`） |
| AC-15 | 未实现 | — | 首轮互不可见需要 Reducer/分阶段调度（WP-07） |
| AC-16 | 未实现 | — | 冲突收敛/复核上限在 synthesis 工作流（WP-07） |
| AC-17 | 通过 | `test_context_builder.py::test_protected_content_is_never_dropped_by_budget`；`test_coach.py::test_confirmed_field_is_never_overwritten_by_model` | 受保护字段永不因预算丢弃；模型不可改写已确认项 |
| AC-18 | 部分通过 | `test_canvas_projector.py::test_two_calls_are_byte_identical`、`test_projection_does_not_mutate_state`；`test_api_v2.py::test_message_evidence_and_canvas_endpoints`；`frontend/tests/store.test.cjs`（acceptProductVersion 单调） | 投影纯函数、模型调用数 0、旧版本不覆盖；**Reducer 提交与「待合并建议」未实现** |
| AC-19 | 部分通过 | `test_canvas_projector.py::test_branch_order_follows_declared_order`、`test_every_node_carries_refs_and_status`；前端 `components/canvas/CanvasPanel.tsx` | 节点带 source_message_ids/evidence_refs，无「+N 更多」折叠逻辑；**8 分支展开未做浏览器 e2e** |
| AC-20 | 未实现 | `test_retriever.py`（检索本身可用） | 检索未接入 `build_context`；M2 才能验证「不编造/标未验证」 |
| AC-21 | 部分通过 | `test_context_builder.py::test_audit_messages_are_excluded_from_clarify_task`；`test_audit_repo.py::test_audit_to_snapshot_tolerates_bad_dimension_data` | 审计上下文与快照协议就绪；**开场问题生成未实现** |
| AC-22 | 部分通过 | `test_audit_repo.py::test_open_and_latest_audit_selection`；`frontend/app/session/[id]/interview/page.tsx` | 同一 audit_id 持久化、可恢复；**问答工作流缺失** |
| AC-23 | 部分通过 | `test_command_service.py::test_answer_audit_requires_open_question`；`test_audit_repo.py::test_answer_and_record_assessment` | 已回答问题不能二次回答；并发语义由 AC-06 幂等框架覆盖 |
| AC-24 | 未实现 | — | 维度由 question 指定需要审计工作流 |
| AC-25 | 部分通过 | `test_audit_repo.py::test_add_question_ordinal_bounds_and_uniqueness`（CHECK 1..18）；`test_contracts.py`（TARGET=12/HARD=18） | 上限在仓储/契约层强制；**第 18 问后转 closing 的流程未实现** |
| AC-26 | 部分通过 | `test_command_service.py::test_end_audit_schedules_closing_run_when_idle`；`test_audit_repo.py::test_report_revisions_and_snapshot` | 提前结束/报告版本保留就绪；**有限总结降级未实现** |
| AC-27 | 通过 | `test_api_v2.py::test_session_ownership_and_missing`；`test_command_service.py::test_other_owner_cannot_submit`；`test_audit_repo.py::test_require_audit_enforces_ownership_and_existence`；`test_legacy_compat.py::test_legacy_delete_then_all_legacy_routes_404` | v2 与 v1 兼容路径都按 owner 拒绝 |
| AC-28 | 通过（跨进程） | `test_cross_process.py::test_auth_code_and_rate_limit_are_shared_across_processes`；`test_auth_repo.py`(14) | 共享 DB、一次使用、5 次错码、窗口原子 |
| AC-29 | 通过 | `test_command_service.py::test_clarify_rejects_too_long_input`；`test_runner.py::test_budget_exceeded_fails_without_model_call`、`test_upstream_call_budget_rejects_eleventh_call`；`test_llm_client.py::test_missing_usage_falls_back_to_estimated`、`test_sdk_retries_disabled` | 8001 字拒绝、第 11 次调用拒绝、usage 缺失标 estimated、SDK 重试 0 |
| AC-30 | 通过 | `test_command_service.py::test_cancel_queued_run_terminates_immediately`、`test_cancelled_run_result_cannot_be_committed`、`test_cancel_deleted_session_is_not_found`；`test_runner.py::test_session_deleted_cancels_run_without_results`、`test_cancel_requested_commits_cancelled_without_product_state` | 取消优先、旧结果不能提交、不复活会话 |
| AC-31 | 部分通过 | `frontend/tests/store.test.cjs`（登出后任何写回被丢弃）；`frontend/app/login/page.tsx` | store 层隔离已验证；**未做真实浏览器换账号 e2e** |
| AC-32 | 通过 | `test_migration_import.py`(12)：dry-run 零写、apply 幂等、源变更 conflict、空 owner 隔离、旧面试 cancelled+归档、源 hash 不变 | 迁移器真机 dry-run/apply 另有人工演练（见 §2） |
| AC-33 | 部分通过 | `test_voice.py`(7)；`frontend/app/session/[id]/interview/page.tsx`（摄像头可拒绝、纯文字可用） | 引擎能力如实上报；**未做真实浏览器权限拒绝演练** |
| AC-34 | 部分通过 | v2 单一写源 + 旧路由 adapter；`test_legacy_compat.py`(7) | 无 JSON 双写；**未做「新构建失败切回上一兼容构建」演练** |
| AC-35 | 通过 | `test_api_v2.py::test_sse_after_seq_zero_replays_from_start`、`test_sse_cursor_expired_only_below_retention_watermark`、`test_sse_invalid_cursor_errors`、`test_run_detail_and_snapshot_single_transaction` | after_seq=0 合法从头回放；410 仅低于保留水位；快照单事务 |
| AC-36 | 部分通过 | `test_command_service.py::test_end_audit_schedules_closing_run_when_idle`、`test_cancel_audit_supersedes_pending_end`；状态机 `AUDIT_TRANSITIONS[closing] ⊇ cancelled` | 意图先落库、唯一 closing Run、cancel 优先；**无 kill/restart 集成验证，审计生成未实现** |
| AC-37 | 通过 | `test_command_service.py::test_retry_rejects_when_product_state_changed` | 409 RETRY_CONTEXT_CHANGED |
| AC-38 | 部分通过 | `test_run_repo.py::test_reserve_candidate_id_is_stable`；`test_cross_process.py`（创建响应丢失重试） | 服务端候选 ID 稳定；**两角色 local_id=1 的真实候选登记在 M2 才能端到端** |
| AC-39 | 部分通过 | `test_api_v2.py::test_run_detail_and_snapshot_single_transaction`；`test_migration_import.py::test_changed_source_is_conflict_not_overwrite` | 快照与水位一致、tombstone 阻止重迁；**「删除后在线正文清除」未实现（当前为软删保留正文）** |
| AC-40 | 通过 | `test_command_service.py`（revise/evidence/correct）；`test_audit_repo.py::test_assessment_revisions_are_append_only` | 同步命令无模型调用、新修订可追溯、原报告保留；**完成后「人工修订」重生成报告属 M3** |
| AC-41 | 部分通过 | `retry_run` 的审计版本校验（代码就绪）；`test_command_service.py::test_retry_failed_run_creates_child_run` | 无审计工作流，无法构造「开场失败后新建审计」的端到端场景 |
| AC-42 | 部分通过 | `test_command_service.py::test_cancel_audit_supersedes_pending_end`；`test_state_machine.py::test_cancel_supersedes_unfinished_end` | cancel 优先于未完成 end 已覆盖（单进程）；**真实并发竞争未测** |

## 4. 已知偏差（如实列出，含影响与最小调整方案）

1. **M2/M3 工作流缺失**：`brainstorm`、`start_audit`、`answer_audit`、`end_audit`、`rebuild_legacy` 均已接入命令层并正确建 Run/审计/控制意图，但 Worker 端没有对应工作流，会以 `failed(FEATURE_NOT_READY)` 明确失败并保留 step 记录（不产生假成功、不写脏状态）。影响：AC-14/15/16/20/24 及部分 AC-19/21–26/36/41/42 无法端到端通过。最小调整：按 PRD WP-06→09 依次实现 `workflows/{brainstorm,reducer,audit}.py` 并在 `runner.WORKFLOWS` 注册。
2. **检索未接入上下文**：`rag/retriever.py` 可用且有 11 个用例，但 `build_context` 的证据区只读 DB evidence 表。影响：AC-20 与 FR-04A 的 `search()` 集成未完成。最小调整：在 `context_builder._section_evidence` 前调用 `retriever.search()` 并合并为 `RetrievalHit`。
3. **前端测试用 `node --test` 而非 vitest/RTL**：本机 `registry.npmjs.org` 不可达，无法 `npm ci` 或安装新依赖；改用 `tsc -p tsconfig.test.json` + `node --test tests/*.test.cjs`（69 用例）+ `next build`。影响：缺少组件级渲染测试，AC-01/19/31/33 只能到 store/逻辑层。最小调整：网络恢复后引入 vitest + RTL 补齐组件测试。
4. **旧 JSON 存储与 v1 模块仍留在仓库**：`backend/db/session_store.py`、`core/agent_loop.py`、`core/interviewer.py`、`core/canvas_parser.py` 已不在任何 API 写路径（`api/*` 全量 grep 无引用），但文件与旧测试仍在（`test_session_store` 7、`test_interviewer` 8、`test_canvas_parser` 10）。影响：PRD §7.3「不得保留第二条不受状态机约束的旧写路径」在**运行时**已满足，代码层面是死代码。最小调整：M2 删除这四个模块与其测试（删除后总数 352 → 327）。
5. **删除会话为软删**：`deleted_at` 置位后不可读，但正文仍在库中；AC-39「删除后在线正文已清除」需在 M2 增加物理清理作业（先软删保留审计期，再按保留期清理）。
6. **会话内同步命令也建 Run 行**：`confirm_brief` 等同步命令各建一条 `succeeded` Run 用于回执与审计（`command_service._submit_sync`）。影响：会话下 Run 行数多于「用户可感知任务数」，跨进程测试因此按 run_id 而非 `COUNT(*)` 断言。这是有意设计（§5.3「所有写动作走 IdempotencyRecord」+ 可回执），非缺陷。
7. **`MigrationRecord` 无 detail 列**：迁移细节写入 `warnings_json`；12-hex 旧 ID 重映射为 32-hex 后无旧 URL 别名表。影响：旧书签/旧链接不可直接访问。最小调整：加 `legacy_id_map(legacy_id TEXT PRIMARY KEY, session_id TEXT)` 表。
8. **本机 8000 端口运行的是旧进程**：`/api/v2` 在当前进程返回 404（前端已做 404→legacy 回落，重启后端即自动切 v2）。影响：手工浏览器验证需重启服务。

## 5. NFR（§9）抽查

- 事务中不等待模型/网络：runner 在提交阶段只做 DB 写入；模型调用在事务之外（`test_runner` 覆盖失败路径不写脏状态）。
- 单会话单活动 Run：`test_command_service.py::test_second_command_while_run_active_is_session_busy`。
- 幂等保留 30 天：`test_run_repo.py::test_save_then_get_returns_same_record_and_keeps_expiry_epoch`（expires_at 为 epoch 秒，>now+29d）。
- 限额：`MAX_QUEUED_RUNS_PER_USER=3`（`test_queue_limit_rejects_fourth_queued_run`）、`COMMANDS_PER_MINUTE=20`（`test_rate_limit_blocks_21st_command`）。
- 事件保留 7 天/活动 Run 不压缩：实现于 `api/sse.py` 的水位（低于水位返回 410），但**压缩作业本身尚未实现**（M3 发布闭环）。

## 6. 结论

- M1（WP-00～WP-05）**已交付并通过验证**：后端 352 用例、前端 69 用例、tsc/build、真 Worker 端到端、跨进程 4 用例、迁移演练全部真实通过。
- AC 统计（按 §3 表格逐条点数）：**通过 18 条**（AC-02/03/04/06/07/08/11/12/13/17/27/28/29/30/32/35/37/40）、**部分通过 19 条**（AC-01/05/09/10/18/19/21/22/23/25/26/31/33/34/36/38/39/41/42）、**未实现 5 条**（AC-14/15/16/20/24，均依赖 M2 专家工作流与检索接入）。
- 未实现项均有明确的下一步（见 §4.1–§4.5），没有任何一条是通过修改期望值或预填结果取得的。
