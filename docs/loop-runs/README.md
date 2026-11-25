# Loop Engineering Product · 运行归档

本目录记录「多角色 Loop Engineering」（7 角色 / 8 维评分 / 硬上限规则 / 写域隔离）在**本产品仓库**上的
每一次真实运行。方法学与 Harness 契约见 `skills/loop-engineering-product/SKILL.md`。

## 运行索引

| # | 日期 | 轮次 | 逐轮分数 | 终分 | 是否收敛(≥95) | 记录 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 2026-11-19 | 3 | 72 → 73 → 66 | 66 | 否 | [product-loop-2026-11-19.md](product-loop-2026-11-19.md) |
| 2 | 2026-11-19/20 | 4~5（接续） | 57 → 63 | 63 | 否 | [product-loop-2026-11-19.md](product-loop-2026-11-19.md) §7/§8 |

> 说明：第 1 次运行的 **P0**（闭环每轮整体覆盖 `product_state`，清空脑暴期 claims/options/已确认决策）
> 已在本仓库修复并补回归；随后发现的两个线上故障（旧库缺 `steps.created_at`、`LLM_API_KEY` 401）
> 属环境/schema 漂移，与 Loop 评分无关，修复记录见同目录第 1 份运行报告的 §4「Loop 之后的热修」。

> 第 2 次运行说明：第 4 轮修复提交 `5e5c08b`；第 5 轮评分代理首次返回空（`finalScore=57` 实为第 4 轮），
> 已单独补跑「只评分」轮次取得 **63/100（p0=1、p1=7、NO-GO）**；第 5 轮 P0（Worker 不驱动控制意图扫描器）
> 与 P1（兜底终结不收口闭环、前端失败被当成功、`loop_version` 无单调守卫）已在本仓库修复，见 §8 与发布清单。

## 评分口径（摘要）

| 维度 | 分值 |
| --- | --- |
| 产品与需求达成 | 20 |
| 功能正确性 | 15 |
| 数据一致性与状态机 | 15 |
| 交互与前端逻辑 | 15 |
| 架构与代码质量 | 10 |
| 安全与错误处理 | 10 |
| 自动化测试与证据 | 10 |
| 文档与可运维 | 5 |

硬上限：存在未修 P0 → 总分 ≤70；存在 ≥3 条未修 P1 → 总分 ≤85；发现测试被削弱 → ≤60 且要求回退。

## 第 6 批热修（第 5 轮评分后，2026-11-20）

| 修复 | 证据 | 验证 |
| --- | --- | --- |
| SRV-02 步骤成功与结果提交原子化 | `backend/workflows/runner.py` 的 `_attach_step_success_hook` + `requeue_uncommitted_step` | `tests/test_runner.py::ReplayTests` 2 例 + `tests/test_crash_atomicity.py`（真子进程在「步骤成功」flush 处 `os._exit`，验证事务回滚且恢复后只写一条消息）；后端全量 629 OK |
| FE-DX-02 闭环不跨会话串数据 | `store.loadSession` 清空 `loops`；`lib/loopView.ts::selectLoopChoices` 过滤 `session_id` | `tests/loop-view.test.cjs` 2 例 + `tests/session-store.test.cjs` 1 例；前端 238 pass |
| CHAT-03 刷新后看不到「上次失败原因」 | `loadSession → hydrateFailedRun` 从 `GET /sessions/{id}/runs` 恢复失败任务；`lib/chatNotices.ts` 只挑对话类（clarify/brainstorm）最近一条 | `backend/tests/test_api_session_runs.py` 3 例 + `frontend/tests/chat-notices.test.cjs` 6 例 + `tests/session-store.test.cjs` 1 例；浏览器刷新实测见 `docs/browser-qa.md` §3 |
| TST-01 页面只有 token 正则断言 | 新增 `tests/components-render.test.cjs`（`react-dom/server` 真渲染 `MessageList` / `BriefCard`，含「已确认简报 + 待确认字段」的历史数据分支） | 7 例；`tsconfig.render.json` + `npm test` 串入 `test:render` |
| scope 真正进入提示词 | `backend/core/loop_prompts.py` 的「本轮范围」行 | `tests/test_loop_commands.py::test_scope_is_injected_into_stage_prompt` |
| 硬性封顶有解释 | `lib/loopView.ts::scoreCapNotice` + 评分卡头部提示 | `tests/loop-view.test.cjs` 1 例 |
| 决策事件反映决策后状态 | `command_service._loop_event(status=…, loop_version=…)` | `test_ship_decision_closes_loop_synchronously` 断言 payload |

浏览器交互验收已从「人工/半自动协议」升级为 **CDP 自动化 e2e**（`scripts/browser_e2e/`，AC-01/19/31/33，见第 13/14 批）；仍不进 CI（需要本机 Chrome，CI 无图形栈）。SRV-02 已由 `backend/tests/test_crash_atomicity.py` 用真子进程崩溃注入补齐（不再是单进程模拟），TST-01 由 `tests/components-render.test.cjs` 补上真实渲染断言。

## 第 7 批热修（契约守卫 / 吸底 / 降级，2026-11-20）

| 修复 | 证据 | 验证 |
| --- | --- | --- |
| 契约 parity 变成真测试 | `backend/tests/test_contract_parity.py`：解析 `frontend/lib/contracts.generated.ts` 的 `export const X = [...] as const`，与 `domain/contracts.py` 的 40 组枚举 / 常量 / `Literal` 字段逐条按序比对 | 13 例；**首次运行即发现真漂移**：前端 `ACTIONS` 缺内部动作 `synthesis` / `loop_round`（`RunView.action` 无法表示这两类 Run），已补齐并给 `CommandPayloadMap` 加空 payload 声明 |
| 决策条吸底（P2 收口） | `app/session/[id]/loop/page.tsx` 的 `Section` 新增 `sticky` 属性，`DecisionBar` 在 `awaiting_decision` 时吸底（`sticky bottom-2 z-20` + 半透明背景 + 上投影） | 真浏览器实测：`position=sticky`、`bottom=8px`、`zIndex=20`，三按钮可用；截图 `docs/loop-runs/evidence/browser-loop-sticky-decision.png`；`docs/browser-qa.md` §2 第 9 步 |
| 产物反序列化降级 | `repositories/loop_repo.py::_validate_or_none` + `loop_to_snapshot` 五类产物逐条降级：单条历史脏数据只丢该产物并记 warning，不再让 `GET /loops/{id}` 500 | `backend/tests/test_loop_artifact_degrade.py`：损坏 `prototype.limitations` 后快照仍返回、合法产物照常还原、原始载荷仍可读取；线上 HTTP 实测 200 |

## 第 8 批热修（数据生命周期 / 预算档位 / 审计并发，2026-11-22）

| 修复 | 证据 | 验证 |
| --- | --- | --- |
| ③ 低额度模式 | `domain/contracts.py` 的 `BUDGET_PROFILES`（`standard` / `lean` / `frugal`）+ `budget_profile_limits()`；`core/config.py` 的 `llm_budget_profile`；`services/command_service.py::default_budget()` 用 `min()` 只收紧契约常量，生效值写入 `Run.budget_json`；`/health/detail` 暴露 `budget_profile` / `budget_limits` | `backend/tests/test_budget_profile.py` 5 例（standard=契约默认、lean 逐项 ≤ 契约且 ≤ standard、frugal 更紧、未知档位回落、`main.C_budget_defaults()` 反映档位） |
| ①-1 软删清正文 | `repositories/session_repo.py::purge_session_content`（消息正文 / 证据摘要 / 产品状态回骨架 / 审计问答与报告 / 闭环产物全清，保留版本链与会话骨架作为 tombstone）；`soft_delete_session(..., purge_content=True)` 默认清，`False` 仅调试 | `backend/tests/test_data_lifecycle.py` 2 例：清后正文为空但 tombstone 仍可追溯、版本链保留、`require_session` 仍 NOT_FOUND；`purge_content=False` 保留正文 |
| ①-2 事件压缩 + 逐 Run 水位 | `repositories/run_repo.py::oldest_event_seq` / `compact_events`（只删终态 Run 的旧事件、每 Run 保留最后一个）；`workers/run_worker.py` 每小时驱动（异常不阻塞任务）；`api/sse.py::resolve_start_cursor` 的 watermark = 第一个仍可回放的 seq，`api/v2/runs.py` 取 `max(全局水位, 该 Run 最早保留)` | `test_data_lifecycle.py` 3 例（压缩保留终结事件、活动 Run 不动、幂等；压缩后 `after_seq=0` → 410、`after_seq=watermark` → 200、超 latest → 422）；`test_api_v2` 的 410 用例保持兼容 |
| ② 审计并发 / 取消竞争跨进程 | `backend/tests/test_cross_process.py` 新增 `_prepare_active_audit()` 与两例（AC-23 同题并发回答只接受一个、AC-42 end/cancel 竞争只留一条控制链） | `tests.test_cross_process` 6 例 OK（4 旧 + 2 新），真 `subprocess` + gate 同步 + 共享 SQLite |

## 第 9 批热修（v1 死代码清理 / 引用归属校验 / 崩溃注入，2026-11-22）

| 修复 | 证据 | 验证 |
| --- | --- | --- |
| ① v1 死代码清理 | 删除 `core/agent_loop.py`、`core/interviewer.py`、`core/canvas_parser.py`、`db/session_store.py`、`db/__init__.py` 及 `core/role_prompts.py` 的旧兼容 API 块（`build_system_prompt` / `INTERVIEWER_PROMPT` / `ROLE_PROMPTS` 等）；grep 证实 `api/brainstorm_routes.py` / `session_routes.py` 不再 import v1 模块，v2 是唯一写源 | 全量用例 661 → **646**（-27：session_store 9 + interviewer 8 + canvas_parser 10）；`python -c "import main"` 通过 |
| ② 引用归属校验（AC-05） | `services/evidence_guard.py::check_role_result_refs` / `assert_role_result_refs` + `session_repo.list_referenceable_evidence_ids`（含公开知识域 `session_id IS NULL`）/ `list_session_message_ids`，接到 `workflows/brainstorm.py::_validate_role_result` | `tests/test_evidence_guard.py` 7 例；`test_brainstorm_runner.py` 2 例（越权 → 回喂一次后 `MODEL_OUTPUT_INVALID`、合法引用可通过） |
| ③-1 崩溃注入（AC-36） | `tests/support/control_worker.py`（独立进程假 LLM + 真 `RunWorker`，`mode=slow` 在 `audit.answer` sleep 8s）与 `tests/test_control_crash.py`：真子进程生成中途 `kill()` → 结束意图落库 → 租约过期后新 Worker 接管 | `tests.test_control_crash` 3 例 OK（11.6s）：旧 Run `cancelled`、旧生成零写入、唯一 `end_audit` closing Run `succeeded`、审计 `completed` + 阶段 `audit_completed` |
| ③-2 审计上下文守卫（AC-41）+ 取消接管回归（AC-30） | `retry_run` 新增「`session.current_audit_id != original.audit_id` 即 409 `RETRY_AUDIT_CONTEXT_CHANGED`」；`run_repo._claim_once` 接管租约过期的 `cancel_requested` Run 时保持该状态（原实现无条件重置为 `running`，取消意图被静默复活） | `test_retry_rejected_when_newer_audit_same_product_version`（Run 数不变、旧审计仍 failed）；`test_cancel_requested_survives_lease_takeover`（接管后仍 `cancel_requested`，执行器直接提交 `cancelled`、零步骤） |

## 第 10 批热修（旧会话 ID 别名表，2026-11-22）

| 修复 | 证据 | 验证 |
| --- | --- | --- |
| 老 12 hex 会话 ID 的旧链接 404 | 新表 `session_id_aliases`（`repositories/database.py::SessionIdAliasRow`，legacy_id 主键 + session_id 外键级联 + source_path/created_at）；迁移器 `_build_alias_row` 在 `legacy_id != session_id` 时写入；`session_repo.record_session_alias` / `resolve_session_id` / `list_session_aliases`；`api/v2/common.py::require_owned_session` 先解析别名再走原归属校验 | `tests/test_session_alias.py` 6 例 OK：迁移写别名、旧链接 `GET /api/v2/sessions/{12hex}` → 200 且返回 32 hex 会话、未知 ID 404、他人会话 403、软删后 404、幂等与参数校验；迁移导入 12 例仍 OK；合成源迁移演练 `DRILL_OK`（`session_id_aliases` 2 行，重复 apply 与回退重迁计数一致、源哈希不变） |

## 第 11 批热修（AC-14 只重调失败专家，2026-11-23）

| 改动 | 缺陷/差距 | 修复与证据 |
| --- | --- | --- |
| ① 单专家瞬时错误不再拖垮整轮 | 角色循环只捕获 `AppError`，`_run_role` 对任何 `LLMError` 都是「写 step 后 `raise`」→ 一个专家超时就让整轮 `failed`，已成功角色不提交、不标 `partial_succeeded`（违反 AC-14 第一条判据） | `workflows/brainstorm.py::_run_role` 区分：`retryable=True`（`LLMUnavailableError`/`LLMRateLimitedError`/`LLMOutputInvalidError`）记为角色级失败、其余角色继续 → `partial_succeeded`；账号级（401/402）仍整轮失败并保留真实错误码；所有角色均瞬时失败才上抛给 runner 的步骤级重试 |
| ② 部分成功不自动排 synthesis | 原实现无论是否有角色失败都 `follow_up=TaskKind.synthesis`，synthesis 子 Run 占住 `active_run_id` → 用户点重试一律 `SESSION_BUSY`，AC-14「重试只调失败专家」实际不可达 | `follow_up=None if failed else TaskKind.synthesis` + 追加提示「主持人归并未自动开始；请先重试失败专家」；重试成功后由该 Run 排入「必要汇总」 |
| ③ 部分成功 Run 允许重试 | `retry_run` 的 `base_product_version != session.current_product_version` 检查会拒绝 partial Run（它已提交 v+1）→ `RETRY_CONTEXT_CHANGED` | `services/command_service.py`：`partial_succeeded` 且 `result_product_version == session.current_product_version` 时放行；归并依赖 `merge_role_results` 的 claim 文本去重，结果为 v+1 的增量版本 |
| ④ 复用角色不重复发言 | 子 Run 把复用角色的消息再发一遍，聊天里同一专家说两次 | `runner._retry_scope_for` 增 `parent_committed`（父 Run 为 `partial_succeeded` 时其消息已提交）；`workflows/brainstorm.py` 仅在该标记下跳过复用角色的对外消息 |
| ⑤ 前端可达 | partial Run 是终态且无 errorMessage，聊天区不会出现错误条（`latestChatFailure` 只认 `failed`），用户无法从 UI 触发 AC-14 重试 | 新增 `frontend/lib/chatNotices.ts::latestPartialBrainstormRun`（同会话 `partial_succeeded && brainstorm` 取最新）；`components/chat/ChatPanel.tsx` 加琥珀提示条「有专家本轮失败，可重试失败专家补齐」+ 关闭按钮（按 runId 记忆，换会话重置）；`tests/chat-notices.test.cjs` 新增 4 例 |

证据：`tests/test_brainstorm_runner.py::test_retry_reruns_all_failed_roles_and_enqueues_synthesis`（超时 + 结构失败 → 父 `partial_succeeded`、子 Run 失败步骤 = {ops, target_user}、只调这两个、产品版本 [1,2,3,4]、4 条 agent 消息、synthesis 子 Run 指向重试 Run 且 `base_product_version=4`）；回归 `tests.test_brainstorm_runner` 13 例 OK；后端全量 653 例 OK；前端 249 例 + `tsc` 0 错（新增 4 例覆盖 partial 入口）。

## 第 12 批热修（AC-10 崩溃后预留预算不丢，2026-11-23）

| 改动 | 缺陷/差距 | 修复与证据 |
| --- | --- | --- |
| ① 预留额度在发请求前落盘 | `BudgetedLLM` 只在调用**结束后**才把用量写回（`_persist_budget` 仅在工作流返回/重试前调用），进程若在「模型已响应、结果未提交」处崩溃，这次调用的次数与 token 完全丢失；接管者从旧账本继续，反复崩溃可无限多花 | `runner._persist_budget` 接线到 `BudgetedLLM(on_reserve=...)`，在 `begin_call()` 之后、发请求之前写入；`as_dict()` 增加 `reserved_prompt_tokens` / `reserved_completion_tokens`，`_restore_usage` 把上一 epoch 的预留按已消耗计入 → 崩溃一次 + 重跑一次 `upstream_calls == 2` |
| ② `attempt_count` 只按真实提交推进 | 步骤尝试次数随 `start_step` 递增，崩溃后接管会再 start 一次；配合 ① 保证总尝试 ≤ 2 落库可查 | `test_budget_crash.py` 断言崩溃后 step `running` 且 `attempt_count == 1`，接管完成后为 `succeeded` |

证据：`tests/test_budget_crash.py` 4 例 OK（`BudgetLedgerUnitTests` 2 例账本单测 + `BudgetReservationCrashTests` 2 例真 `os._exit(9)` 子进程崩溃/接管）；回归 `tests.test_runner tests.test_crash_atomicity tests.test_command_service tests.test_control_crash tests.test_brainstorm_runner tests.test_audit_workflow tests.test_loop_workflow` 124 例 OK；后端全量 657 例 OK。

## 第 13 批热修（真实浏览器 E2E：AC-01 / AC-19 / AC-31，2026-11-23）

| 修复 | 证据 | 验证 |
| --- | --- | --- |
| AC-01 刷新三次不重复 | `scripts/browser_e2e/run_browser_e2e.py::scenario_ac01`：CDP 驱动真 Chrome 打开会话页后 `reload()` ×3，前后比对库内 runs/messages/session_version | PASS：页面上原始用户消息恰好 1 条，`messages=2`、`runs=1`、版本未推进、无未捕获异常；截图 `docs/browser-qa/e2e/ac-01-refresh-x3.png` |
| AC-19 八分支不折叠 | `scenario_ac19`：种子产品状态产出 8 个分支（技术/设计/商业/用户/方案/决策/未决/实验），逐个断言标题可见、无「+N 条更多」，并检查第 8 分支节点原文与引用计数 | PASS：8 分支全渲染、`v2` 版本徽标与「证据/消息」计数可见；截图 `ac-19-eight-branches.png` |
| AC-31 换账号零残留 | `scenario_ac31`：点真实「退出登录」按钮 → 换 B 的 Cookie 登录 → 断言历史只有 B、storage 无 A 会话 ID、越权打开 A 会话显示「加载失败」 | PASS；截图 `ac-31-account-a.png` / `ac-31-account-b.png` |
| 零新依赖浏览器驱动 | `scripts/browser_e2e/cdp.py`（`websockets` + stdlib，`--headless=new`；因 venv Python 3.12 的句柄继承会让 Chrome 以 `STATUS_BREAKPOINT` 退出，本机 headless 追加 `--no-sandbox`）+ `seed.py`（走真实仓储直接播种，不调模型、不耗额度） | `run_browser_e2e.py` 连跑 2 次均 3/3 PASS（约 19s），种子会话自动硬删（`cleanup=3`，库里无残留） |

证据：`cd backend && ../.venv/Scripts/python.exe ../scripts/browser_e2e/run_browser_e2e.py` → `[PASS] AC-01 / [PASS] AC-19 / [PASS] AC-31`，`failures: []`，`elapsed_seconds: 19.22`。

## 第 14 批热修（真实浏览器 E2E：AC-33 摄像头/STT/TTS 降级，2026-11-23）

| 修复 | 证据 | 验证 |
| --- | --- | --- |
| 摄像头被拒仍可完成文字审计 | `scripts/browser_e2e/run_browser_e2e.py::scenario_ac33`：点「同意并开启摄像头面试」后断言 `[role="alert"]` 文案含「摄像头」（headless 默认 `NotAllowedError`，无需 CDP 授权），再点「继续仅文字面试」断言 textarea 可输入 | PASS：真实拒绝原因浮层 + 文字面试可继续；截图 `ac-33-camera-denied.png` |
| 引擎说明如实 / 播放停止 / 麦克风跟随实际音频 | 同场景：TTS 用 `Page.add_init_script` 替换 `/api/voice/tts` 返回 1.5s 静音 WAV；断言服务端 STT 未配置时页面无「AI 高精度转写」、播放中麦克风 `disabled`、切「文字」后「正在发言…/正在播报」消失 | PASS：三条状态断言全部成立；截图 `ac-33-voice-playing.png` |
| STT 未配置时错误不静默 + 文字回答真实推进审计 | 点麦克风按钮 → 断言 `[role="alert"]` 文案含「麦克风」；在 textarea 输入真实访谈内容并发送 → 轮询库内 `audits.active_question_id`/`question_count` 变化 | PASS：`audit_after={status:active,count:2}`（Worker 真跑回答并生成第 2 问）；截图 `ac-33-text-answer.png` |

证据：`cd backend && ../.venv/Scripts/python.exe ../scripts/browser_e2e/run_browser_e2e.py AC-33` → `[PASS] AC-33`，`elapsed_seconds: 18.07`，`failures: []`，`cleanup: 1`；随后回归 `run_browser_e2e.py AC-01 AC-19 AC-31` → 3/3 PASS（`elapsed_seconds: 11.06`）。本轮真实模型消耗约 6–8K tokens（audit.open + audit.answer 各一次）。

## 第 15 批热修（AC-34 新构建失败→切回上一兼容构建回滚演练，2026-11-23）

| 修复 | 证据 | 验证 |
| --- | --- | --- |
| 回滚演练可重复、不碰生产库 | `scripts/ac34_rollback_drill.py`：`sqlite3.backup()` 从 `backend/data/app.db` 做一致性快照到 `backend/.tmp_tests/ac34-*/rollback.db`，全部 DDL 只在副本上执行 | 演练结束 `finally` 删除工作目录；生产库文件路径仅用于只读快照 |
| 坏构建的两种失败形态 | ① `alter table sessions add column rollout_flag text not null`（SQLite 拒绝：`Cannot add a NOT NULL column with default value NULL`）② 加可空列 + 建 `rollout_journal` + 写 marker 后启动自检失败 | 两种形态都被捕获并记录在报告 `failed_build` 字段 |
| 切回后消息/任务不丢 | 回滚（`drop table`/`drop column`/幂等 `initialize_database()`）后，逐表行数、会话/消息/Run ID 集合、每会话产品状态最大版本与回滚前完全一致；在第二份副本上 `claim_next_run` 领取「切换时排队」的注入任务 | PASS：`ROLLBACK_DRILL_OK`；`claim_check.claimed == inflight.run_id`（`status: running`）；`readable_sessions: 14` |
| 数据库状态可读 + 无 JSON 双写 | 每个会话经 `session_repo.get_session(include_deleted=True)` 与 `list_session_runs` 读取；`pragma integrity_check=ok`、`foreign_key_check` 0 行；`data/sessions` 全量文件 sha256 指纹前后一致 | PASS；`sqlite_version: 3.53.1`（支持 `DROP COLUMN`） |

证据：`cd backend && ../.venv/Scripts/python.exe ../scripts/ac34_rollback_drill.py` → `ROLLBACK_DRILL_OK`，`ok: true`；基线 `sessions=14 / messages=25 / product_states=21 / runs=25 / steps=22 / events=96`，注入排队任务后 `runs=26 / events=97`，回滚后计数与注入后完全一致；`json_dir_fingerprint_before == after`。

## 第 16 批热修（AC-20 数字可追溯性 + 推理型模型适配，2026-11-24）

| 修复 | 证据 | 验证 |
| --- | --- | --- |
| 无来源数字必须被标注，不能当事实 | 新增 `backend/services/number_guard.py`（`extract_numbers` / `number_is_traceable` / `verification_suggestion` / `audit_role_result`）；`CandidateClaim`/`Claim`/`CanvasNode` 增 `unverified_numbers`，节点增 `verification_suggestion`；画布橙色徽标「数字未验证 …」 | `test_number_guard.py`(18) + `test_canvas_projector.py`(2) + `test_brainstorm_runner.py`(1)；真模型 E2E 19 条论断中 7 个节点被标注，12/6/4 有来源未误标 |
| 推理型模型把 completion 预算花在 reasoning 上导致 content 为空 | `Settings.llm_disable_reasoning`（默认开）+ `LLMClient._reasoning_extra_body()` → `extra_body={"thinking": {"type": "disabled"}}`；`SCHEMA_REMINDER` 在对话末尾复述字段名与长度上限 | 实测单角色 completion 1500→380，content 恢复；`test_llm_client.py` / `test_brainstorm*` 全绿 |
| 供应商不支持 `json_schema` 时每次调用白付一次 400 | `LLMClient._json_schema_supported` 缓存降级结论，同进程后续直接 `json_object` | `test_json_schema_downgrade_is_cached_per_client`：3 次调用只有 1 次 400，上游调用从 8 降到 4–5 |
| 模型把上下文里的 `[#seq]` 写进 `source_message_ids` 被误判越权 | `evidence_guard.normalize_message_refs` + `session_repo.list_session_message_refs`：序号确定性解析为本会话真实消息 ID（映射不到仍拒绝） | `test_evidence_guard.py::NormalizeMessageRefsTests`(3)；实跑四角色全部 `succeeded`（此前 3 个角色 `MODEL_OUTPUT_INVALID`） |

证据：`cd backend && ../.venv/Scripts/python.exe ../scripts/ac20_number_provenance.py` → `run_status=succeeded`、`claim_count=19`、`unverified_nodes=7`、`calls=8 / prompt=19460 / completion=5786`；报告 `docs/ac20-number-provenance.md`，人工核对结论 `docs/ac20-number-provenance.annotation.md`（无来源数字 100% 标注、有来源数字 100% 未误标、`evidence_refs` 未编造）。

## 第 17 批热修（结项报告形状容错 + 真实模型报告路径验收，2026-11-24）

| 问题 | 修复 | 证据 |
| --- | --- | --- |
| 结项报告 100% 落兜底：模型多输出 `type` 等元字段、`limitations` 写成数组，`StrictModel(extra="forbid")` 校验失败 → `build_fallback_report`，真实审计报告从不来自模型（旅程 `end_audit` 3 次调用 / 8,326 tokens 仍 `fallback`） | `ReportDraft` 增加 `@model_validator(mode="before")` 有界归一（白名单字段、数组/文本互相容错、长度上限仍生效）；`_report_instruction` 明确「不要额外字段、`limitations`/`summary` 为单字符串」 | `tests/test_audit_report_draft.py` 5 例；`scripts/audit_report_e2e.py` 真模型 `generated_by=model`（8,078 tokens）；`docs/audit-report-e2e.md` |
| 缺一条「真实模型全链路」证据（clarify→brief→四角色→synthesis→审计→报告） | 新增 `scripts/full_journey_e2e.py`：7 步真模型旅程，输出产物/来源/成本表 | `docs/full-journey-e2e.md`：53,910 tokens、18 条论断、8 个分支、5 个未验证节点、审计 2 问 completed |

## 第 18 批热修（结项报告摘要端到端，2026-11-24）

| 问题 | 修复 | 证据 |
| --- | --- | --- |
| 模型生成的 `summary` 被直接丢弃：`ReportDraft.summary` 有值，但 `AuditReport` 契约没有该字段，`_apply_report_draft` 也不透传 → 报告页只有维度/论据/缺口，没有一段话总结（白花输出 token） | `AuditReport` 增加 `summary`（≤4000 字）；`_apply_report_draft` 透传模型摘要、为空时回退兜底摘要；`build_fallback_report` 生成确定性摘要（问题数 + 已支撑/缺口/未讨论计数）；前端 `NormalizedReport.summary` + 「报告摘要」Section | `test_audit_workflow.py`（模型摘要原样落库、兜底摘要含计数）、`test_audit_runner.py`（closing Run 报告必须有摘要）、`frontend/tests/audit-page.test.cjs`（摘要归一化 2 处）；真模型 `scripts/audit_report_e2e.py`：`generated_by=model`、摘要 562 字、8,054 tokens |
| 模型层问题不可诊断：`/health/detail` 只有服务/预算，出了「结构化输出为空」只能翻 `.env` | 新增 `llm` 诊断块（模型名、base_url 主机名、`disable_reasoning`、超时/重试）且不泄露 Key | `tests/test_health_detail.py` 3 例（字段存在 + 无 `sk-`/`api_key` + 未登录 401 + `_url_host` 只留主机名） |

## 第 19 批热修（文档索引 + 引用体检，2026-11-24）

| 问题 | 修复 | 证据 |
| --- | --- | --- |
| 文档没有总索引：根 `README.md` 平铺十余条，`docs/` 下 17 个条目（含 5 个子目录）只能靠猜，新读者不知道先读哪份、哪份已过期 | 新增 `docs/README.md`：按「发布与验收 / 架构与接口 / 运行与真实链路证据 / 逐批记录 / 历史审计」五组，表格列「文档 / 内容 / 何时读」；根 `README.md` 的 `### 文档` 段改为索引指针 + 最常用 8 条 | `docs/README.md`（5 组 17 条 + 维护约定）、`README.md` |
| 文档引用会腐烂：已删除的 v1 模块（`~~backend/db/session_store.py~~` 等）、改名后的截图路径仍以「现存文件」形式出现在文档里，人工 review 逐条点不现实 | 新增 `scripts/verify_docs_refs.py`（零依赖）：扫 `docs/**/*.md` + `README.md`/`AGENTS.md`，校验 Markdown 链接与反引号里的仓库路径；支持花括号展开 `{a,b}.py`、剥离 `::name` 与 `:12-30` 行号、跳过 `~~删除线~~`、已删除模块白名单、唯一 basename 兜底；默认报告模式、`--strict` 失败 | 实测 25 个文件 / 601 条引用 / 0 断链；坏引用注入探针同时命中 `[code]` 与 `[link]`；已接入 CI（backend job 的 `docs reference check` 步骤） |

## 第 20 批热修（Worker 卡死可见 + 排队提示，2026-11-24）

| 问题 | 修复 | 证据 |
| --- | --- | --- |
| 「发消息一直没反应」无法定位：命令先落库排队等 Worker 领取，Worker 没起来时命令只会排队到 `QUEUE_TIMEOUT`（10 分钟）才失败；而 `/health/detail` 只报 `running_runs/queued_runs`，看不错「有排队但没人领」 | `/health/detail` 的 `worker` 块增加 `heartbeat_age_seconds` / `stalled` / `hint`：有排队任务且（无 running 或心跳超过 120s）即判 `stalled=true`，顶层 `status` 同步降级为 `degraded`，`hint` 直接给出 `start.bat` / `python -m workers.run_worker`；抽出纯函数 `_worker_stall_hint` 便于单测 | `tests/test_health_detail.py` 3 例（工作块一致性、stalled 判定 5 种输入、心跳时间解析容错） |
| 排队状态对用户不可见：前端聊天区只显示消息条数，任务排队时用户不知道是「系统在忙」还是「Worker 没启动」，只能反复点发送 | `frontend/lib/chatNotices.ts` 新增 `queuedRunHint(runs, sessionId, {nowMs, thresholdSeconds=45})`（按最早排队 Run 的 `createdAt` 计时）；`ChatPanel` 只在存在 queued Run 时启动 5s tick，超阈值渲染灰色状态条「任务已排队 N 秒，Worker 可能未启动：请运行 start.bat…」 | `frontend/tests/chat-notices.test.cjs` 新增 5 例（未超阈值不提示、超阈值提示含命令、多任务按最早计时、跨会话/非排队/缺时间不提示）；`npm test` 254 pass + `tsc --noEmit` 0 错 |

## 第 21 批热修（发版备份 / 校验 / 恢复演练，2026-11-24）

| 问题 | 修复 | 证据 |
| --- | --- | --- |
| 发版第一步「备份 `app.db` + `data/sessions/`」只有手工 `copy` 的建议：库正被 Worker 写入时 `copy` 可能拿到半页状态；备份是否可用也从没演练过，真出事才发现缺表/损坏 | 新增 `scripts/backup_restore.py`：`backup` 用 SQLite online backup API（写入进行中也能拿一致性快照）+ 复制 `data/sessions/` + 写 `manifest.json`（db sha256/字节数/逐表计数/JSON 指纹），支持 `--keep N` 轮转；`verify` 校验哈希/manifest 计数/`integrity_check`/外键；`drill` 把备份恢复到临时目录、`initialize_database()` 后逐个会话走 `get_session` + `list_session_runs` 读路径 | 实测：`BACKUP_OK`（585,728 字节 / 19 个 JSON 文件）→ `VERIFY_OK sessions=14 messages=25 runs=25 integrity=ok fk=0` → `RESTORE_DRILL_OK sessions=14 readable_sessions=14`；命令已进 `docs/v4-release-checklist.md` 第 1 节；回归 `tests/test_backup_restore.py` 4 例（备份后 manifest 计数一致、篡改后 verify 必须失败、drill 恢复后应用层可读、`--keep` 轮转）；期间修掉脚本自身缺陷：同一秒内连续备份会 `FileExistsError`（改为时间戳 + 序号后缀） |

## 第 24 批（维护性数据清理：幂等记录 + 过期验证码，2026-11-24）

| 问题 | 修复 | 证据 |
| --- | --- | --- |
| `idempotency_records` 每条命令一行、`auth_codes` 每次登录一行，两者过期后都没有读者；长期运行会随使用量线性增长，把库撑大并拖慢索引 | 新增 `run_repo.prune_idempotency`（按 `created_at < now-7d`，单次上限 5000 行）与 `auth_repo.prune_expired_codes`（`expires_at < now`，单次 2000 行）；`RunWorker.tick()` 每 `MAINTENANCE_INTERVAL_SECONDS`（6 小时）调一次，异常只记日志、绝不阻塞任务执行（与事件压缩同一模式） | 新增 `backend/tests/test_worker_maintenance.py` 4 例（只清窗口外、单次上限 + 幂等、保留未过期验证码、tick 按间隔触发且第二轮跳过）；后端全量 713 用例 OK；`scripts/disk_cleanup.py` 报告可直接看到 `backend/data/app.db` 体积变化 |

## 第 25 批（audit / loop 页面的真实渲染测试，2026-11-24）

| 问题 | 修复 | 证据 |
| --- | --- | --- |
| 两个页面只有「纯函数 + 文件 token 正则」断言（`tests/audit-page.test.cjs`、`tests/loop-page.test.cjs`）：页面里的条件渲染分支整段删掉，测试仍然是绿的 | 新增 `frontend/tests/pages-render.test.cjs`，用 `react-dom/server` 渲染**整个页面组件**；为 `next/navigation`（useParams/useSearchParams/useRouter）与 `next/link` 提供零依赖替身，并用 `useSyncExternalStore` 的 server snapshot 指向真实 snapshot，使 SSR 能读到播种的 store 状态；`tsconfig.render.json` 纳入两个页面 | 6 例全绿：完成态审计渲染「报告摘要」「维度评估」与问题原文；读取失败渲染错误态且不出现「开始审计」；会话未加载禁用开始按钮并给「重新加载会话」；闭环页阶段门文案；允许启动时的启动面板（含范围/轮次/目标分）；加载中态。前端 254 → **260** 用例、`tsc --noEmit` 0 错 |

## 第 26 批（中间载荷保留策略 + 数据库增长报告，2026-11-24）

| 问题 | 修复 | 证据 |
| --- | --- | --- |
| 每轮头脑风暴会写 4-8 条 `steps.input_snapshot_json`（发给模型的完整上下文）与角色结果 `output_json`，候选表另有 `payload_json`；这些只是「重试复用 + 排查」用的中间数据，长期不清理会让库随任务量线性增长（用户可见的正文只有 messages / product_states） | 契约新增 `LLM_PAYLOAD_RETENTION_DAYS = 90`（0 关闭）；`run_repo.prune_intermediate_payloads` 只清「终态 + 已结束 + 早于窗口」的 Run，**跳过**重试父 Run（AC-14 复用）与 `loops.latest_run_id`（Loop 重试），做法是删 `step_candidates` 行 + 把 `input_snapshot_json`/`output_json` 置 `"{}"`，Run/Step 骨架与状态、错误码全部保留；`RunWorker.tick()` 在维护阶段按同一间隔调用 | `backend/tests/test_payload_retention.py` 3 例（只清窗口外、跳过重试父 Run、跳过 loop latest、limit 分批且幂等）+ `test_worker_maintenance.py` 补 1 例；后端全量 720 用例 OK |
| SQLite 删除行后不会自动缩小文件（`freelist` 页一直占着磁盘），此前没有任何工具能回答「库为什么这么大、还能不能变小」 | 新增 `scripts/db_maintenance.py`：`report` 用 `dbstat` 输出每表字节 + 行数 + 文本列总长（无 dbstat 时按页数估算），`vacuum` 执行 VACUUM 并回报前后体积、回收空闲页 | `backend/tests/test_db_maintenance.py` 3 例（报告含表名/行数、VACUUM 后 `freelist_pages=0` 且 `integrity_check=ok`、JSON 可序列化）；线上库实测 `freelist=16 页` → VACUUM 后 0 页 |

## 第 28 批（报告正文的数字可追溯，2026-11-24）

| 问题 | 修复 | 证据 |
| --- | --- | --- |
| 画布里的无来源数字会标「数字未验证」，但**审计报告正文**（支持论据 / 未解决缺口 / 建议实验 / 局限性 / 摘要）里的数字没人核对：报告读起来像事实，用户分不清哪些来源里有、哪些是模型编的 | `AuditReport` 新增 `unverified_numbers` + `verification_suggestion`；`number_guard.audit_report_texts()` 复用同一套规则（数字必须出现在来源消息/证据原文里）；`_apply_report_draft` 落库前标注并写 warning；兜底报告不标注（其中的计数来自结构化记录而非模型） | `test_number_guard.py` 报告侧 2 例（2/66/5 标、12/6 不标、空报告无标注）+ `test_audit_workflow.py::test_report_marks_numbers_absent_from_sources`；前端 `audit-page.test.cjs` 归一化 1 例 + `pages-render.test.cjs` 报告顶部徽标 1 例（前端 260 → 262） |

## 第 27 批（浏览器 e2e 进 CI + 体检加库增长步骤，2026-11-24）

| 问题 | 修复 | 证据 |
| --- | --- | --- |
| 真实浏览器验收（AC-01/19/31）只在本机跑过，CI 里没有，回归靠人记；且 `cdp.py` 只认 Windows 的 Chrome 路径、只按 `taskkill` 清理进程树，Linux runner 上跑不起来 | `cdp.find_chrome()` 改为 `CHROME_PATH` → Windows 常见路径 → PATH（`google-chrome`/`chromium` 等）；POSIX 下 Chrome 以独立进程组启动、关闭时 `killpg(SIGKILL)`；新增 CI job `browser-e2e`（ubuntu 自带 Chrome，起 API + Next 后跑三场景，截图作 artifact） | `scripts/browser_e2e/run_browser_e2e.py AC-01 AC-19 AC-31` 本机仍全 PASS（AC-01 单跑 9.4s）；`yaml.safe_load` 校验通过（backend 6 / browser-e2e 11 / frontend 9 步） |
| 发版体检不看库增长，删了一堆数据也不知道文件有没有瘦 | `pre_release_check.py` 新增 `db-report` 步骤（`db_maintenance.py report`，只读；输出库大小与空闲页），排在 backup-drill 之后 | `python scripts/pre_release_check.py --only docs,db-report` → `PRE_RELEASE_OK 2 步 / 2.3s`，`db-report` 报「库大小 504.0KB，空闲页 0」；自检 7 例仍 OK |

## 第 29 批（数字未验证 → 补充来源闭环，2026-11-24）

| 问题 | 修复 | 证据 |
| --- | --- | --- |
| 徽标只能看不能处理：前端没有证据录入入口，且补了证据也不会反映到已落库的报告 | 徽标旁加「补充来源」按钮 → `EvidenceForm`（摘录必填、≥8 字）→ store 新增 `addEvidence`（走 `add_evidence` 命令）；`audit_repo._remark_report_numbers` 在**读取时**按最新消息/证据重算标注，补完证据徽标即消失（零模型成本） | `test_audit_report_recompute.py`(3)、`evidence-form.test.cjs`(3)、`pages-render` 入口 1 例；726 后端 / 266 前端 |

## 第 30 批（报告质量信号进 /health/detail，2026-11-24）

| 问题 | 修复 | 证据 |
| --- | --- | --- |
| 「模型是不是老在编数字」只能靠人翻报告：`/health/detail` 只报服务/预算/模型层，没有内容质量信号 | `audit_repo.report_quality_stats`（只读：窗口内报告份数、含无来源数字的份数、数字总数、前 5 个样本）+ `_report_quality_status` 阈值判定（`no_data`/`insufficient`/`clean`/`occasional`/`frequent`，≥50% 判 frequent 并提示改提示词）+ `/health/detail` 新增 `report_quality` 块（默认 24h 窗口） | `test_report_quality.py` 5 例（阈值 4 + 接口 1）；731 后端 / 266 前端 |

## 第 31 批（报告质量窗口回落，2026-11-25）

| 问题 | 修复 | 证据 |
| --- | --- | --- |
| 安静一天后 `report_quality` 永远只答 `no_data`：排障时「没有信息」与「一切正常」看起来一样 | `_report_quality` 先用 24h 窗口，判定为 `no_data`/`insufficient` 且 7 天内样本更多时改用 7 天统计，返回 `window=7d` + `fallback_from=1d`，并在 hint 里说明回落 | `test_report_quality.py` 新增 4 例（回落、无需回落、长窗也无数据、库异常降级）；735 后端 / 266 前端 |

## 第 32 批（账户级配额：防单个账号反复烧额度，2026-11-25）

| 问题 | 修复 | 证据 |
| --- | --- | --- |
| 原有预算只按「单 Run」收紧：一个脚本循环提交 brainstorm、或多人共用一个账号，可以把上游额度烧穿而没有任何拦截 | 新增 `services/quota_service.py`：`usage_since` 用 `usage_json` 统计已消耗（含崩溃遗留预留），未终结 Run 按 `budget_json` 上限预占，因此并发提交不会集体越过配额；`ensure_within_quota` 在 `submit_command` 与 `start_initial_run`（建会话）两处网关拦截，超额抛 429 `ACCOUNT_QUOTA_EXCEEDED` 并带已用/上限/重置时间；改稿、登记证据、确认决策、取消、结束审计等同步命令不受限，避免额度耗尽后连收尾都做不了；新增 `GET /api/v2/quota` 供前端显示剩余额度；上限来自 `ACCOUNT_DAILY_TOKEN_LIMIT` / `ACCOUNT_DAILY_CALL_LIMIT`（默认 0=不限，只收紧不放宽） | `test_account_quota.py` 9 例（终结 Run 实计 + 排队预占、他人配额隔离、异步拒绝、同步放行、仅次数上限也拒绝、`/quota` 视图、命令 429、建会话 429）；744 后端 / 266 前端 |

当前基线：**后端 744 用例 OK / 前端 266 用例 + `tsc --noEmit` 0 错 / 真实浏览器 e2e AC-01+AC-19+AC-31+AC-33 PASS / 回滚演练 ROLLBACK_DRILL_OK / AC-20 真模型端到端 7 个未验证节点（人工核对） / 完整用户旅程真模型端到端 53,910 tokens / 结项报告真实模型路径 `generated_by=model` + 摘要落库（562 字） / 文档引用体检 601 条 0 断链（CI 已接入） / Worker 卡死诊断（`stalled`+`hint`）与前端排队提示 / 备份 `BACKUP_OK`+`VERIFY_OK`+`RESTORE_DRILL_OK` / 发版体检 `PRE_RELEASE_OK`（8 步，含 db-report） / 磁盘清理脚本（报告 + `--apply`） / 中间载荷 90 天保留策略 + `db_maintenance.py report|vacuum` / 账户级配额（429 `ACCOUNT_QUOTA_EXCEEDED` + `GET /api/v2/quota`）**。

## 第 22 批（发版前一键体检，2026-11-24）

| 问题 | 修复 | 证据 |
| --- | --- | --- |
| 发版前要记住 7 条命令、逐个判断结果容易漏项：文档体检、后端全量、前端类型/用例、真实浏览器 e2e、备份演练各自有输出格式，人工汇总易出错 | 新增 `scripts/pre_release_check.py`：按 `docs → backend-import → backend-tests → frontend-types → frontend-tests → browser-e2e → backup-drill → db-report` 串行执行，逐步打印 `[OK]/[FAIL]` 与耗时，末尾给 `PRE_RELEASE_OK` / `PRE_RELEASE_FAILED`；支持 `--list`、`--only`、`--skip`、`--skip-browser`、`--json`、`--keep-going` | 实测 `PRE_RELEASE_OK  8 步`（docs 0.7s、backend-import 1.6s、backend-tests 69.4s、frontend-types 2.7s、frontend-tests 9.9s、browser-e2e 14.7s 3/3、backup-drill 2.3s、db-report 1.1s） |
| 服务没启动时浏览器 e2e 会白等到超时，且报错只剩「连接被拒绝」 | `step_browser_e2e` 先探 `API/health` 与 Web 首页，未就绪直接失败并提示「先运行 start.bat，或用 --skip-browser」 | `backend/tests/test_pre_release_check.py` 7 例（步骤/超时表一致、`--list`、未知步骤退出 2、`--only`/`--skip` 过滤、输出裁剪、未就绪提示） |

## 第 23 批（磁盘卫生：清理脚本 + 浏览器进程树泄漏修复，2026-11-24）

| 问题 | 修复 | 证据 |
| --- | --- | --- |
| 本机 D 盘只剩 1.2GB：`npm config get cache` 指向 `D:\trae\node_cache`，实测 **18.1GB / 22.8 万个文件**，且每次安装依赖只增不减 | 清理 npm 缓存（`npm cache clean --force`）释放约 24GB；新增 `scripts/disk_cleanup.py`：默认只报告本项目可重建产物（e2e 临时 user-data-dir、`backend/.tmp_tests`、`.tmp_*` 脚本、超期备份、可选 npm 缓存 / `.next`），`--apply` 才删除，`--keep-backups N` 控制备份轮转 | 实测 D 盘 1.2GB → 25.5GB 空闲；`--apply` 清空遗留 e2e 日志与临时目录；`backend/tests/test_disk_cleanup.py` 5 例（文件/字节统计、备份轮转、类别过滤、临时产物识别、`--json` 报告） |
| e2e 崩溃/中断后无头 Chrome 子进程成树残留：`process.kill()` 只杀父进程，crashpad/gpu/renderer 继续持有 `user-data-dir`，临时配置目录删不掉（`Device or resource busy`），还持续占内存 | `scripts/browser_e2e/cdp.py`：Windows 下用 `taskkill /F /T /PID` 杀整棵进程树；新增 `atexit` 兜底关闭所有打开中的 Browser；`_shutdown_process` 幂等；加 `--disk-cache-size=1048576` 限制临时缓存 | 手工清掉 1 棵 8 进程的遗留树后，跑 `run_browser_e2e.py AC-01 AC-19 AC-31` 退出码 0，且事后 `browser-e2e-*` 临时目录 0 个、残留 Chrome 进程 0 个 |

## 运行方式

```bash
# 1) 把 skill 脚本作为 workflow 脚本执行（参数见文件头注释）
#    skills/loop-engineering-product/scripts/loop-workflow.js
#    典型 args：
#    { repoPath, maxRounds: 2, target: 95, focus,
#      backendTestCmd: "cd backend && ../.venv/Scripts/python.exe -m unittest discover -s tests",
#      frontendCmds: ["cd frontend && npx tsc --noEmit", "cd frontend && npm test"] }

# 2) 运行后做写域校验（防止修复越界）
python skills/loop-engineering-product/scripts/verify_write_scopes.py --baseline <运行前 commit>

# 3) 独立复跑（不信任 agent 自述）
cd backend && ../.venv/Scripts/python.exe -m unittest discover -s tests
cd frontend && npx tsc --noEmit && npm test
```

## 证据纪律（每次运行都必须满足）

1. 结论必须能落到 `文件:行号`、HTTP 请求或命令输出；推演不算证据。
2. 评测组必须先真跑至少一条验证命令，并把真实输出写进 verdict。
3. 禁止删用例 / 改期望值 / skip / 恒真断言换取绿色。
4. 跨进程或并发类结论必须真起进程或真并发，不得用单进程模拟。
5. 修不了的必须写进 `skipped` 并说明原因，不允许假装修好。
