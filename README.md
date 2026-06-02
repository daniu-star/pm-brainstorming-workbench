---
title: PM Brainstorm Workbench
emoji: 🧠
colorFrom: yellow
colorTo: red
sdk: docker
app_port: 7860
pinned: false
---

# AI产品头脑风暴工作台

AI-powered product brainstorming workbench with multi-role agents and visual canvas.

## Features
- 🎭 Multi-Role Brainstorming (CTO/Designer/Ops/User)
- 🎨 Visual Canvas (Timeline View)
- 🔍 AI Interviewer Mode
- 📚 RAG Knowledge Base
- 🎙️ Voice Interaction (edge-tts)
- ⚡ SSE Streaming

## Tech Stack
- **Frontend**: Next.js 14 (Static Export) + Zustand + Tailwind CSS
- **Backend**: FastAPI + SSE + edge-tts
- **AI**: OpenAI-compatible API + RAG
- **Deployment**: Docker (Frontend + Backend in one container)

## Usage
This Space hosts both the **frontend and backend** in a single Docker container.

Users can bring their own API key (BYOK mode) if no default key is configured.
