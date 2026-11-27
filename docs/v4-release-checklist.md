# V4.0 发布就绪清单（WP-10）

- 分支：`feat/prd-v4-m1`；对应 PRD：`docs/superpowers/specs/2026-11-18-pm-brainstorm-agent-architecture-prd-v4.md`
- 验收记录：`docs/v4-acceptance-report.md`（874 后端用例 / 334 前端用例 / 契约 parity 13 / 跨进程 6 + 崩溃注入 3 + 预算崩溃 4 / 真 Worker E2E 30 项 + Loop 闭环 29 项 / 迁移演练 / 回滚演练（AC-34，`scripts/ac34_rollback_drill.py`）/ 真实浏览器 E2E AC-01/19/31/33（docs/browser-qa.md + docs/browser-qa/e2e/ 7 张截图）/ AC-20 数字可追溯性真模型端到端（`scripts/ac20_number_provenance.py` + `docs/ac20-number-provenance.md`） / 完整用户旅程真模型端到端（`scripts/full_journey_e2e.py` + `docs/full-journey-e2e.md`） / 结项报告真实模型路径（`scripts/audit_report_e2e.py` + `docs/audit-report-e2e.md`，含报告摘要落库）；文档索引见 docs/README.md；QA Loop 记录见 docs/qa-loop-runs/README.md，多角色 Loop 记录见 docs/loop-runs/README.md）

## 1. 发布前必须通过的命令

```bash
# 后端（874 用例，含 Loop Studio 65 项 + 数据生命周期/预算档位/审计并发 13 项；第 9 批删 v1 死代码 27 例，新增引用归属 7 + 崩溃注入 3；第 10 批新增旧 ID 别名 6；第 11 批新增 AC-14 多失败重试 1；第 12 批新增 AC-10 预算预留崩溃 4；第 16 批新增 AC-20 数字守卫 18 + 序号引用规范化 3 + json_schema 降级缓存 1；第 17 批新增结项报告形状容错 5；第 18 批新增报告摘要断言 + health/detail 模型层诊断 3；第 20 批新增 Worker 卡死诊断 3 + 前端排队提示 5；第 21 批新增备份/校验/恢复演练 4；第 22 批新增发版体检脚本自检 7；第 23 批新增磁盘清理脚本自检 5；第 24 批新增 Worker 维护清理 4；第 25 批新增 audit/loop 页面服务端渲染 6；第 26 批新增中间载荷保留 3 + 增长报告/VACUUM 3 + Worker 维护补 1；第 28 批新增报告侧数字标注 3（后端 2 + 前端页面 1）；第 29 批新增报告读取重算 3 + 证据表单渲染 3 + 页面入口 1；第 30 批新增报告质量统计 5；第 31 批新增报告质量窗口回落 4；第 32 批新增账户级配额 9；第 33 批新增账户自带模型密钥 11；第 37 批新增团队工作区成员组 8；第 38 批新增执行沙箱 16 + 运维健康区块 4；第 39 批新增共享可见性与权限页 8；第 42 批新增平台加固 12，含 CORS 通配符拒绝 4）
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

**2026-11-27 起 CI 全绿**（run `45865717810`，三个 job 全 success）。此前长期「本地绿、CI 红」的 6 个根因与修法见 `docs/loop-runs/README.md` 第 43 批：测试引导补目录/库/env、补 `cryptography`、沙箱子进程独立进程组、Node 20 的 glob 引号、`test:e2e` 的 PATH 解释器探测、文档体检跳过生成物。

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
- Loop Studio（v5）的「测试执行」默认是模拟执行：产物 `simulated=true` 且必须注明「未实际执行」，否则确定性校验记 P0 阻塞并把总分压到 70。真实的执行沙箱在**默认关闭**状态下提供（`LOOP_SANDBOX_ENABLED=false`，白名单为空时一律 503）：开启后 argv 直传 `shell=False`、独立临时目录、剥离敏感环境变量、超时强杀、输出截断，每次运行都落 `sandbox_run` 产物；这是**进程级隔离，不是安全边界**，只在本地/受信环境开启。
- Loop Studio 原型产物是文件内容 + 启动命令，不在服务端构建；`requires_human_build=true` 时由用户自行落地。
- **模型鉴权失败会以 `LLM_AUTH_ERROR` 明确失败**（401/403，不可重试），错误信息直接提示更新 `backend/.env` 的 `LLM_API_KEY` 并重启服务；排查用 `.venv/Scripts/python.exe scripts/check_llm.py`。
- **控制意图由 Worker 每轮扫描**：`end_audit`/`cancel_audit` 在活跃 Run 上只登记意图（`control_intents`，状态 `accepted`），由 `RunWorker.tick()` 每轮调用 `command_service.reconcile_control_intents` 在旧 Run 终结后建唯一 closing Run（或完成取消）。停掉 Worker 时结束/取消审计会停在 `scheduled`，属预期。
- **闭环兜底终结会收口**：`workflows.runner._force_terminal` 与排队超时路径会调 `command_service.settle_loop_for_run_failure`，把 `loop/loop_round` 置 `failed` 并写 `loop.updated`；只收口 `loop.latest_run_id` 指向的 Run，迟到兜底不会误关已推进的闭环。
- **前端 `retryFailedRun` / `cancelRun` 返回 `boolean`**：失败时返回 false 并写 `error`/toast，页面据此展示失败而不是「已重新排队」。
- **低额度模式**：`backend/.env` 设 `LLM_BUDGET_PROFILE=lean|frugal` 后，`services.command_service.default_budget()` 用 `min()` 只收紧不下调契约常量，生效值写入每条 Run 的 `budget_json`（重放/迁移口径不变）；`/health/detail` 暴露 `budget_profile` 与 `budget_limits`。
- **事件压缩由 Worker 每小时驱动**：只删已终结 Run 的旧事件（每个 Run 保留最后一个），压缩后旧游标返回 410 `EVENT_CURSOR_EXPIRED`（`resolve_start_cursor` 的 watermark = 第一个仍可回放的 seq）。
- 旧 SQLite 库缺新列时由 `repositories/database.py::_additive_schema_upgrade()` 在启动时自动补列（当前覆盖 `runs.failed_steps_json`、`runs.loop_id`、`steps.created_at`）。缺列曾导致「发消息一律失败（INTERNAL / no such column: steps.created_at）」，已在 `backend/tests/test_schema_upgrade.py` 固化回归。

## 6. 本机验证的临时产物约定

本机 `TEMP` 不可用，`tempfile.gettempdir()` 会回退到当前工作目录。测试/脚本已统一使用 `backend/.tmp/` 或显式临时目录；`.gitignore` 覆盖 `.tmp/`、backend 下的 `.tmp_tests/` 目录、`backend/pm_api_v2_*/`、`backend/pm_legacy_*/` 等模式，避免污染仓库。

## 7.1 第 40 批补记（2026-11-26 上线实测）

- 线上已切到 V4：GitHub `main` = `04b4476`（备份分支 `backup/v3-production`、tag `v3-production-backup` 指向 V3 的 `16d3cc6`）；
  Vercel 自动重建，`https://www.brainstorming.top` 首页标题已变成「产品决策智能工作台」，
  OpenAPI 里能查到 `/api/v2/access`、`/api/v2/groups`、`/api/v2/sessions/{session_id}/share`、
  `/api/v2/loops/{loop_id}/sandbox-runs` 等 V4 路由。
- HF Space 不收二进制（push 被 pre-receive 拒绝：`Your push was rejected because it contains binary files.`），
  因此 Space 仓库只保留可部署子集（`Dockerfile`、`.dockerignore`、`README.md`、`backend/`、`scripts/container_start.py`，
  159 个文件 2.0MB），前端与截图只留在 GitHub 主仓。
- Space 封 SMTP 出站：`POST /api/auth/email/code` 曾卡 20.6 秒后由 Vercel 反代返回 502。
  镜像因此默认 `SMTP_TIMEOUT_SECONDS=8` + `AUTH_CODE_LOG_FALLBACK=true`：投递失败立刻返回
  `delivery="logs"`，验证码写进 Space Logs 由所有者转达；配好 `EMAIL_PROXY_URL` 或可用 SMTP 后应关掉该兜底。

## 6.7 请求号贯通与部署变量自检（第 44 批）

**请求号（request_id）**：每个请求都会得到一个 id —— 调用方传了 `X-Request-ID`（或 `Idempotency-Key`）就用它，否则服务端生成 `req_xxxxxxxxxxxx`。它同时出现在三处，用户报障时按顺序对齐即可定位：

1. 响应头 `X-Request-ID`（浏览器 Network 面板可见）；
2. 错误体 `{"error": {"request_id": ...}}`（前端提示里会带出来）；
3. 服务端每条日志的 `[rid=...]` 段（`core/log_context.py` 用 `ContextVar` 注入，异步并发下不会串；Worker 与启动期日志回落 `-`）。

**部署变量自检**：`python scripts/check_deploy_environment.py --env-file backend/.env`（加 `--json` 供脚本消费，`--skip-network` 跳过端口探测）。判定项与常见修法：

| 项 | fail 条件 | 修法 |
| --- | --- | --- |
| database | `DATABASE_URL` 主机/端口连不上 | 放行出口 IP、核对端口；无 URL 只记 warn（SQLite 会随重启丢） |
| auth | `AUTH_SECRET_KEY`（兼容旧名 `JWT_SECRET`）缺失或 <32 字符 | 换成 `python -c "import secrets;print(secrets.token_urlsafe(32))"` 生成的串 |
| cookie | 线上 HTTPS 但 `AUTH_COOKIE_SECURE` 未开（warn） | 设 `AUTH_COOKIE_SECURE=true` |
| cors | `CORS_ORIGINS` 含 `*` | 写具体域名；代码侧也会丢弃通配符 |
| email | 既无 `EMAIL_PROXY_URL` 也无可用 SMTP（warn/fail） | 配 SMTP 或 `EMAIL_PROXY_URL`；容器封端口时只能走代理 |
| llm | `LLM_KEY_MODE=system` 但 `LLM_API_KEY` 为空 | 填账户 Key，或改回 `byok` |
| storage | `SESSION_DATA_DIR` 不可写 | 换可写目录或修权限 |

发版体检（`scripts/pre_release_check.py`）已把这项加为第二步 `deploy-env`：warn 不阻断，fail 直接让 `PRE_RELEASE_FAILED`。

## 6.8 线上必须同源接入（第 42 批，P0）

线上前端（Vercel）访问后端必须走同源 `/backend` 反代，**不能**直连 HF Space 域名：

- HF 平台代理会拦下跨域预检，响应里没有 `Access-Control-Allow-Credentials`，浏览器对所有带
  `Content-Type: application/json` 的 POST/PUT/DELETE 直接拒发（`net::ERR_FAILED`）；
- 一旦发生，登录发码、建会话、发消息、改配置全部失败，而界面只会显示网络类文案，极难定位；
- 代码侧已强制：`frontend/lib/api.ts::resolveApiBase()` 在非本地页面一律返回 `/backend`，
  只有显式设置 `NEXT_PUBLIC_ALLOW_CROSS_ORIGIN_API=1` 才尊重绝对地址（仅本地调试用）。

排查命令（预期域名为 `www.brainstorming.top`，不应出现 `hf.space`）：

```powershell
curl.exe -s "https://www.brainstorming.top/_next/static/chunks/<latest>.js" | Select-String 'hf\.space'
```

## 6.9 已知限制：HF Space 网关抖动与客户端自动重试（第 41 批）

2026-11-26 上线后实测：Vercel 前端经 `/backend/*` 反代到 Hugging Face Space 时，
约 5%~10% 的请求会在 **21 秒**左右被前置代理直接断开（`curl` 返回 `000`，无响应体；
直连 `https://123chenjunliang-pm-brainstorm-workbench.hf.space/api/v2/...` 同样复现，
因此可以定位在 Space 前置代理层，而不是 Vercel 反代或某个接口的业务逻辑）。

用户在浏览器里看到的现象是：进「模型 API 设置」页报「读取模型配置失败：网络连接失败」，
或页面直接透出英文 `Failed to fetch`。修复只在客户端做（后端无需变更）：

- `apiV2` 对瞬时故障（`fetch` TypeError、客户端超时、网关 502/503/504）自动重试 3 次，
  退避 700/1800ms；重试时单次超时收紧到 8s/6s，整体最长等待从 45s 降到约 29s。
- GET 默认开启重试；写请求默认关闭，携带 `Idempotency-Key` 的幂等写（建会话、命令、
  取消、重试失败专家）显式开启，三次尝试复用同一个幂等键，后端按 `request_id` 去重。
- SSE 首次连接失败也重试 2 次（1200/3000ms），避免事件流一个字都没收到就 EOF。
- `errorMessage` 把 `TypeError` / `AbortError` / 英文 `Failed to fetch` 统一成中文可操作
  文案；`isEndpointUnavailable` 不再把 `TypeError` 当「端点不存在」（旧实现会让一次网络
  抖动把会话永久降级到 legacy 写路径）。
- 模型配置页读取失败会静默重试两次（2s/4s），仍失败才展示错误并提供「重新读取配置」按钮。

长期方案仍建议换稳定后端（HF 免费实例还有「重启丢 SQLite」与「封 SMTP」两个限制），
或者给 Space 配 `EMAIL_PROXY_URL` 恢复邮件投递。

## 7. 线上部署（Vercel 前端 + Hugging Face Space 后端）

线上站点 <https://www.brainstorming.top>：前端 Next.js 由 Vercel 从本仓库 `frontend/` 构建，后端 FastAPI 由 Hugging Face Space（docker SDK）构建，浏览器同源访问 `/backend/*` 由 `frontend/next.config.js` 的 rewrites 反代到 Space。

部署顺序：

1. **后端先上**：Space 使用仓库根 `Dockerfile`，`CMD` 起 `scripts/container_start.py`（同容器内托管 API + Worker），`EXPOSE 7860`；构建期已执行 `rag.index_builder.build_index()`。
   Space 必须配置：`AUTH_SECRET_KEY`（≥32 字符，缺了登录 500）、`SMTP_HOST/SMTP_PORT/SMTP_USERNAME/SMTP_PASSWORD/SMTP_FROM`、`AUTH_COOKIE_SECURE=true`、`LLM_KEY_MODE=byok`；SMTP 出站被平台封锁时改用 `EMAIL_PROXY_URL`/`EMAIL_PROXY_KEY`。
2. **前端再上**：Vercel 读 `frontend/vercel.json`（`NEXT_PUBLIC_API_URL=/backend`）；如后端不在默认 Space，改 `BACKEND_ORIGIN`。
3. **冒烟**：`GET /health`、`GET /backend/health`、打开首页、走一次邮箱验证码登录、建会话看首轮提问、打开 `/ops` 与 `/settings/access`。

回滚：V3 线上版本保存在 `backup/v3-production` 分支；需要回退时把该分支强制推回 `main`，Vercel 与 Space 会重新构建上一版。

本地无 Docker 时可用同一套启动器模拟容器：`cd backend && python ../scripts/container_start.py --port 8099 --app-dir .`。

