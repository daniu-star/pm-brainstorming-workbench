FROM python:3.11-slim

WORKDIR /app

ENV PYTHONPATH=/app
ENV BUILD_VERSION=2

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .
COPY scripts/ ./scripts/

RUN python scripts/build_knowledge_base.py

RUN mkdir -p /app/static /app/data/sessions /app/data/users

EXPOSE 7860

ENV LLM_API_KEY=""
ENV LLM_BASE_URL="https://api.openai.com/v1"
ENV LLM_MODEL="gpt-4o"
ENV HF_API_TOKEN=""
ENV HF_WHISPER_MODEL="openai/whisper-large-v3"
ENV SESSION_DATA_DIR="/app/data/sessions"
ENV USER_DATA_DIR="/app/data/users"
ENV BACKEND_PORT=7860

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]
