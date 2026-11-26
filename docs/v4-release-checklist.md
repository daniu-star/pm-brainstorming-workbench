# V4.0 发布就绪清单（WP-10）

- 分支：`feat/prd-v4-m1`；对应 PRD：`docs/superpowers/specs/2026-11-18-pm-brainstorm-agent-architecture-prd-v4.md`
- 验收记录：`docs/v4-acceptance-report.md`（764 后端用例 / 277 前端用例 / 契约 parity 13 / 跨进程 6 + 崩溃注入 3 + 预算崩溃 4 / 真 Worker E2E 30 项 + Loop 闭环 29 项 / 迁移演练 / 回滚演练（AC-34，`scripts/ac34_rollback_drill.py`）/ 真实浏览器 E2E AC-01/19/31/33（docs/browser-qa.md + docs/browser-qa/e2e/ 7 张截图）/ AC-20 数字可追溯性真模型端到端（`scripts/ac20_number_provenance.py` + `docs/ac20-number-provenance.md`） / 完整用户旅程真模型端到端（`scripts/full_journey_e2e.py` + `docs/full-journey-e2e.md`） / 结项报告真实模型路径（`scripts/audit_report_e2e.py` + `docs/audit-report-e2e.md`，含报告摘要落库）；文档索引见 docs/README.md；QA Loop 记录见 docs/qa-loop-runs/README.md，多角色 Loop 记录见 docs/loop-runs/README.md）

## 1. 发布前必须通过的命令

```bash
# 后端（755 用例，含 Loop Studio 65 项 + 数据生命周期/预算档位/审计并发 13 项；第 9 批删 v1 死代码 27 例，新增引用归属 7 + 崩溃注入 3；第 10 批新增旧 ID 别名 6；第 11 批新增 AC-14 多失败重试 1；第 12 批新增 AC-10 预算预留崩溃 4；第 16 批新增 AC-20 数字守卫 18 + 序号引用规范化 3 + json_schema 降级缓存 1；第 17 批新增结项报告形状容错 5；第 18 批新增报告摘要断言 + health/detail 模型层诊断 3；第 20 批新增 Worker 卡死诊断 3 + 前端排队提示 5；第 21 批新增备份/校验/恢复演练 4；第 22 批新增发版体检脚本自检 7；第 23 批新增磁盘清理脚本自检 5；第 24 批新增 Worker 维护清理 4；第 25 批新增 audit/loop 页面服务端渲染 6；第 26 批新增中间载荷保留 3 + 增长报告/VACUUM 3 + Worker 维护补 1；第 28 批新增报告侧数字标注 3（后端 2 + 前端页面 1）；第 29 批新增报告读取重算 3 + 证据表单渲染 3 + 页面入口 1；第 30 批新增报告质量统计 5；第 31 批新增报告质量窗口回落 4；第 32 批新增账户级配额 9；第 33 批新增账户自带模型密钥 11）
cd backend && ../.venv/Scripts/python.exe -m unittest discover -s tests

# 前端类型检查 + 逻辑用例 + 生产构建 + 冒烟 e2e
cd frontend && npx tsc --noEmit
cd frontend && npm test
cd frontend && npm run build
cd frontend && npm run test:e2e

# 真实浏览器 E2E（CDP + 本机 Chrome；前置：API 8001 / Web 3001 已启动）
cd backend && ../.venv/Scripts/python.exe ../scripts/browser_e2e/run_browser_e2e.py
# AC-33（语音/摄像头降级）会真实调用模型生成审计开场问题，约 6–8K tokens
cd backend && ../.venv/Scripts/python.exe ../scripts/browser_e2e/run_browser_e2e.py AC-33

# 迁移演练（只读复制源文件，临时库；不得对生产库执行 --apply 前跳过此步）
../.venv/Scripts/python.exe scripts/v4_migration_drill.py

# 迁移回滚演练（AC-34；生产库一致性快照到临时副本，模拟坏构建→回滚，不碰生产库）
cd backend && ../.venv/Scripts/python.exe ../scripts/ac34_rollback_drill.py

# 文档引用体检（校验文档里写的仓库路径是否仍存在；--strict 有断链即失败）
../.venv/Scripts/python.exe scripts/verify_docs_refs.py --strict

# 备份 / 校验 / 恢复演练（SQLite online backup，不阻塞 Worker；演练在临时目录）
../.venv/Scripts/python.exe scripts/backup_restore.py backup --keep 10
../.venv/Scripts/python.exe scripts/backup_restore.py verify backend/data/backups/<时间戳>
../.venv/Scripts/python.exe scripts/backup_restore.py drill  backend/data/backups/<时间戳>

# 数据库增长报告（每表字节/行数/文本列）与空闲页回收（发版前/大清理后执行一次）
../.venv/Scripts/python.exe scripts/db_maintenance.py report
../.venv/Scripts/python.exe scripts/db_maintenance.py vacuum
```

CI 配置见 `.github/workflows/ci.yml`（backend：安装依赖 → 全量测试 → `import main` 冒烟 → 文档引用体检；frontend：`npm ci` → tsc → test → build → test:e2e；browser-e2e：起 API + Next 后用真 Chrome 跑 AC-01/19/31，截图作为 artifact 上传）。

## 2. 部署顺序

1. **备份**：用 `python scripts/backup_restore.py backup` 生成一致性备份（SQLite online backup API + `backend/data/sessions/` + `manifest.json` 的 sha256/计数），输出到 `backend/data/backups/<时间戳>/`（已在 `.gitignore`）；恢复演练见第 1 节命令。
2. **建库/升级 schema**：应用启动时 `repositories.database.initialize_database()` 幂等建表；若已有库，先跑 `alembic upgrade head`（`backend/migrations/` 中的 alembic 环境）。
3. **迁移旧数据**（可选、一次性）：
   ```bash
   cd backend
   python -m migrations.import_json_sessions --source <旧 sessions 绝对目录> --dry-run
   python -m migrations.import_json_sessions --source <绝对目录> --apply
   ```
   dry-run 不写库；apply 幂等（重复执行 `skipped`）；源文件被改动时报 `conflicts` 并拒绝覆盖迁移记录；空 owner 迁到隔离账号 `legacy-unknown@local`。
4. **启动 API**：`uvicorn main:app --host 0.0.0.0 --port 8000`（`backend/` 为工作目录，`backend/.env` 提供配置）。
5. **启动 Worker**（独立进程，至少 1 个；推荐 2 个验证租约）：`python -m workers.run_worker`；调试用 `--once`。
6. **前端**：`cd frontend && npm ci && npm run build && npm start`（或静态导出部署）；`NEXT_PUBLIC_API_URL` 指向 API。
   > **账户自带模型密钥（BYOK）**：`LLM_KEY_MODE` 默认 `byok`（首次登录必被整屏拦截，填完 Key 保存即放行，已用真 Chrome CDP 实测）——平台不代付模型费，用户首次登录后必须先到「模型 API 设置」（`/settings/llm`）填写自己的 `base_url` / `model` / `api_key`，否则建会话与模型命令一律 428 `LLM_CONFIG_REQUIRED`（密钥 Fernet 加密落库，只回显末 4 位）；本地联调/自动化测试可设 `LLM_KEY_MODE=system` 复用 `.env` 里的共享 Key。
7. **健康检查**：`GET /health`、`GET /health/detail`（`worker.running_runs/queued_runs/last_heartbeat_at`；DB 不可用、或「有排队任务但 Worker 无心跳」时 `status=degraded` 且 `worker.stalled=true` + `worker.hint` 给出启动命令）。

## 3. 发布后验证（手工 5 分钟）

1. 登录 → 新建会话 → 澄清 2 轮 → 确认产品定义（`phase` 变 `brainstorming`）。
2. 发起共创 → Worker 日志出现 `run.started` → 画布出现 ≥4 条分支；`GET /api/v2/sessions/{sid}/canvas` 的 `product_version` 递增。
3. 发起审计 → 出现第 1 问 → 回答 → 出现第 2 问；结束审计 → 报告页有结论与局限。
4. 刷新审计页 → 仍停在同一 `audit_id` 与当前问题（不重新开场）。
5. 打开浏览器开发者工具 → SSE 连接断开后重连，`after_seq` 从上次位置继续，不重复消息。

## 4. 回滚

- 代码回滚：`git checkout <上一个发布 tag>`，重新构建前端、重启 API 与 Worker。
- 数据兼容：v2 表结构为新增，旧 JSON 写路径已退役；回滚到 v1 代码需同时恢复 `backend/data/sessions/`（迁移器未修改源文件，仍可直接使用）。
- 迁移记录：`migration_records` 保留每个源文件的 `source_hash`；回滚后重新迁移同一源会 `skipped`，不会重复导入。

- 浏览器交互验收（`docs/browser-qa.md`）需要本机铸造登录 Cookie（验证码只存哈希、不可反推），且当前 `LLM_API_KEY` 为 401 失效状态，因此该流程验证的是「失败可见 + 状态一致 + 恢复入口」。

## 5. 已知行为（非缺陷，交接给运维/客服）

- 未登录访问未知 `/api/v2/*` 返回 **401**（鉴权中间件在路由匹配前短路）。
- `PUT /api/canvas` 返回 **409 CANVAS_IS_DERIVED**（画布由 `product_state` 投影生成，不接受手工写入）。
- M3 之后仍未实现的动作（如 `rebuild_legacy` 缺工作流）会以 `failed(FEATURE_NOT_READY)` 明确失败，并保留 step 记录，不产生假成功。
- 删除会话为软删 + **默认清正文**（`session_repo.purge_session_content`）：消息正文/证据摘要/审计问答/审计报告/闭环产物清空，保留会话/消息/版本链等骨架行作为 tombstone（`test_data_lifecycle.py` 覆盖）。调试可用 `purge_content=False`。
- 同步命令（确认定义/跳过/修订/加证据/确认决策/更正评估）各产生一条 `succeeded` Run，用于幂等回执，属预期行为。
- **`next build` 与 `next dev` 共用 `frontend/.next`**：开发服务器运行时执行生产构建会破坏其路由缓存（动态路由全 500）。验证构建前先 `stop.bat`，构建后 `start.bat` 重新拉起。
- Loop Studio（v5）的「测试执行」是模拟执行：产物 `simulated=true` 且必须注明「未实际执行」，否则确定性校验记 P0 阻塞并把总分压到 70；真实沙箱执行尚未实现。
- Loop Studio 原型产物是文件内容 + 启动命令，不在服务端构建；`requires_human_build=true` 时由用户自行落地。
- **模型鉴权失败会以 `LLM_AUTH_ERROR` 明确失败**（401/403，不可重试），错误信息直接提示更新 `backend/.env` 的 `LLM_API_KEY` 并重启服务；排查用 `.venv/Scripts/python.exe scripts/check_llm.py`。
- **控制意图由 Worker 每轮扫描**：`end_audit`/`cancel_audit` 在活跃 Run 上只登记意图（`control_intents`，状态 `accepted`），由 `RunWorker.tick()` 每轮调用 `command_service.reconcile_control_intents` 在旧 Run 终结后建唯一 closing Run（或完成取消）。停掉 Worker 时结束/取消审计会停在 `scheduled`，属预期。
- **闭环兜底终结会收口**：`workflows.runner._force_terminal` 与排队超时路径会调 `command_service.settle_loop_for_run_failure`，把 `loop/loop_round` 置 `failed` 并写 `loop.updated`；只收口 `loop.latest_run_id` 指向的 Run，迟到兜底不会误关已推进的闭环。
- **前端 `retryFailedRun` / `cancelRun` 返回 `boolean`**：失败时返回 false 并写 `error`/toast，页面据此展示失败而不是「已重新排队」。
- **低额度模式**：`backend/.env` 设 `LLM_BUDGET_PROFILE=lean|frugal` 后，`services.command_service.default_budget()` 用 `min()` 只收紧不下调契约常量，生效值写入每条 Run 的 `budget_json`（重放/迁移口径不变）；`/health/detail` 暴露 `budget_profile` 与 `budget_limits`。
- **事件压缩由 Worker 每小时驱动**：只删已终结 Run 的旧事件（每个 Run 保留最后一个），压缩后旧游标返回 410 `EVENT_CURSOR_EXPIRED`（`resolve_start_cursor` 的 watermark = 第一个仍可回放的 seq）。
- 旧 SQLite 库缺新列时由 `repositories/database.py::_additive_schema_upgrade()` 在启动时自动补列（当前覆盖 `runs.failed_steps_json`、`runs.loop_id`、`steps.created_at`）。缺列曾导致「发消息一律失败（INTERNAL / no such column: steps.created_at）」，已在 `backend/tests/test_schema_upgrade.py` 固化回归。

## 6. 本机验证的临时产物约定

本机 `TEMP` 不可用，`tempfile.gettempdir()` 会回退到当前工作目录。测试/脚本已统一使用 `backend/.tmp/` 或显式临时目录；`.gitignore` 覆盖 `.tmp/`、`backend/.tmp_tests/`、`backend/pm_api_v2_*/`、`backend/pm_legacy_*/` 等模式，避免污染仓库。
