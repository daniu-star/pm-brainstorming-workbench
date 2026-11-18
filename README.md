# PM Brainstorming Workbench

AI-powered product brainstorming workbench with multi-role agents and visual canvas.

## Features

- **Multi-Role Brainstorming**: AI plays CTO, Designer, Operations, and Target User simultaneously
- **Visual Canvas**: Real-time feature tree visualization using React Flow
- **AI Interviewer Mode**: Role-reversal stress-testing of product plans
- **RAG Knowledge Base**: Pre-loaded with PM methodologies, case studies, and benchmarks
- **Cold & Objective Tone**: Agent critiques ideas without praise or cheerleading
- **Hybrid Speech Recognition**: Server-side STT when configured, with browser recognition fallback
- **Email Code Login**: Passwordless sign-in through NetEase SMTP verification codes
- **Browser Gaze Measurement**: Local MediaPipe face, iris, head-pose, and gaze analysis in interview mode

## Tech Stack

- **Frontend**: Next.js 14, React Flow, Zustand, Tailwind CSS
- **Backend**: Python FastAPI, SSE streaming
- **AI**: OpenAI-compatible API (GPT-4o / DeepSeek)
- **Vector DB**: ChromaDB (embedded)

## Quick Start

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
- M2/M3 尚未实现的动作（如 `brainstorm` 的四角色工作流、审计闭环）在 Run 里以
  `failed(FEATURE_NOT_READY)` 明确失败，不会静默降级到旧代码。
