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

## 第 35 批（会话共享：viewer 只读 / editor 可操作，2026-11-26）

| 问题 | 修复 | 证据 |
| --- | --- | --- |
| 会话只有唯一所有者：协作只能共用账号登录；所有读接口都用 `require_owned_session` 硬校验归属，「给同事看一眼」做不到 | 新增 `session_shares` 表（session_id + email + role + created_by，级联删会话）与仓储 `session_role / add_session_share / list_session_shares / remove_session_share`；鉴权拆三档 `require_session_read`（owner/editor/viewer 均可读）/ `require_session_write`（owner/editor 可提交命令，Run 与模型 Key 仍记在所有者名下）/ `require_session_owner`（增删共享、删除会话）；读接口统一改用 read 权限；`GET /sessions` 合并「我拥有的 + 共享给我的」；新增 `GET/POST/DELETE /api/v2/sessions/{id}/shares` | `backend/tests/test_session_sharing.py` 9 例（重复共享只改角色、viewer 三个读接口 200 但命令 403、editor 提交命令 202 且 Run 记在所有者名下、陌生人 403、非所有者不能增删共享、不能共享给自己、撤销后立即 403、被共享方列表可见）；全量后端 764 OK |

## 第 34 批（配额可视化：配置页用量卡 + 顶栏额度徽标，2026-11-25）

| 问题 | 修复 | 证据 |
| --- | --- | --- |
| 账户级配额只在接口里（`GET /api/v2/quota`），部署方设了上限后用户只能等 429 才知道超了；`ACCOUNT_DAILY_*` 默认 0=不限，更看不出自己用掉多少 | 新增 `frontend/lib/quota.ts`（`quotaSummary` 取 token/调用较高百分比、`formatTokens`、`resetInText`）与 `QuotaMeter`（`/settings/llm` 的「模型额度与用量」卡片：两条进度条 + 预占 + 恢复倒计时 + 超额时的「仍可做同步操作」提示）与 `QuotaBadge`（顶栏只在设了上限时出现，≥80% 琥珀、超额红色，悬停显示用量与恢复时间） | `frontend/tests/quota.test.cjs` 6 例（未设上限 / 百分比与明细 / 80% 警戒 / 超额 / 数字进位 / 倒计时归一）；真 Chrome 实测两种状态：未设上限时徽标不出现、卡片显示「未设上限」；临时设 60k/20 次上限后徽标显示「额度 0%」且悬停拼出「已用 0 / 60k tokens · 调用 0 / 20 次 · 约 24 小时后恢复」，验证后已恢复默认不限 |

## 第 33 批（账户自带模型密钥 BYOK + 登录强制配置，2026-11-25）

| 问题 | 修复 | 证据 |
| --- | --- | --- |
| 平台要替所有用户付模型费；而线上收款需要营业执照 + ICP 备案 + 公网 HTTPS 回调，本机暂时都做不到 | 改为 **BYOK**：新增 `llm_configs`（每账户一条，Fernet 加密、只回显末 4 位）；`llm_config_service.gate` 在建会话与提交需要模型的命令两处拦截，Worker 执行 Run 时再兜一层，未配置统一 428 `LLM_CONFIG_REQUIRED`；前端 `LlmConfigGate` 整屏引导 + `/settings/llm` 表单（保存并测试连通性）；`LLM_KEY_MODE=system` 仅供本地联调/自动化 | `backend/tests/test_llm_config.py` 11 例（加密脱敏 2 + 服务闸门 4 + 接口 3 + 执行器兜底 2）；`frontend/tests/llm-config.test.cjs` 5 例（闸门状态机）；755 后端 / 271 前端 |
| 前端闸门首版没生效：`lib/llmConfig.ts` 调 `apiV2("/llm-config")`，而 `apiV2` 不会补 `/api/v2` 前缀（其余调用都传完整路径）→ 请求打到 `:8001/llm-config` 得 404，闸门按「读配置失败放行」直接渲染业务页面 | 四个调用改为完整路径 `/api/v2/llm-config`（含 `/test`） | 真 Chrome（CDP）复验：未配置时整屏「先配置你自己的模型 API」→ `/settings/llm` 表单可用 → `PUT` 保存后刷新自动放行，输出 `BYOK_GATE_CDP_OK` |

## 第 37 批（团队工作区：成员组一次性授予多人，2026-11-26）

| 问题 | 修复 | 证据 |
| --- | --- | --- |
| 会话共享只能一个个加人：同一批同事（评审组、设计组）每次都要重输邮箱，且「组」的概念只在人脑里；成员的只读/可操作角色也没法按组统一 | 新增 `member_groups` + `member_group_members` 两张表（组名同 owner 唯一、成员邮箱复合主键、组级联删成员）与 `member_group_repo`（`create_group/list_groups/require_group/add_member/remove_member/delete_group/apply_group`，每组上限 50 人）；**组只存名单、不授予权限**——`POST /sessions/{id}/shares/from-group` 由会话所有者把组内成员按各自角色写入 `session_shares`（跳过所有者本人与已在列表里的人，返回 `added/already_shared/members_total`）；新增 `GET/POST /api/v2/groups`、`DELETE /api/v2/groups/{id}`、`POST/DELETE /api/v2/groups/{id}/members[/{email}]` | `backend/tests/test_member_groups.py` 8 例（建组/同名 422/重复加人改角色/不能加所有者/viewer 只读/陌生人 403/应用幂等 `added=0`/删组保留已授予访问）；前端 `app/settings/team` 团队工作区 + 会话页「共享」面板里的「从成员组一次性添加」；`frontend/tests/team-groups.test.cjs` 9 例 |
| 删组会不会把已发出去的访问权一起收回？语义必须说清 | 明确「组=名单、共享行=授权」：删组只删名单，已授予的 `session_shares` 保留（要收回得逐个移除共享） | 用例断言「删组后成员仍能读、移除共享行后 403」；页面文案同步写明 |

## 第 38 批（Loop Studio 真实执行沙箱 + /ops 运维健康页，2026-11-26）

| 问题 | 修复 | 证据 |
| --- | --- | --- |
| Loop Studio 的「开发/测试」阶段只能生成文本产物，没有真的跑过一次代码——用户无法验证 MVP 规格里的命令是否可执行 | 新增 `services/loop_sandbox.py`：**fail-closed**（`LOOP_SANDBOX_ENABLED` 默认关，且 `LOOP_SANDBOX_ALLOWED_COMMANDS` 为空时一律 503 `SANDBOX_DISABLED`）；argv 直传 `shell=False` 并拒绝 `&&`/`\|`/`;`/`>`/`$(` 等元字符；独立临时目录 + 剥离 `LLM_`/`SMTP_`/`AUTH_SECRET` 等敏感环境变量 + 超时 `taskkill /F /T` 强杀 + 输出截断；每次运行都作为 `sandbox_run` 产物落档（`loop_repo.append_artifact` 不去重）；新增 `GET /api/v2/sandbox`（能力视图）与 `POST/GET /api/v2/loops/{id}/sandbox-runs`（长任务分两段：先确认归属 → 关连接跑进程 → 重开库落证据，避免长时间占用 DB 连接） | `backend/tests/test_loop_sandbox.py` 16 例（默认关闭/空白名单/白名单外 422/4 组元字符/超长参数/成功 `passed`/非 0 退出 `failed`/超时 `timeout`/输出截断/多次追加/他人闭环 403/子进程看不到密钥/UTF-8 环境/临时目录清理）；前端 `components/loop/SandboxPanel.tsx` + `lib/loopSandbox.ts`（argv 解析与预校验）+ `tests/loop-sandbox.test.cjs` 9 例 |
| 线上排障要登服务器：Worker 是否卡死、库多大、队列多深、沙箱为什么是灰的，全都没有页面 | 新增 `/ops` 运维健康页（只读聚合 `GET /health/detail`）：`storage`（`page_count*page_size` + 8 张主表行数）、`queues`（排队/运行/最久排队时长）、`sandbox`（开关+白名单+超时）、`worker`（stalled + 可操作 hint）、`report_quality`、`budget_profile` 与 `llm` 块；页面附「常见问题排查顺序」 | `backend/tests/test_ops_health.py` 4 例；`frontend/app/ops/page.tsx` + `lib/ops.ts` + `tests/ops.test.cjs` 8 例 |
| 沙箱执行最长 60 秒，而前端 `apiV2` 默认 15 秒超时 → 按钮先报 `TIMEOUT` 但服务端还在跑 | `V2RequestOptions` 新增 `timeoutMs` 覆盖，沙箱调用按「服务端超时 + 10 秒」放宽 | `lib/loopSandbox.ts::runSandbox` 传 `timeoutMs`；`tsc` 通过 |
| 上线后没人确认「服务真在跑、新页面真能打开」，只能靠口头记忆 | 重启后以真 Cookie 跑一次冒烟：`/health`、`/health/detail` 的 `storage`/`queues`/`sandbox`/`worker`/`llm`/`report_quality` 六块、`/api/v2/sandbox`、`/api/v2/groups`、`/api/v2/quota`，以及 Web `/`、`/ops`、`/settings/team`、`/settings/llm` | `RELEASE_SMOKE_OK 17 checks`；发版体检 `PRE_RELEASE_OK 8 步 / 109.3s`（后端 792 / 前端 306 / 浏览器 e2e 3/3）；备份 `BACKUP_OK 20261126-125549`；提交 `38ad1fd` |

## 第 39 批（共享可见性与统一权限页，2026-11-26）

| 问题 | 修复 | 证据 |
| --- | --- | --- |
| 会话能共享，但「共享给我的」在历史列表里和「我创建的」长得一模一样；删除按钮对共享会话也显示（点了静默 403） | 后端 `SessionView` 与旧 `legacy_session_payload` 都新增 `my_role`（owner/editor/viewer，列表与详情都返回）；旧 `GET /api/session/{id}` 从 `require_owned_session` 改为 `require_session_read`（成员不再 403，写操作闸门不变）；前端历史抽屉按 `my_role` 显示「共享给我 · 只读/可操作」徽标，并只对所有者显示删除按钮 | `backend/tests/test_access_overview.py` 8 例（总览/角色/旧接口/退出/陌生人与未登录）；`frontend/tests/access.test.cjs` 8 例（汇总文案 + 权限页首屏 + 历史抽屉徽标与删除按钮） |
| 想知道「我把哪些会话共享给了谁 / 谁共享了会话给我 / 成员组都有谁」只能逐个会话点开，且成员无法自己退出共享 | 新增 `GET /api/v2/access`（owned 含 shares / incoming 含 role+owner_email / groups / totals）；新增 `DELETE /api/v2/sessions/{id}/share` 让成员退出自己的共享（所有者退出 422、陌生人 403）；新增 `frontend/app/settings/access/page.tsx` + 顶栏「权限」入口 | 后端 8 例外加真 Cookie 冒烟；前端 `/settings/access` SSR 断言三分区与入口；`session_repo.list_owned_sessions/list_incoming_shares/shares_by_session` 避免 N+1 |
| 发布验证 | 后端全量 `Ran 825 tests / OK`；前端 `314/314` + `tsc --noEmit` 0 错；`PRE_RELEASE_OK 8 步 / 178.4s`；真 Cookie 冒烟 28 项（含成员退出后 403、旧列表 `my_role`）；真 Chrome `ACCESS_CDP_OK` 13 项；停栈后 `npm run build` exit 0（12 条路由）再 `start.bat` 拉起，构建后冒烟 10 项全 200 | 提交 `4bcef3a`（功能）、`2c9334f`（验收证据）；截图 `docs/browser-qa/e2e/access-*.png`；备份 `BACKUP_OK 20261126-144004` |

## 第 46 批（数据持久化：SQLite 周期快照到私有 dataset，2026-11-27）

| 问题 | 修复 | 验证 |
| --- | --- | --- |
| HF 免费实例无持久卷：SQLite 在容器内，重启/休眠回收即清空用户、会话、审计、模型密钥（R1~R3 的 P0-1） | 新增 `backend/core/db_snapshot.py`：`sqlite3.backup()` 一致性拷贝 → gzip+base64（HF 拒收二进制）→ NDJSON 提交到私有 dataset；`container_start.py` **先恢复后建表**；`main.py` 起周期上传线程并在关闭时 `flush`；内容未变化则跳过 | 真实 dataset 往返 `uploaded: 643072 字节 → 101168 字符`、`restored: 643072 字节`（`sessions: 35 / messages: 46 / integrity: ok`）；`backend/tests/test_db_snapshot.py` 25 例；后端全量 `Ran 910 tests / OK` |
| `/health/ready` 只说「没有 DATABASE_URL」，看不出到底会不会丢、丢多少 | 新增 `persistence` 块（`kind`: `external`/`local-file`/`snapshot`/`ephemeral`，snapshot 带 `repo`、`interval_seconds`、`worst_case_loss_seconds`）；`warnings` 文案改为「最坏丢最近 N 秒写入」 | 线上 `/backend/health/ready` 返回 `persistence.kind=snapshot` |
| 部署自检把「只有快照」直接判 fail，误伤快照方案 | `check_deploy_environment.py::check_database` 认可 `DB_SNAPSHOT_REPO`+token，判 warn 并写明「最坏丢一个周期」 | `python scripts/check_deploy_environment.py --env-file backend/.env` 7 项 0 fail |

## 第 45 批（R3 评分卡意见：数据丢失不再被静默放行 + 自检编码崩溃，2026-11-27）

| 问题 | 修复 | 验证 |
| --- | --- | --- |
| 容器内 SQLite 在三条闸门上被制度化放行：部署自检判 warn、体检第二步 warn 不阻断、`/health/ready` 的 `blocking_reasons` 为空 —— 「披露」不等于「关闭」，任何监控都无法据此报警 | `scripts/check_deploy_environment.py::check_database` 在平台环境（`SPACE_ID`/`SPACE_HOST` 存在）缺 `DATABASE_URL` 时判 **fail**（退出码 1 → 体检阻断），只有显式 `ACCEPT_EPHEMERAL_STORAGE=1` 才降级为 warn 并点名「已显式接受数据丢失」；`backend/main.py::_readiness()` 新增 `degraded_reasons` 与 `persistent` 字段（状态码仍 200，避免 HF 判定不健康反复重启） | `backend/tests/test_deploy_env_check.py` 4 例（平台缺 URL→FAIL、显式接受→WARN、本机缺 URL 仍 WARN、CORS 不受影响）；`backend/tests/test_platform_readiness.py` 2 例（`degraded_reasons == warnings` 且 `persistent is False`；设了 URL 时 `persistent is True`） |
| README 里的 `python scripts/check_deploy_environment.py --env-file backend/.env` 在 Windows 默认 GBK 控制台直接崩：`UnicodeEncodeError: 'gbk' codec can't encode character '\u2713'`（只有 `--json` 通路正常） | 输出图标改 ASCII `[OK]`/`[!]`/`[X]`；新增 `_tolerate_console_encoding()` 对 stdout/stderr 设 `errors="replace"` 并在 `main()` 首行调用 | `backend/tests/test_deploy_env_check.py` 2 例（`PYTHONIOENCODING=gbk` 下文本通路不含 `UnicodeEncodeError` 且含 `[OK]`；JSON 通路最后一行可 `json.loads`） |
| 文档把后端用例数写成 874（真值 885），R3 评分卡据此扣 G 维 1 分 | 6 个文件 8 处 874→885，并在 `docs/v4-release-checklist.md` 第 9 行补第 44/45 批用例来源 | `scripts/verify_docs_refs.py --strict` 26 文件 0 断链；后端全量 `Ran 885 tests / OK`（第 46 批后为 910） |

## 第 44 批（可运维性：请求号贯通 + 部署变量自检，2026-11-27）

| 问题 | 修复 | 验证 |
| --- | --- | --- |
| 用户报「点了就失败」时，前端错误体里的 `request_id` 在服务端日志里查不到，只能靠时间戳猜是哪一条 | 新增 `backend/core/log_context.py`（`ContextVar` + `RequestIdFilter`）；中间件为每个请求生成或沿用 `X-Request-ID`/`Idempotency-Key`，写入 `request.state`、日志上下文并回显响应头；日志格式改为 `[rid=%(request_id)s]` | `backend/tests/test_log_context.py` 8 例（未绑定回落 `-`、绑定/重置往返、响应头回显、调用方传入不覆盖、连续请求 id 不同、401 错误体与日志同 id） |
| 「登录发不出去」「重启丢数据」这类问题在应用层表现成一串看似无关的报错，线上排查靠人肉逐个试环境变量 | 新增 `scripts/check_deploy_environment.py`：数据库 URL/端口、`AUTH_SECRET_KEY` 长度（兼容旧名 `JWT_SECRET`）、Cookie secure、CORS 通配符、邮件通道（代理或 SMTP 可达性）、LLM 模式、存储目录可写，逐项给 ok/warn/fail + 修法；`--json` 机器可读、`--skip-network` 离线 | `backend/tests/test_deploy_env_check.py` 25 例；实跑本机 `0 项必须修复，2 项建议处理`；发版体检新增第二步 `deploy-env`（warn 不阻断、fail 阻断） |
| 体检脚本「8 步」不含环境变量检查，配置类故障只能等线上炸了才发现 | `scripts/pre_release_check.py` 的 `ALL_STEPS` 插入 `deploy-env`（`docs → deploy-env → backend-import → …`），`backend/tests/test_pre_release_check.py` 增 4 例（warn 通过、fail 阻断、非 JSON 输出降级、顺序在 backend-tests 之前） | 体检 9 步；后端全量 `Ran 885 tests / OK`（第 46 批后为 910）；前端 334/334；`tsc --noEmit` 0 错 |
| 线上到底跑的是不是最新代码、请求号能不能真的串起来，只能靠猜 | 第 44 批上线后做强证据验证：GitHub CI run `45932843781` success（backend/frontend/browser-e2e 三 job 全绿）；`scripts/deploy_hf_space.py` 推送 Space `f4fbdcf` 且 `stage=RUNNING`；线上带 `X-Request-ID: probe_abc123456789` 请求 `GET /backend/api/v2/quota`，响应头 `X-Request-Id` 与错误体 `request_id` **双双沿用**该 id | 见 `docs/v4-release-checklist.md` 6.7 段的平台层限制说明（经 Vercel 反代时，服务端新建 id 不回显响应头，但错误体与日志始终可用） |

## 第 43 批（CI 从恒红到全绿：4 个根因 + 文档检查口径，2026-11-27）

| 项 | 内容 |
| --- | --- |
| 起点 | GitHub Actions 全历史 2 次运行均 failure；`ci` 只在 PR 分支触发、不覆盖 `main`，所以「本地全绿」与「CI 全红」长期并存 |
| 根因 1 | `backend/tests/__init__.py` 只在本机（`backend/.tmp_tests`、`backend/data` 恰好存在）能过：CI 空目录 → 17 处 `tempfile.mkdtemp(dir=...)` 抛 `FileNotFoundError`；默认 SQLite 无 DDL → `no such table: auth_codes` |
| 根因 2 | `backend/requirements.txt` 缺 `cryptography`：`core/crypto.py` 导入失败 → `services.llm_config_service` 不可导入 → 建会话 `503 FEATURE_NOT_READY` |
| 根因 3 | 没有 `backend/.env` 时 `LLM_KEY_MODE=system` 仍要求账户 Key 存在、`AUTH_SECRET_KEY` 为空：建会话 `428 LLM_CONFIG_REQUIRED`、造 Cookie 抛 `AuthConfigurationError` |
| 根因 4 | 沙箱 `_kill_tree` 在 POSIX 上 `os.killpg(os.getpgid(pid), 9)`，但子进程没 `start_new_session` → 一刀把 CI 的 shell 进程组杀掉，job 挂在 `test_timeout_is_recorded_not_raised` 永不结束 |
| 根因 5 | 前端 `node --test "tests/*.test.cjs"` 的引号把 glob 交给 Node，Node 20 不支持 glob → `Could not find`；`test:e2e` 用 `fs.existsSync("python")` 判断解释器，裸命令名永远 false |
| 根因 6 | 文档引用检查把 `backend/.env`、`frontend/.next`、`backend/data/…` 当断链，干净检出里必然不存在 → `--strict` 在 CI 恒红（本机这些目录一直在） |
| 修复 | `tests/__init__.py` 建 `.tmp_tests` + 默认测试库 + DDL + 假 `LLM_API_KEY`/`AUTH_SECRET_KEY`；`requirements.txt` 加 `cryptography`；`ci.yml` 加 `main` 触发、浏览器 job 注入 `AUTH_SECRET_KEY`/`LLM_KEY_MODE`；沙箱子进程 `start_new_session=True` + `os.killpg(pid, 9)`；`package.json` 去引号 glob；`api-smoke` 改 `spawnSync --version` 探测；`verify_docs_refs.py` 增加 `GENERATED_PREFIXES/GENERATED_FILES` 跳过生成物 |
| 回归 | **CI 全绿**：run `45865717810`（`backend (python 3.12)` / `frontend (node 20)` / `browser e2e (AC-01/19/31)` 三个 job 全 success）；本地后端 841 两跑 OK、前端 334 两跑一致 |
| 口径 | 临时诊断用的 `ci-logs` 工作流与分支已删除，仓库只保留 `ci.yml`（含 `main` 触发） |

## 第 42 批（线上不可用根因：跨域 API 基址 + 网关抖动韧性 + 平台加固，2026-11-26）

| 问题 | 证据 | 修复 | 回归 |
| --- | --- | --- | --- |
| 线上登录与所有写操作 100% 失败：生产 bundle 把 API 基址编译成 `https://…hf.space`，HF 平台代理的预检响应缺 `Access-Control-Allow-Credentials`，浏览器直接拒发 | 独立评测组 `eval-online`：真机 `/login` 发码 console 报 `net::ERR_FAILED`；`curl …/chunks/*.js` 命中 `hf.space`；OPTIONS 预检无 ACAC | `frontend/lib/api.ts` 新增 `resolveApiBase()`：生产页面一律走同源 `/backend`（跨域仅本地/显式 `NEXT_PUBLIC_ALLOW_CROSS_ORIGIN_API=1` 时保留） | `frontend/tests/api-base.test.cjs` 5 例；模拟 Vercel 环境变量构建 12 路由 exit 0 |
| 网关 21s 断连时用户只看到「已自动重试」却无从判断，写请求不重试也这样显示 | 采样本机 fake-IP 代理对照（cloudflare/huggingface.co 同比例），确认非服务端 | `apiV2` 4 次尝试 / 首跳 12s、抖动退避、`subscribeRetryNotices` + `ConnectionBanner` 顶栏进度；未重试写请求改用 `NETWORK_ERROR_UNRETRIED_MESSAGE` | `frontend/tests/api-retry.test.cjs` 11 例（含幂等键复用断言） |
| SSE 断线两跳后把用户丢在错误页，`isStreaming` 永真 | `frontend/store/sessionStore.ts` 原 `reconnectRun` | 重连耗尽改轮询 `GET /api/v2/runs/{id}`（2.5s、10 分钟上限），瞬时错误降级为 warning | `session-store.test.cjs` 轮询用例 |
| 三态缺失：`/ops` 无数据仍显示「Worker 正常」、权限页失败永远「正在读取」、配置页读失败渲染空表单并 bypass 静默 | 匿名访问线上 `/ops`、`/settings/llm`、`/settings/access` 实测 | `/ops` 未就绪显示「读取中/—」；权限页失败文案 + 刷新中态；配置页骨架 + 自动重试 + 手动重试；`AuthGate` 超时 5s 并 toast；`LlmConfigGate` bypass 显式提示 | 前端 330 用例 |
| 容器平台代理下登录限流退化成「全站 10 次/小时」 | `container_start.py` 起 uvicorn 无 `--proxy-headers` | 启动器加 `--proxy-headers` + `--forwarded-allow-ips`（可配置），并发码限流增加「邮箱」桶（5 次/小时） | `backend/tests/test_platform_readiness.py` 9 例 |
| 未登录时登录页顶部挂着「模型配置读取失败（网络不稳）」误导横幅 | 线上真机：匿名从 `/ops` 被重定向到 `/login` 后横幅仍在（401 的失败态跨路由残留） | `LlmConfigGate` 白名单路径直接渲染 children；新增 `api.isUnauthenticated()`，401 静默交给 `AuthGate` 跳登录 | `tests/llm-gate-render.test.cjs` 2 例 + `tests/api-auth-error.test.cjs` 2 例 |
| 线上数据无持久化（HF 免费实例无卷，重启/休眠回收即清零）却完全静默 | HF 元数据 `gcTimeout=172800`；`Dockerfile` `DATABASE_URL=""` 落容器内 SQLite | 代码无法直接修（需挂卷/外部库），改为**不静默**：`/health`、`/health/ready` 增 `warnings`（`SPACE_ID` 且无 `DATABASE_URL` 时明确写出「数据会丢失」），`/ops` 可直接看到 | `tests/test_platform_readiness.py` 持久化 3 例 |
| `/health` 恒 200，只看配置存在不看数据库，运维据此误判健康 | `/health` 实现与线上实测 | 新增 `/health/live`（纯存活）与 `/health/ready`（真探数据库，异常 503 + `blocking_reasons`）；`/health` 语义保持不变供 keep-warm 使用 | 同上（ready 注入探测失败断言 503） |

## 第 41 批（线上网关抖动热修：客户端重试与中文错误文案，2026-11-26）

| 项 | 结果 |
| --- | --- |
| 现象 | 线上点「配置模型 API Key」报「读取模型配置失败：网络连接失败」；页面偶发英文 `Failed to fetch`，用户完全进不去 |
| 定位 | `curl` 实测经 Vercel `/backend/*` 与直连 Space 均有约 5%~10% 请求 21s 后返回 `000`；抖动在 Space 前置代理，与本接口逻辑无关 |
| 修复 | `apiV2` 瞬时故障重试 3 次（退避 700/1800ms，重试单次超时 8s/6s）；GET 默认开、幂等写显式开（复用 `Idempotency-Key`）；SSE 首连重试 2 次；`errorMessage` 中文化；`isEndpointUnavailable` 不再把 TypeError 当端点缺失；模型配置页静默重试 + 「重新读取配置」按钮 |
| 回归 | 新增 `frontend/tests/api-retry.test.cjs` 7 例；前端 **321 / 321 pass**、`tsc` 0 错、停栈后 `npm run build` exit 0（12 条路由） |
| 发布验证 | Vercel 线上 chunk 已含新文案（`461-*.js` 有「服务端可能正在唤醒…」、`settings/llm/page-*.js` 有「重新读取配置」）；curl 12 次 `/backend/api/v2/llm-config` 全 401 且 0.74~1.08s（抖动为间歇性，约 5%~10%）；CDP 断网用例 6 项全过并留截图 `docs/browser-qa/e2e/llm-config-offline-retry.png` |
| 提交 | `676d763`（前端热修）、线上 Vercel 自动重建；Space 后端无需重部署 |

## 第 40 批（V4 容器部署链路 + 邮件代理 + 上线，2026-11-26）

| 问题 | 修复 | 证据 |
| --- | --- | --- |
| V4 只有本机 `start.bat` 三进程启动方式，没有任何线上部署文件；而线上站点是 Vercel 前端 + Hugging Face Space（docker SDK）后端，堆栈完全对不上 | 新增 `Dockerfile`（`python:3.11-slim` + 构建期 `rag.index_builder.build_index()` + 只 `COPY backend/` 与 `scripts/container_start.py`）、`.dockerignore`（保留 `backend/rag/knowledge/**/*.md` 语料、排除 `frontend/`、`data/`、`.env`）、`scripts/container_start.py`（标准库 `subprocess` 同时托管 `uvicorn main:app` 与 `workers.run_worker`，任一子进程退出即整体退出；启动前先 `initialize_database()`，否则 Worker 的 `recover_expired_leases` 会撞 `no such table: runs`） | `backend/tests/test_deployment_assets.py` 8 例（CMD/EXPOSE、dockerignore 不误伤知识库、rewrites、vercel.json、env 示例、HF front matter、只用标准库、缺库快速失败）；进程级模拟 `/health` 200、`/docs` 200、日志无 `no such table`；停栈后 `npm run build` exit 0（12 条路由，`routes-manifest` 的 `/backend/:path*` → HF Space 反代生效） |
| 前端 `API_BASE` 默认 `http://localhost:8000`，线上必须走同源 `/backend` 反代；根 `package.json` 是空对象，Vercel 构建行为不确定 | `frontend/next.config.js` 增加 `rewrites`（`/backend/:path*` → `BACKEND_ORIGIN`，默认本项目的 HF Space）、新增 `frontend/vercel.json`（`NEXT_PUBLIC_API_URL=/backend`）、`frontend/.env.example` 写明本地/线上两种取值、根 `package.json` 改为 workspaces、README 顶部加 HF Space front matter 与「线上部署」章节 | `npx tsc --noEmit` 0 错；`npm test` 314/314；`test_frontend_env_example_documents_both_modes` 锁定生效行 |
| HF Space 封 SMTP 出站端口，邮箱验证码登录必然超时；V4 的 `core/auth.py` 只会直连 `smtplib` | 新增 `EMAIL_PROXY_URL`/`EMAIL_PROXY_KEY`：`_send_via_proxy()` 用标准库 `urllib.request` POST 转发，成功即返回，失败/未配置回落直连 SMTP，不新增依赖 | `backend/tests/test_email_proxy.py` 5 例（代理成功不碰 SMTP、`success=false` 回落、`OSError` 回落、未配置不走 HTTP、`SMTP_USE_SSL=false` 走 starttls）；全量后端 `Ran 825 tests / OK` |
| 仓库根目录误留 12 个 0 字节中文垃圾文件（早前误写入被 commit），会让 Vercel/HF 构建上下文变脏 | `git rm` 掉 12 个 0 字节文件（`remaining_zero_byte=0`），保留被 PRD 引用的 `PM_Brainstorm_产品与UIUX重构_PRD_V3.0_源码实测版.md` | `git status` 12 条 `D`；PRD V4 第 45 行引用仍可解析 |
| 部署上线只做到「代码在本地能跑」，没有真正推到 GitHub / HF Space，线上仍是 V3 | GitHub `main` 强推到 `aeb03df`（先建 `backup/v3-production` 分支与 `v3-production-backup` tag 指向 V3 的 `16d3cc6`）；HF Space 拒收二进制（`Your push was rejected because it contains binary files.`），改为推「可部署子集」孤儿分支（`Dockerfile`/`.dockerignore`/`README.md`/`backend/`/`scripts/container_start.py`，159 文件 2.0MB） | Vercel 自动重建：首页标题变为「产品决策智能工作台」；`https://www.brainstorming.top/backend/health` → `200 {"status":"ok"}`；线上 OpenAPI 含 `/api/v2/access`、`/api/v2/groups`、`/api/v2/sessions/{session_id}/share`、`/api/v2/loops/{loop_id}/sandbox-runs`；Space `stage=RUNNING`（`sha=9eea1cb`，第一批部署为 `def63cd`）；`/ops`、`/settings/access`、`/settings/team`、`/login`、`/product` 全部 200 |

| Space 每次部署都要手搓 worktree/孤儿分支，容易漏文件，且本机桌面根与用户 TEMP 都可能不可写 | 新增 `scripts/deploy_hf_space.py`：用 git plumbing（`read-tree` → `update-index --force-remove` → `write-tree` → `commit-tree` → `update-ref`）从 HEAD 组装「只含可部署子集」的提交再 `push --force space space-main:main`，不建临时 worktree、不依赖临时目录写权限；`--dry-run` 只列文件 | `--dry-run` 输出「子集树 a8f500e…，160 个文件」；实推 `9eea1cb..f29ce35`；Space `stage=RUNNING`；`backend/tests/test_deployment_assets.py` 15 例（新增 4 例脚本契约）；后端 825 用例 OK |
## 第 43 批 发布验证（2026-11-27）

| 项 | 内容 |
| --- | --- |
| 前端 | Vercel 重建完成，`/_next/static/chunks/819-*.js` 内可见 `return "localhost"===e||"127.0.0.1"===e||"::1"===e||t?n:"/backend"`（生产强制同源）与「服务端可能正在唤醒或网络抖动」文案 |
| 后端 | Hugging Face Space `sha=9b19b45`、`stage=RUNNING`；`/backend/health`、`/health/live`、`/health/ready` 全 200，`ready` 带持久化 `warnings` |
| 真机验收 | CDP 真 Chrome 打开 `https://www.brainstorming.top/login` 发码：无跨域脚本、无英文报错、出现日志兜底提示、`page_errors` 为空 → `LOGIN43_LIVE_OK`（8 项）。证据见 `docs/browser-qa.md` 第 10 节与 `docs/browser-qa/e2e/login43-live-same-origin.png` |
| CI | run `45872510869`（head `99c0f7a`）三 job 全 success；此前 `45867837383`、`45871541688`、`45865717810` 亦全绿 |
| 独立评分 R1 | `loop/rounds/R1/scorecard.md`：**85.95 → FAIL**（A95/B70/C86/D85/E90/F82/G90/H92） |
| 独立评分 R2 | `loop/rounds/R2/scorecard.md`：**88.30 → FAIL**（A95/B70/C86/**D90**/E90/**F100**/**G86**/H92）。已关闭：CI 恒红、应用侧 CORS 通配；未关闭：P0-1 线上无持久卷（需挂卷或换外部库）、P1 平台代理层凭据反射、日志缺 `request_id` |
| 结论 | 线上「报错」类缺陷已清零（根因是构建期 API 基址为跨域 + 平台代理拦预检）；评分闸门仍未过，差在 **P0-1 基础设施决策**（用户侧）与 B 维 5 个 >1500 行文件的大重构（需单独排期，一次改动风险高于收益） |

当前基线：**后端 910 用例 OK / 前端 334 用例 + `tsc --noEmit` 0 错 / 真实浏览器 e2e AC-01+AC-19+AC-31+AC-33 PASS / 回滚演练 ROLLBACK_DRILL_OK / AC-20 真模型端到端 7 个未验证节点（人工核对） / 完整用户旅程真模型端到端 53,910 tokens / 结项报告真实模型路径 `generated_by=model` + 摘要落库（562 字） / 文档引用体检 601 条 0 断链（CI 已接入） / Worker 卡死诊断（`stalled`+`hint`）与前端排队提示 / 备份 `BACKUP_OK`+`VERIFY_OK`+`RESTORE_DRILL_OK` / 发版体检 `PRE_RELEASE_OK`（8 步，含 db-report） / 磁盘清理脚本（报告 + `--apply`） / 中间载荷 90 天保留策略 + `db_maintenance.py report|vacuum` / 账户级配额（429 `ACCOUNT_QUOTA_EXCEEDED` + `GET /api/v2/quota`） / 账户自带模型密钥（428 `LLM_CONFIG_REQUIRED` + 整屏配置引导 + 密钥加密只回末 4 位，真 Chrome 实测 `BYOK_GATE_CDP_OK`） / 团队工作区成员组（组=名单、共享行=授权，应用幂等） / Loop 执行沙箱（fail-closed + argv 直传 + 超时强杀 + 产物留档）与 `/ops` 运维健康页（storage/queues/sandbox 三块） / 共享可见性与权限总览（`my_role` 贯通 v2 与旧接口、`GET /api/v2/access`、成员退出共享、真 Chrome `ACCESS_CDP_OK` 13 项） / 生产构建 `npm run build` exit 0（12 条路由） / 生产 API 基址强制同源 `/backend`（跨域预检被平台代理拦截，登录与写操作曾 100% 失败） / 网络韧性（4 次尝试 + 首跳 12s + 抖动退避 + 顶栏重试进度 + SSE 断线转轮询） / 平台加固（`--proxy-headers`、邮箱维度限流、`/health/live`+`/health/ready`） / 请求号贯通（响应头 + 错误体 + 每条日志 `[rid=...]`，见 `core/log_context.py`） / 部署变量自检 `scripts/check_deploy_environment.py`（7 项 ok/warn/fail，体检第 2 步）**。

## 第 22 批（发版前一键体检，2026-11-24）

| 问题 | 修复 | 证据 |
| --- | --- | --- |
| 发版前要记住 7 条命令、逐个判断结果容易漏项：文档体检、后端全量、前端类型/用例、真实浏览器 e2e、备份演练各自有输出格式，人工汇总易出错 | 新增 `scripts/pre_release_check.py`：按 `docs → backend-import → backend-tests → frontend-types → frontend-tests → browser-e2e → backup-drill → db-report` 串行执行，逐步打印 `[OK]/[FAIL]` 与耗时，末尾给 `PRE_RELEASE_OK` / `PRE_RELEASE_FAILED`；支持 `--list`、`--only`、`--skip`、`--skip-browser`、`--json`、`--keep-going` | 实测 `PRE_RELEASE_OK  8 步`（docs 0.7s、backend-import 1.6s、backend-tests 69.4s、frontend-types 2.7s、frontend-tests 9.9s、browser-e2e 14.7s 3/3、backup-drill 2.3s、db-report 1.1s） |
| 服务没启动时浏览器 e2e 会白等到超时，且报错只剩「连接被拒绝」 | `step_browser_e2e` 先探 `API/health` 与 Web 首页，未就绪直接失败并提示「先运行 start.bat，或用 --skip-browser」 | `backend/tests/test_pre_release_check.py` 7 例（步骤/超时表一致、`--list`、未知步骤退出 2、`--only`/`--skip` 过滤、输出裁剪、未就绪提示） |

## 第 23 批（磁盘卫生：清理脚本 + 浏览器进程树泄漏修复，2026-11-24）

| 问题 | 修复 | 证据 |
| --- | --- | --- |
| 本机 D 盘只剩 1.2GB：`npm config get cache` 指向 `D:\trae\node_cache`，实测 **18.1GB / 22.8 万个文件**，且每次安装依赖只增不减 | 清理 npm 缓存（`npm cache clean --force`）释放约 24GB；新增 `scripts/disk_cleanup.py`：默认只报告本项目可重建产物（e2e 临时 user-data-dir、backend 下的 `.tmp_tests` 目录、`.tmp_*` 脚本、超期备份、可选 npm 缓存 / `.next`），`--apply` 才删除，`--keep-backups N` 控制备份轮转 | 实测 D 盘 1.2GB → 25.5GB 空闲；`--apply` 清空遗留 e2e 日志与临时目录；`backend/tests/test_disk_cleanup.py` 5 例（文件/字节统计、备份轮转、类别过滤、临时产物识别、`--json` 报告） |
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
