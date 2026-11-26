# PM Brainstorm Workbench — V4.0 发版说明

- 版本：**V4.0**（分支 `feat/prd-v4-m1`；PRD V4.0 见 `docs/superpowers/specs/2026-11-18-pm-brainstorm-agent-architecture-prd-v4.md`）
- 验收结论：**42 / 42 通过**，部分通过 0 条、未实现 0 条（逐条证据见 `docs/v4-acceptance-report.md` §3）
- 一句话定位：给产品经理的多角色 AI 脑暴工作台——四角色并行共创、确定性归并、可追溯画布、反向审计闭环，所有写操作走「命令落库 → Worker 领取 → 结果与事件同事务提交」的持久执行模型。

## 1. 当前基线

| 检查项 | 结果 |
| --- | --- |
| 后端全量 | `Ran 825 tests` / OK（含 M2/M3、闭环、数据生命周期、崩溃注入、旧 ID 别名、AC-20 数字守卫、结项报告容错、Worker 卡死诊断、维护性清理、账户配额、账户自带模型密钥） |
| 前端类型 + 用例 | `npx tsc --noEmit` 0 错；`npm test` **314** 用例 pass（含页面级渲染 8 例 + 证据表单渲染 3 例 + 额度视图 6 例） |
| 前端构建 + 冒烟 | `npm run build` exit 0（7 条路由，含 `/session/[id]/audit` 8.11 kB）；`npm run test:e2e` 1 pass |
| 契约 parity | `tests.test_contract_parity` **13** 例，前后端枚举/常量/Literal 按序比对 |
| 跨进程并发 | `tests.test_cross_process` **6** 例（同 request_id 单 Run、SESSION_BUSY、双 Worker 单领、审计并发、end/cancel 竞争、验证码限流共享） |
| 崩溃注入 | `tests.test_control_crash` **3** 例（真 kill/restart）+ `tests.test_crash_atomicity`（真 `os._exit(9)`） |
| 预算崩溃 | `tests.test_budget_crash` **4** 例（崩溃一次 + 重跑一次 `upstream_calls == 2`） |
| 真 Worker 端到端 | `.tmp/e2e_audit.py` **30 项 ALL PASS**；Loop 闭环 **29 项** |
| 真实浏览器 E2E | AC-01 / AC-19 / AC-31 / AC-33 **PASS**（CDP + 本机 Chrome，零新依赖，**7 张**截图） |
| 迁移演练 | `scripts/v4_migration_drill.py` → **DRILL_OK**（scanned=19、messages=65、apply 幂等、删库重迁 identical、源 sha256 不变） |
| 回滚演练 | `scripts/ac34_rollback_drill.py` → **ROLLBACK_DRILL_OK**（注入的排队 Run 仍可被领取、计数不变、`integrity_check=ok`、JSON 指纹不变） |
| AC-20 真模型端到端 | 19 条论断、**7** 个画布节点标注「未验证数字」；有来源的 12/6/4 未被误标；calls=8，prompt 19460 + completion 5786 = **25246 tokens** |

## 2. 本次发版包含的能力

- **M1 状态机与命令链路**：`backend/domain/{contracts,state_machine,errors}.py` 定义实体、枚举与限额；`/api/v2/*` 共 **21** 条路由，旧 4 条路由保留为 adapter 并调用同一套应用服务（不存在第二条不受状态机约束的写路径）；幂等键保留 30 天，单会话单活动 Run，同步命令各留一条 `succeeded` Run 作回执。
- **M2 四角色脑暴与归并**：CTO / 设计 / 运营 / 目标用户四角色各按 `product_version` 取上下文并首轮互不可见，`services/reducer.py` 确定性归并（与完成顺序无关），`workflows/synthesis.py` 最多 1 轮修复 + 至多 2 位专家复核、冲突上限 2、预算不足时 degraded 回退；部分成功以 `partial_succeeded` 收口，重试只重调失败角色。
- **M3 审计闭环与报告**：`/api/v2/audits` 支持开场、逐问回答、结束与重开；18 问硬上限，第 18 问后直接由 closing Run 生成报告，模型可用走 `generated_by="model"`，不可用走确定性 `fallback` 并写 limitations；人工更正后从结构化记录重建报告并标 `human_revised=True`。
- **Loop Studio 闭环**：v5 闭环覆盖「设想 → 检验 → 开发 → 测试 → 评测」，`scripts/loop_e2e/run_loop_e2e.py` 29 项；决策条吸底、跨轮产物面板、`ship` 后决策置 `confirmed` 并 bump 版本。测试执行仍为模拟执行，见 §6。
- **数据生命周期与低额度模式**：软删默认清正文（消息、证据、产品状态、审计问答与报告、闭环产物），只留 tombstone 与版本链；事件压缩只动终态 Run 并保留每 Run 最后一条，旧游标按水位返回 410；`BUDGET_PROFILES` 提供 `standard` / `lean` / `frugal`，用 `min()` 只收紧不下调契约常量，生效值写入每条 Run 的 `budget_json`。
- **可靠性加固**：条件 UPDATE 原子领取 + epoch 递增、步骤成功与结果同事务、取消优先于提交、审计上下文守卫（版本或当前实例变化即 409）、证据归属校验、数字可追溯（`services/number_guard.py` 只标注不否决）、旧库缺列启动时自动补列、旧 12 hex 会话 ID 经 `session_id_aliases` 解析。

## 3. 42 条验收结论（按能力域分组）

| 能力域 | 覆盖的 AC | 条数 | 状态 |
| --- | --- | --- | --- |
| 会话、澄清与上下文装配 | AC-01 / AC-02 / AC-03 / AC-04 / AC-06 / AC-17 | 6 | 通过 |
| 并发控制与持久执行 | AC-07 / AC-08 / AC-09 / AC-10 / AC-29 / AC-30 | 6 | 通过 |
| SSE 与事件回放 | AC-11 / AC-12 / AC-13 / AC-35 | 4 | 通过 |
| 四角色脑暴、证据与归并（M2） | AC-05 / AC-14 / AC-15 / AC-16 / AC-37 / AC-38 | 6 | 通过 |
| 画布投影与数字可追溯 | AC-18 / AC-19 / AC-20 | 3 | 通过 |
| 审计闭环（M3） | AC-21 / AC-22 / AC-23 / AC-24 / AC-25 / AC-26 / AC-36 / AC-40 / AC-41 / AC-42 | 10 | 通过 |
| 鉴权、归属与账号隔离 | AC-27 / AC-28 / AC-31 | 3 | 通过 |
| 数据生命周期、迁移与回滚 | AC-32 / AC-34 / AC-39 | 3 | 通过 |
| 语音与降级交互 | AC-33 | 1 | 通过 |
| **合计** | **AC-01 … AC-42** | **42** | **42 通过 / 0 部分 / 0 未实现** |

其中 4 条以真实浏览器为最终证据（AC-01 刷新三次不重复、AC-19 八分支不折叠、AC-31 换账号零残留、AC-33 摄像头/STT/TTS 三条降级路径），2 条以真实演练为最终证据（AC-32 迁移演练、AC-34 回滚演练），1 条以真模型端到端加人工标注为最终证据（AC-20），其余为可执行单测、跨进程或崩溃注入。

## 4. 值得单独说明的修复

以下 15 项是 36 批热修与 M1 验收期里影响面最大的缺陷，按「问题 → 修复 → 证据用例」列出；完整清单见 `docs/loop-runs/README.md`。

| 问题（来源） | 修复 | 证据用例 |
| --- | --- | --- |
| 假成功：步骤已标 `succeeded` 但结果与消息未提交，崩溃后留下空成功步骤（第 6 批） | 步骤成功与结果同事务（`_attach_step_success_hook`），恢复时 `requeue_uncommitted_step` 重新排队 | `test_runner.py::ReplayTests` 2 例 + `test_crash_atomicity.py`（真子进程 `os._exit(9)`） |
| 双领：两个 Worker 同时领到同一 Run，重复执行（M1 跨进程验收） | 条件 UPDATE 原子领取 + epoch 递增，旧 worker 的提交被拒 | `test_cross_process.py::test_two_worker_processes_claim_the_same_run_once`、`test_run_repo.py::test_old_worker_submit_after_takeover_is_rejected` |
| 幂等双 Run：同一 `request_id` 跨进程各建一条 Run（M1 跨进程验收） | 幂等键查库后复用结果，同键异体返回 409 | `test_cross_process.py::test_same_request_id_in_two_processes_creates_one_run`（messages seq `[1,2]`、runs=1） |
| 取消被复活：接管租约过期的 `cancel_requested` Run 时被重置成 `running`（第 9 批） | 接管保持取消意图，执行器直接提交 `cancelled`、零步骤 | `test_cancel_requested_survives_lease_takeover` |
| 崩溃丢预算：用量只在调用结束后落盘，进程一崩这次调用的次数与 token 全丢（第 12 批） | 发请求前写 `reserved_prompt_tokens` / `reserved_completion_tokens`，接管方把上一 epoch 预留按已消耗计入 | `test_budget_crash.py` 4 例：崩溃一次 + 重跑一次 `upstream_calls == 2` |
| 推理模型 content 空：`deepseek-flash` 把 1500 tokens 先花在 `reasoning_content`，四角色全部 `MODEL_OUTPUT_INVALID`（第 16 批） | `LLM_DISABLE_REASONING` 默认开，对 `deepseek*` 发送 `extra_body={"thinking": {"type": "disabled"}}`，并在对话末尾复述 `SCHEMA_REMINDER` | 实测 completion 1500→380、content 恢复；`test_llm_client.py` / `test_brainstorm*` 48 例 |
| `json_schema` 400 白付：供应商不支持时每次调用先吃一次 400，四角色即触发 `BUDGET_EXCEEDED`（第 16 批） | `_json_schema_supported` 缓存降级结论，同进程后续直接走 `json_object` | `test_json_schema_downgrade_is_cached_per_client`（3 次调用仅 1 次 400，上游调用 8→4–5） |
| 报告摘要被丢弃：模型给了 `summary`，但 `AuditReport` 契约里没有该字段，报告页只能看到空块（第 18 批） | 契约新增 `summary`（≤4000 字），模型漏写时用结构化兜底摘要（含问题数/维度计数），前端新增「报告摘要」区 | `test_audit_workflow.py` 2 处断言 + 真模型 `scripts/audit_report_e2e.py`（562 字摘要、8,054 tokens） |
| 报告数字无来源：审计报告正文（支持论据/缺口/建议/摘要）里的数字没人核对，读起来像事实（第 28 批） | 报告落库前用同一套 `number_guard` 标注：来源消息/证据里找不到的数字写进 `unverified_numbers` 并生成补充建议；前端在报告顶部给「数字未验证」徽标 | `test_number_guard.py` 报告侧 2 例 + `test_audit_workflow.py::test_report_marks_numbers_absent_from_sources`（12/6 可追溯不误标、2/66/5 标注）+ `pages-render.test.cjs` 徽标 1 例 |
| 平台替所有人付模型费：任何登录用户都能烧平台额度，而线上收款又需要营业执照（第 33 批） | 改为 BYOK：`llm_configs` 每账户一条（Fernet 加密、只回显末 4 位）；未配置时建会话 / 模型命令 / Worker 执行三处统一 428 `LLM_CONFIG_REQUIRED`，前端整屏「先配置你自己的模型 API」并带保存+连通性测试表单 | `test_llm_config.py` 11 例 + `llm-config.test.cjs` 5 例 |
| 额度只存在于接口里：部署方设了上限，用户只能等被 429 拒绝才知道超了（第 34 批） | `GET /api/v2/quota` 的数据画成两处：`/settings/llm` 的「模型额度与用量」卡片（token / 调用次数进度条、预占与恢复倒计时），以及顶栏徽标（仅在设了上限时出现，≥80% 琥珀色、超额红色）；恢复时间按分钟归一，不会出现「23 小时 60 分钟」 |
| 会话是「一个人的」：想给同事看一眼方案，只能把账号给对方登（第 35 批） | 会话共享：所有者把会话授予 viewer（只读）或 editor（可提交命令），`GET/POST/DELETE /api/v2/sessions/{id}/shares` 管理成员；共享只授权不复制数据，撤销即失效；被共享方在 `GET /sessions` 里能看到该会话，`/messages`、`/product-state` 等读接口全部放开 |
| 越权引用：模型写他会话的 `evidence_id` / `message_id` 也能通过校验（第 9 批） | `evidence_guard` 逐条回查会话归属，先回喂修复一次，仍失败 → `MODEL_OUTPUT_INVALID` | `test_evidence_guard.py` 7 例 + `test_brainstorm_runner.py::test_foreign_evidence_ref_fails_after_one_repair` |

## 5. 运行方式

- 一键启动：双击 `start.bat`，自动建库后拉起 `PM-API :8001`、`PM-Worker`、`PM-Web :3001` 三个控制台窗口；端口被占用时自动顺延并同步 `CORS_ORIGINS`，重复双击只打开浏览器。`stop.bat` 按 PID 收掉三个窗口及子进程。
- `.run/` 存放 PID 与端口（已 gitignore）；可用参数 `-NoBrowser` / `-ApiPort` / `-WebPort` / `-SkipWorker` / `-Force`。
- **前端 3001 必须与后端 8001 使用同一主机名**（脚本统一 `127.0.0.1`），否则 `SameSite=lax` 的登录 Cookie 不会随请求发送，表现为「登录后又退回未登录」。
- `LLM_API_KEY`：必填；鉴权失败以 `LLM_AUTH_ERROR` 明确失败并提示改 `backend/.env`，排查用 `.venv/Scripts/python.exe scripts/check_llm.py`。
- `LLM_MODEL`：供应商模型名（默认 `gpt-4o`），必须与 `LLM_BASE_URL` 指向的供应商一致；本项目联调与 AC-20 使用 `deepseek-flash`。
- `LLM_BUDGET_PROFILE=standard|lean|frugal`：只收紧预算，生效值写入每条 Run 的 `budget_json`；`/health/detail` 暴露 `budget_profile` 与 `budget_limits`。
- `LLM_DISABLE_REASONING=true`：默认开启，对含 `deepseek` 的模型关闭思考；设 `false` 恢复供应商默认行为。
- 其它：`DATABASE_URL` 覆盖 SQLite 位置（默认 `backend/data/app.db`），`NEXT_PUBLIC_API_URL` 指向 API。
- 手工部署顺序：`python scripts/backup_restore.py backup`（一致性备份 + manifest） → `initialize_database()` 或 `alembic upgrade head` → 迁移 `--dry-run` 后再 `--apply` → API → Worker（至少 1 个，推荐 2 个验租约）→ 前端 `build` / `start` → `GET /health`、`GET /health/detail`。

## 6. 已知限制与后续

以下均为当前现状，不是已修复项，按此交接。

- 重试粒度：AC-14 已从整 Run 重放收窄为「只重调失败角色、复用已成功角色」，但仍以角色为单位，没有 step 级断点续跑。
- 事件保留：未压缩事件默认保留 7 天，压缩由 Worker 每小时驱动；Worker 不常驻时压缩与 410 水位都不推进，`end_audit` / `cancel_audit` 也会停在 `scheduled`。
- 未登录访问未知 `/api/v2/*` 返回 **401** 而非 404：鉴权是中间件，在路由匹配前短路；PRD 未要求，前端已按 401/404 双兼容。
- 前端测试栈仍是 `node --test`（另加 `react-dom/server` 真渲染），不是 vitest/RTL；本机 `registry.npmjs.org` 不可达，暂时没有组件级快照测试，浏览器级证据由 CDP 脚本补足。
- 真模型调用、迁移与回滚演练、AC-33/34 不进 CI：需要模型额度、本机权限演练与独占的 8001/3001，只在发版前手工执行并留痕。
- 浏览器 e2e 的 **AC-01/19/31** 已进 CI（`browser-e2e` job：ubuntu 自带 Chrome，起 API + Next 后跑三场景，截图上传为 artifact）；这些场景只播种数据、不花模型额度。
- 成本：真模型四角色一轮约 **25K tokens**（calls=8，prompt 19460 + completion 5786 = 25246）；AC-33 语音降级场景另耗 6–8K tokens。
- Loop 技能包 `skills/loop-engineering-product/` 只在本仓库，尚未导出为独立仓库或推 GitHub（`scripts/export_loop_skill.py` 可导出）。
- Loop Studio 的「测试执行」是模拟执行：产物带 `simulated=true` 且必须注明「未实际执行」，真实沙箱尚未实现。
- 观测粒度：brainstorm 的 role 步骤不发 `step.started`（角色完成以 `role.completed` 表达），预算中止时个别 role step 可能停留 `running`，但 Run 终态为 `failed` 且恰好一个终结事件。
- 本机注意：`next build` 与 `next dev` 共用 `frontend/.next`，开发服务器运行时做生产构建会破坏路由缓存；构建前先 `stop.bat`，构建后 `start.bat`。

## 7. 如何复跑验收

```bash
# 后端全量（825）
cd backend && ../.venv/Scripts/python.exe -m unittest discover -s tests

# 前端类型、用例、构建、冒烟
cd frontend && npx tsc --noEmit
cd frontend && npm test
cd frontend && npm run build
cd frontend && npm run test:e2e

# 契约 parity（13）
cd backend && ../.venv/Scripts/python.exe -m unittest tests.test_contract_parity

# 跨进程（6）
cd backend && ../.venv/Scripts/python.exe -m unittest tests.test_cross_process

# 崩溃注入 / 预算崩溃（4）
cd backend && ../.venv/Scripts/python.exe -m unittest tests.test_control_crash tests.test_crash_atomicity
cd backend && ../.venv/Scripts/python.exe -m unittest tests.test_budget_crash

# 真 Worker 审计端到端（30 项）
.venv/Scripts/python.exe backend/.tmp/e2e_audit.py

# 真实浏览器 E2E（前置：start.bat 已拉起 8001 / 3001）
cd backend && ../.venv/Scripts/python.exe ../scripts/browser_e2e/run_browser_e2e.py
cd backend && ../.venv/Scripts/python.exe ../scripts/browser_e2e/run_browser_e2e.py AC-33

# 迁移演练
cd backend && ../.venv/Scripts/python.exe ../scripts/v4_migration_drill.py

# 回滚演练（AC-34，只做只读快照，不碰生产库）
cd backend && ../.venv/Scripts/python.exe ../scripts/ac34_rollback_drill.py

# AC-20 真模型端到端（约 25K tokens）
cd backend && ../.venv/Scripts/python.exe ../scripts/ac20_number_provenance.py
```

以上命令与 `docs/v4-acceptance-report.md` §2、`docs/v4-release-checklist.md` §1 一致；本文所有数字均可在上述两份文档与 `docs/loop-runs/README.md` 中复核。
