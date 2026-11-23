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

浏览器交互验收已从「人工/半自动协议」升级为 **CDP 自动化 e2e**（`scripts/browser_e2e/`，AC-01/19/31，见第 13 批）；仍不进 CI（需要本机 Chrome，CI 无图形栈）。SRV-02 已由 `backend/tests/test_crash_atomicity.py` 用真子进程崩溃注入补齐（不再是单进程模拟），TST-01 由 `tests/components-render.test.cjs` 补上真实渲染断言。

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

当前基线：**后端 657 用例 OK / 前端 249 用例 + `tsc --noEmit` 0 错 / 真实浏览器 e2e AC-01+AC-19+AC-31 PASS**。

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
