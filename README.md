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
