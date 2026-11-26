# V4 容器镜像（第 40 批）：Hugging Face Space / 任意 Docker 平台共用。
#
# 与 V3 的关键差异：V4 的运行模型是「API 只落库、Run Worker 领取执行」（ADR-04），
# 单容器平台只给一个前台进程，因此用 scripts/container_start.py 同时托管
# uvicorn 与 workers.run_worker，任一子进程退出即整体退出交给平台重启。
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 后端包根目录直接摊到 /app（main.py、api/、core/、workers/ 都在这一层）。
COPY backend/ ./
COPY scripts/container_start.py ./scripts/container_start.py

# RAG 索引在镜像里预先构建：backend/data 被 .gitignore 忽略，容器内必须自建；
# 启动期 main.py 的 lifespan 仍会做一次 build_if_stale 兜底。
RUN python -c "from rag.index_builder import build_index; build_index()" \
    && mkdir -p /app/data

# 运行期数据（SQLite + 会话产物）落 /app/data；平台无持久卷时重启即丢，
# 需要长期保存请挂卷或改用 DATABASE_URL 指向外部数据库。
ENV DATABASE_URL=""
ENV SESSION_DATA_DIR="/app/data/sessions"
ENV CHROMA_PERSIST_DIR="/app/data/chroma_db"

# 默认口令模式：每用户自带模型 Key（BYOK），服务端不承担模型成本。
ENV LLM_KEY_MODE="byok"
ENV LLM_DISABLE_REASONING="true"

# 认证必须有 AUTH_SECRET_KEY（≥32 字符）；邮箱登录必须配 SMTP_*。
# 这两组不在镜像里写死，请在平台的 Secrets / Variables 中设置。
ENV AUTH_COOKIE_SECURE="true"
ENV AUTH_COOKIE_SAMESITE="lax"

ENV BACKEND_HOST="0.0.0.0"
ENV BACKEND_PORT="7860"

EXPOSE 7860

CMD ["python", "/app/scripts/container_start.py", "--port", "7860", "--app-dir", "/app"]
