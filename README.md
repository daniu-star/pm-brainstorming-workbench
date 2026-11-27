---
title: PM Brainstorm Workbench
emoji: 🧠
colorFrom: yellow
colorTo: red
sdk: docker
app_port: 7860
pinned: false
---

# PM Brainstorming Workbench

AI-powered product brainstorming workbench with multi-role agents and visual canvas.

## 线上部署（Vercel 前端 + Hugging Face Space 后端）

线上站点是 <https://www.brainstorming.top>：**前端 Next.js 跑在 Vercel**，**后端 FastAPI 跑在 Hugging Face Space（Docker SDK）**，
浏览器与前端同源通信，由 `frontend/next.config.js` 的 rewrites 把 `/backend/:path*` 反代到后端容器。

### 目录里与部署相关的文件

| 文件 | 作用 |
| --- | --- |
| `Dockerfile` | 后端镜像：`python:3.11-slim` + `backend/requirements.txt`，构建时预建 RAG 索引，`CMD` 走 `scripts/container_start.py` |
| `.dockerignore` | 只把 `backend/` 与 `scripts/container_start.py` 送进构建上下文（不传 `frontend/`、`data/`、`.env`） |
| `scripts/container_start.py` | 单容器里同时托管 `uvicorn main:app` 与 `workers.run_worker`（ADR-04 要求 Worker 常驻领取 Run） |
| `frontend/vercel.json` | Vercel 项目配置：`framework=nextjs`，`NEXT_PUBLIC_API_URL=/backend` |
| `frontend/next.config.js` | `/backend/:path*` → `BACKEND_ORIGIN`（默认指向本项目的 HF Space） |
| `frontend/.env.example` | 本地用 `http://127.0.0.1:8001`，线上用 `/backend` |

### 为什么必须两个进程

V4 的命令先落库并返回 `run_id`，由独立 Worker 领取执行（ADR-04），SSE 只是订阅通道。
单容器平台只提供**一个**前台进程，所以 `container_start.py` 用标准库 `subprocess` 同时拉起 Worker 与 API，
Worker 先起 1.5 秒（先把表建好、把上次的孤儿 Run 收口），任一子进程退出就整体退出交给平台重启。

### Hugging Face Space 需要配置的变量

Space 的 *Settings → Variables and secrets* 里至少要有：

| 变量 | 说明 |
| --- | --- |
| `AUTH_SECRET_KEY` | **必填**，≥32 字符随机串；缺失时登录会报 `AuthConfigurationError` |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USERNAME` / `SMTP_PASSWORD` / `SMTP_FROM` | 邮箱验证码登录必需（网易 163 用客户端授权码作密码） |
| `AUTH_COOKIE_SECURE` | 线上为 `true`（HTTPS） |
| `LLM_KEY_MODE` | 默认 `byok`：每个账号在「模型 API 设置」里填自己的 Key，服务端不承担模型成本 |
| `EMAIL_PROXY_URL` / `EMAIL_PROXY_KEY` | 可选。容器平台封 SMTP 出站端口时，用 HTTP 代理投递验证码；未配置或代理失败会自动回落直连 SMTP |
| `DB_SNAPSHOT_REPO` / `DB_SNAPSHOT_TOKEN` | 可选。免费实例没有持久卷时，把 SQLite 周期快照推送到私有 dataset（如 `owner/pm-brainstorm-db`），启动时先恢复再建表；令牌可复用 Space secret `DB_SYNC_TOKEN`。**快照不是强一致，最坏丢一个周期** |
| `DB_SNAPSHOT_INTERVAL_SECONDS` | 快照周期，默认 `120`，下限 `30` |
| `CORS_ORIGINS` | 兜底用；同源反代下 CORS 不参与，但直连调试时需要 `https://www.brainstorming.top` |

> 从 V3 升级上来时的变量名：V4 仍兼容旧的 `JWT_SECRET` / `SMTP_FROM_EMAIL`（代码里自动回退），
> 新名字分别是 `AUTH_SECRET_KEY` / `SMTP_FROM`；V3 的 `SMTP_USE_TLS` 在 V4 没有对应项（只用 `SMTP_USE_SSL`）。

### 持久化

SQLite 默认落在镜像内的 `/app/data` 目录（`app.db`）。HF Space 免费实例**没有持久卷**，重启会回到干净库；
要长期保存数据，把 `DATABASE_URL` 指向外部数据库（如托管 Postgres，SQLAlchemy 直接可用），
或把 `/app/data` 挂成持久卷。

### 手动构建与本地验证

```bash
# 有 Docker：构建并起容器（首次会 pip 安装依赖 + 构建 RAG 索引）
docker build -t pm-brainstorm-v4 .
docker run --rm -p 7860:7860 -e AUTH_SECRET_KEY=<32位随机串> pm-brainstorm-v4

# 没有 Docker：用同一套启动器在宿主机模拟容器（不装 Docker 也能验进程编排）
cd backend && python ../scripts/container_start.py --port 8099 --app-dir .
```

### 验证码发不出去时

HF Space 会封掉 25 / 465 / 587 出站（连不上 `smtp.163.com`），此时邮箱验证码登录会超时。三条路：

1. **配 `EMAIL_PROXY_URL` / `EMAIL_PROXY_KEY`**：验证码先 POST 给代理投递；代理不可用会自动回落直连 SMTP，不会把登录打挂。
2. 换成支持 HTTPS 发信的邮件服务商，同样通过 `EMAIL_PROXY_URL` 接。
3. **镜像内置兜底**：`Dockerfile` 默认 `AUTH_CODE_LOG_FALLBACK=true` + `SMTP_TIMEOUT_SECONDS=8`。
   投递失败时不再卡住请求，而是立刻把验证码写进服务日志（Space → Logs，仅所有者可见）并返回
   `delivery="logs"`，登录页会提示「验证码已写入服务日志，请联系平台管理员获取」。
   验证码依然一次性、10 分钟过期，公开站点请改配真 SMTP / 代理后把这个开关关掉。

日志里看 `event=auth.email_proxy_sent`（代理成功）、`auth.email_proxy_failed`（代理失败已回落）、
`auth.email_proxy_rejected`（代理返回 success=false）、`auth.code_log_fallback`（已写日志兜底）四种事件即可判断当前走哪条路。

### 重新部署 Space（只推可部署子集）

HF Space 的 pre-receive 钩子拒收二进制文件（仓库里的 MediaPipe wasm、浏览器验收截图），
所以 Space 只接收 `Dockerfile`、`.dockerignore`、`README.md`、`backend/`、
`scripts/container_start.py` 这一份子集。用脚本一键完成：

```bash
git push origin feat/prd-v4-m1:main          # 先让 Vercel 重建前端
python scripts/deploy_hf_space.py --dry-run  # 看将推送哪些文件
python scripts/deploy_hf_space.py            # 子集提交 + 强推 Space main，自动 rebuild
```

脚本在临时 worktree 里基于 `space-main` 分支重建提交，不会动主分支；无变更时直接跳过。
推送后 Space 的 runtime.stage 会经历 BUILDING → RUNNING；用浏览器或 curl 打开
https://www.brainstorming.top/backend/health 与 https://www.brainstorming.top/backend/openapi.json
（后者能列出 /api/v2/access 等新路由）确认新版上线。

### 免费实例如何保住数据（第 46 批）

HF 免费实例没有持久卷，容器一重启 `/app/data` 就没了。三条路，按可靠性排序：

1. **外部 Postgres**（`DATABASE_URL=postgresql://…`）——最稳，需要自己有一个库；
2. **SQLite 周期快照到私有 dataset**（本仓内置）：
   - 建一个私有 dataset（例如 `owner/pm-brainstorm-db`），把有写权限的令牌写成 Space secret `DB_SYNC_TOKEN`；
   - 配变量 `DB_SNAPSHOT_REPO=owner/pm-brainstorm-db`、`DB_SNAPSHOT_INTERVAL_SECONDS=120`；
   - 启动时 `scripts/container_start.py` 会**先恢复快照、再建表**，运行期每 120 秒推一次；退出前再推一次；
   - 代价：快照不是强一致，容器被硬杀最多丢最近一个周期的写入（`/health/ready` 的 `persistence.kind=snapshot` 会写清 `worst_case_loss_seconds`）；
3. **什么都不配**——重启即清空，只适合演示；`/health/ready` 会返回 `persistent: false` 并在 `degraded_reasons` 里写明。

### 部署顺序

1. 后端先上（Space build 完成、`/health` 返回 200）；
2. 前端再上（Vercel 读 `frontend/vercel.json`，`NEXT_PUBLIC_API_URL=/backend`）；
3. 冒烟：`GET /backend/health`、打开首页、走一次邮箱验证码登录、建一个会话看到首轮澄清回复。

## Features

- **Multi-Role Brainstorming**: AI plays CTO, Designer, Operations, and Target User simultaneously
- **Visual Canvas**: Real-time feature tree visualization using React Flow
- **AI Interviewer Mode**: Role-reversal stress-testing of product plans
- **RAG Knowledge Base**: Pre-loaded with PM methodologies, case studies, and benchmarks
- **Cold & Objective Tone**: Agent critiques ideas without praise or cheerleading
- **Hybrid Speech Recognition**: Server-side STT when configured, with browser recognition fallback
- **Email Code Login**: Passwordless sign-in through NetEase SMTP verification codes
- **Browser Gaze Measurement**: Local MediaPipe face, iris, head-pose, and gaze analysis in interview mode
- **Session Sharing & Permissions**: share a session with teammates as viewer/editor, group them into reusable member groups, and review everything on one `/settings/access` page
- **Loop Execution Sandbox**: opt-in (fail-closed) command runner that records every run as a loop artifact
- **Ops Health Page**: `/ops` surfaces worker heartbeat, queue depth, storage size, sandbox and report-quality signals

## Tech Stack

- **Frontend**: Next.js 14, React Flow, Zustand, Tailwind CSS
- **Backend**: Python FastAPI, SSE streaming
- **AI**: OpenAI-compatible API (GPT-4o / DeepSeek)
- **Vector DB**: ChromaDB (embedded)

## Quick Start

### 0. 一键启动（Windows，推荐）

配好 `.venv` 与 `frontend/node_modules` 之后，双击仓库根目录的 `start.bat` 即可：

- 自动初始化 SQLite 数据库，然后拉起三个服务，各自一个控制台窗口：
  - `PM-API    :8001` — `uvicorn main:app`（`/api/v2`、`/docs`）
  - `PM-Worker` — `python -m workers.run_worker`（按租约领取并执行 Run）
  - `PM-Web    :3001` — `next dev`（`NEXT_PUBLIC_API_URL` 指向本次 API 端口）
- 端口被其他程序占用时自动顺延（8001→8002…，3001→3002…），并同步 `CORS_ORIGINS`
- 等 `/health` 与首页都返回后才算启动完成，然后自动打开浏览器
- 重复双击不会起第二套：检测到已运行就只打开浏览器
- `stop.bat` 按 PID 结束三个窗口及其子进程；`.run/` 存放 PID 与端口（已 gitignore）

命令行参数（可选）：`start.bat -NoBrowser`、`-ApiPort 8002`、`-WebPort 3002`、`-SkipWorker`、`-Force`（强制重启）。

> 注意：前端与后端必须使用同一主机名（脚本统一用 `127.0.0.1`），否则 `SameSite=lax` 的登录 Cookie 不会随跨站请求发送。

### 1. Install Dependencies

**Backend:**
```bash
cd backend
pip install -r requirements.txt
```

**Frontend:**
```bash
cd frontend
npm install
```

### 2. Configure API Key

```bash
cp backend/.env.example backend/.env
# Edit backend/.env with your API key
```

发送消息后如果界面提示「模型服务鉴权失败 / LLM_AUTH_ERROR」，先跑一次自检定位问题（不会改任何数据）：

```bash
.venv/Scripts/python.exe scripts/check_llm.py
```

自检会打印 `base_url` / `model` / 脱敏后的 key，并区分「key 无效（401/403）」「余额不足（402）」
「模型名或地址错误（404）」「被限流（429）」「网络不可达」。改完 `backend/.env` 后执行
`stop.bat` → `start.bat` 让 API/Worker 重新读取配置。

Configure the frontend API address when it is not served from the default local backend:

```bash
cp frontend/.env.example frontend/.env.local
```

### 3. Configure Email Login

Enable SMTP in the NetEase mailbox settings and use the generated **client authorization code**, not the mailbox password:

```env
SMTP_HOST=smtp.163.com
SMTP_PORT=465
SMTP_USERNAME=your-account@163.com
SMTP_PASSWORD=your-smtp-authorization-code
SMTP_FROM=your-account@163.com
SMTP_USE_SSL=true
AUTH_SECRET_KEY=a-random-secret-with-at-least-32-characters
```

For HTTPS deployments set `AUTH_COOKIE_SECURE=true`. If the frontend and API use different sites, also set `AUTH_COOKIE_SAMESITE=none` and list the frontend origin in `CORS_ORIGINS`.

### 4. Build Knowledge Base

```bash
python scripts/build_knowledge_base.py
```

### 5. Start Development Servers

**Backend:**
```bash
cd backend
uvicorn main:app --reload --port 8000
```

**Frontend:**
```bash
cd frontend
npm run dev
```

Open http://localhost:3000

## Project Structure

```
├── frontend/          # Next.js frontend
│   ├── app/           # Pages and layout
│   ├── components/    # Chat and canvas components
│   ├── store/         # Zustand state management
│   └── lib/           # API and SSE helpers
├── backend/           # Python FastAPI
│   ├── core/          # Agent logic, prompts, LLM client
│   ├── api/           # REST and SSE endpoints
│   ├── rag/           # RAG knowledge retrieval
│   └── db/            # Session storage
├── scripts/           # Build scripts
└── data/              # Runtime data (git-ignored)
```

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `LLM_API_KEY` | OpenAI-compatible API key | (required) |
| `LLM_BASE_URL` | API base URL | `https://api.openai.com/v1` |
| `LLM_MODEL` | Model name | `gpt-4o` |
| `LLM_BUDGET_PROFILE` | Token/call budget profile: `standard` / `lean` / `frugal` (only tightens contract limits) | `standard` |
| `LLM_DISABLE_REASONING` | Disable provider-side reasoning/thinking for models that spend the completion budget on `reasoning_content` (currently auto-applied to `deepseek*`); set `false` to keep provider default | `true` |
| `ACCOUNT_DAILY_TOKEN_LIMIT` | Per-account rolling 24h token limit (used tokens + worst-case reservation of queued runs); `0` disables. Over quota: HTTP 429 `ACCOUNT_QUOTA_EXCEEDED` | `0` |
| `ACCOUNT_DAILY_CALL_LIMIT` | Per-account rolling 24h upstream-call limit; `0` disables | `0` |
| `LLM_KEY_MODE` | `byok` (default) — platform is free but does not pay for model usage: every account must save its own base_url/model/api_key under 模型 API 设置 before creating sessions; `system` — reuse the shared `.env` key (local dev / automated tests only) | `byok` |
| `LOOP_SANDBOX_ENABLED` | Loop Studio 执行沙箱总开关（**fail-closed**：默认关闭；开启后仍需白名单）。进程级隔离，不是安全边界，只在本地/受信环境开启 | `false` |
| `LOOP_SANDBOX_ALLOWED_COMMANDS` | 允许的解释器白名单（逗号分隔，按文件名或去扩展名匹配，如 `python,node`）；为空时一律 503 `SANDBOX_DISABLED` | (empty) |
| `LOOP_SANDBOX_TIMEOUT_SECONDS` | 单次执行超时（秒），超时强杀进程树 | `20` |
| `LOOP_SANDBOX_MAX_OUTPUT_BYTES` | 单次 stdout/stderr 保留上限（超出截断并标 `truncated`） | `8192` |
| `LLM_EMBEDDING_MODEL` | Embedding model | `text-embedding-3-small` |
| `CORS_ORIGINS` | Comma-separated allowed frontend origins | local ports 3000/3001 |
| `STT_API_KEY` | Optional API key for server-side speech recognition | (browser fallback when omitted) |
| `STT_BASE_URL` | OpenAI-compatible audio API base URL | `https://api.openai.com/v1` |
| `STT_MODEL` | Audio transcription model | `whisper-1` |
| `STT_LANGUAGE` | Transcription language hint | `zh` |
| `STT_TIMEOUT_SECONDS` | Server STT request timeout | `45` |
| `STT_MAX_AUDIO_BYTES` | Maximum uploaded recording size | `12582912` |
| `TTS_TIMEOUT_SECONDS` | TTS request timeout | `30` |
| `TTS_MAX_CHARACTERS` | Maximum text length per TTS request | `3000` |
| `SMTP_HOST` | SMTP server hostname | `smtp.163.com` |
| `SMTP_PORT` | SMTP SSL/TLS port | `465` |
| `SMTP_USERNAME` | NetEase mailbox account | (required) |
| `SMTP_PASSWORD` | NetEase SMTP client authorization code | (required) |
| `SMTP_FROM` | Sender mailbox | defaults to SMTP username |
| `AUTH_SECRET_KEY` | HMAC secret for signed login sessions, at least 32 chars | (required) |
| `AUTH_COOKIE_SECURE` | Send auth cookie only over HTTPS | `false` |
| `AUTH_SESSION_DAYS` | Login session duration | `7` |

## V4 architecture (PRD V4.0, M1 已落地)

V4 把「会话历史 / 产品状态 / 上下文装配」拆成三个独立层（PRD ADR-02），所有写操作走
「命令先落库 → 独立 Worker 领取执行 → 结果与事件同事务提交」的持久执行模型（ADR-04）。

```
backend/
├── domain/          # canonical 契约：contracts.py（实体/枚举/限额）、state_machine.py、errors.py
├── repositories/    # SQLAlchemy 2.x 仓储：database.py(18 张表) / session_repo / run_repo / audit_repo / auth_repo
├── services/        # 应用服务：command_service（命令入口+幂等+提交）、context_builder、canvas_projector、reducer
├── workflows/       # 工作流：runner（Run 生命周期）、coach（澄清）、brainstorm（四角色）、audit（审计）
├── workers/         # run_worker：常驻 Worker，领取 Run、续租、执行、回收
├── migrations/      # import_json_sessions.py：旧 JSON 会话迁移（--dry-run / --apply）
└── api/v2/          # /api/v2/* 新端点；api/* 旧路由保留为 adapter
```

### 数据库与 Worker

```bash
cd backend
# 首次运行自动建表（SQLite，WAL + foreign_keys=ON + busy_timeout=5000）
# 启动 API 时会自动 initialize_database()；也可手动执行：
python -c "from repositories.database import initialize_database; initialize_database()"

# 启动常驻 Worker（独立进程；API 只负责接命令与订阅事件）
python -m workers.run_worker            # 单轮调试：--once
```

环境变量 `DATABASE_URL` 可覆盖数据库位置（默认 `backend/data/app.db`）。

### 会话共享与权限（第 39 批）

```bash
GET    /api/v2/access                          # 权限总览：我创建的 / 共享给我的 / 成员组 / 计数
GET    /api/v2/sessions/{id}/shares            # 共享成员列表（所有者与成员都能看）
POST   /api/v2/sessions/{id}/shares            # 仅所有者：加人（viewer / editor）
DELETE /api/v2/sessions/{id}/shares/{email}    # 仅所有者：移除
POST   /api/v2/sessions/{id}/shares/from-group # 仅所有者：按成员组一次性授权
DELETE /api/v2/sessions/{id}/share             # 成员主动退出共享
```

- 每个 `SessionView`（v2 列表/详情）与旧 `/api/session` 列表/详情都带 `my_role`，前端据此隐藏删除、共享管理等所有者操作。
- 成员组只存名单、不授予权限；`session_shares` 行才是授权；删除成员组不回收已授予的访问。
- 权限页 `/settings/access` 汇总三类信息，并提供「退出共享」。

### 旧数据迁移

```bash
cd backend
python -m migrations.import_json_sessions --source <旧 sessions 目录> --dry-run
python -m migrations.import_json_sessions --source <旧 sessions 目录> --apply
```

迁移不删除、不移动、不覆盖源文件；空 owner 的旧会话隔离为 `legacy-unknown@local`；
旧画布存为 legacy artifact；重复执行按源文件 hash 幂等跳过。

### 测试

```bash
cd backend && ../.venv/Scripts/python.exe -m unittest discover -s tests -v   # 后端全量
cd frontend && npm test                                                     # 类型检查 + node --test
cd frontend && npm run test:e2e                                             # 真起后端进程的 API 冒烟
```

### 兼容性

- `/api/v2/*` 为 V4 端点；`/api/session`、`/api/brainstorm`、`/api/canvas`、`/api/interview`
  保留一个迁移版本，由 adapter 调用同一套应用服务（不存在第二条不受状态机约束的写路径）。
- `PUT /api/canvas` 返回 `409 CANVAS_IS_DERIVED`：V4 画布由 `project_canvas(product_state)` 确定性投影，不再手工写入。
- 已注册的工作流：`clarify`（教练）、`brainstorm`（四角色 + 确定性归并）、
  `audit_open` / `audit_answer` / `audit_report`（审计闭环）；未注册的动作在 Run 里以
  `failed(FEATURE_NOT_READY)` 明确失败，不会静默降级到旧代码。
- 画布只从 `product_state` 投影生成；Redis 不参与（验证码/限流/幂等全部走共享 DB）。

### 文档

完整索引（按用途分组，含「何时读」）：`docs/README.md`。最常用的几份：

- 发版说明（一页纸）：`docs/v4-release-notes.md`
- 验收报告（AC-01…AC-42 逐条 + 已知偏差）：`docs/v4-acceptance-report.md`
- 发布就绪清单（部署顺序、回滚、验收命令）：`docs/v4-release-checklist.md`
- 模块接口契约（仓储/服务/SSE 语义）：`docs/v4-m1-interfaces.md`
- 逐批热修总表 + 当前基线：`docs/loop-runs/README.md`
- 真实链路证据（浏览器 e2e / 完整旅程 / 报告路径 / 数字标注）：`docs/browser-qa.md`、
  `docs/full-journey-e2e.md`、`docs/audit-report-e2e.md`、`docs/ac20-number-provenance.md`
- 可复用 Loop 技能包（可开源）：`skills/loop-engineering-product/SKILL.md`
- **发版前一键体检**：`python scripts/pre_release_check.py`（文档体检 + 部署环境自检 + 后端全量 + 前端类型/用例 + 真实浏览器 e2e + 备份校验恢复演练；服务没起时加 `--skip-browser`）
- **部署变量自检**：`python scripts/check_deploy_environment.py --env-file backend/.env`（数据库/认证密钥/Cookie/CORS/邮件/模型/存储逐项判定 ok·warn·fail，fail 才返回非零；不发邮件、不调模型）
- 模型连通性自检：`python scripts/check_llm.py`
- 文档引用体检（CI 已接入）：`python scripts/verify_docs_refs.py --strict`
- 磁盘清理（可重建产物，默认只报告）：`python scripts/disk_cleanup.py`（确认后 `--apply`，npm 缓存用 `--npm-cache`）
- 数据库增长与回收：`python scripts/db_maintenance.py report`（每表字节/行数/文本列），`python scripts/db_maintenance.py vacuum`（回收删除行留下的空闲页）
