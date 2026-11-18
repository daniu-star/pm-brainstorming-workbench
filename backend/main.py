"""FastAPI 应用入口。

M1（v4 契约）变更：
* 所有新端点在 ``/api/v2`` 前缀下（``api.v2``），旧路由（/api/session、/api/brainstorm、
  /api/canvas、/api/interview）保留为**适配器**，共享同一套仓储与命令服务；
* 统一错误体由 ``api.errors.install_error_handlers`` 注册；
* 启动时初始化 SQLite 表（本地/测试快速路径），关闭时清理兼容垫片。
"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.auth_routes import router as auth_router
from api.brainstorm_routes import router as brainstorm_router
from api.canvas_routes import router as canvas_router
from api.errors import error_payload, install_error_handlers
from api.interview_routes import router as interview_router
from api.session_routes import router as session_router
from api.v2 import router as v2_router
from api.voice_routes import router as voice_router
from core.auth import auth_is_configured, smtp_is_configured, verify_session_token
from core.config import settings
from domain.errors import ErrorCode

# 统一日志格式：时间 / 级别 / 模块（B074）。
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时初始化数据库表（幂等；生产迁移由 Alembic 负责）。
    try:
        from repositories.database import initialize_database

        await asyncio.to_thread(initialize_database)
    except Exception:  # noqa: BLE001 - 数据库不可用不应阻止只读接口启动，但必须留痕
        logger.exception("数据库初始化失败")

    # 启动时确保 RAG 知识库索引存在且最新（B004）。
    try:
        from rag.index_builder import build_if_stale

        count = await asyncio.to_thread(build_if_stale)
        if count:
            logger.info("RAG 知识库索引构建完成：%d 个切片", count)
    except Exception:  # noqa: BLE001 - RAG 构建失败不影响服务启动
        logger.exception("RAG 知识库索引构建失败")

    yield

    from core.auth import _gc_verification_codes, _gc_send_times
    import time

    _gc_verification_codes()
    _gc_send_times(time.time())
    logger.info("服务已关闭，内存状态已清理")


app = FastAPI(title="产品脑暴工作台 API", version="0.1.0", lifespan=lifespan)

install_error_handlers(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=[
        "Content-Type",
        "Authorization",
        "X-Requested-With",
        "Idempotency-Key",
        "Last-Event-ID",
        "X-Request-ID",
    ],
)


@app.middleware("http")
async def require_authenticated_api(request: Request, call_next):
    # CORS 预检不带 cookie，直接放行交给 CORSMiddleware 处理（B003）。
    if request.method == "OPTIONS":
        return await call_next(request)

    # 供统一错误体使用的 request_id（可选传入）。
    raw_request_id = (
        request.headers.get("X-Request-ID") or request.headers.get("Idempotency-Key") or ""
    ).strip()
    if raw_request_id:
        request.state.request_id = raw_request_id

    path = request.url.path
    is_public = path == "/health" or path.startswith("/api/auth")
    if path.startswith("/api/") and not is_public:
        email = verify_session_token(request.cookies.get(settings.auth_cookie_name))
        if not email:
            payload = error_payload(
                ErrorCode.UNAUTHENTICATED,
                "请先使用邮箱登录",
                request_id=raw_request_id,
            )
            payload["detail"] = "请先使用邮箱登录"  # 旧前端兼容
            return JSONResponse(status_code=401, content=payload)
        request.state.user_email = email
    return await call_next(request)


# v2 端点（canonical 路径）
app.include_router(v2_router)
# 旧 v1 路由（适配器，禁止第二条写路径）
app.include_router(auth_router)
app.include_router(session_router)
app.include_router(brainstorm_router)
app.include_router(interview_router)
app.include_router(canvas_router)
app.include_router(voice_router)


def _service_status() -> dict:
    return {
        "llm": bool(settings.llm_api_key),
        "smtp": smtp_is_configured(),
        "auth": auth_is_configured(),
    }


@app.get("/health")
async def health():
    """公开健康检查：仅返回总体状态，不暴露配置细节（B070）。"""
    status = "ok" if all(_service_status().values()) else "degraded"
    return {"status": status}


@app.get("/health/detail")
async def health_detail(request: Request):
    """详细健康检查：需登录（走正常认证，B070）。"""
    email = verify_session_token(request.cookies.get(settings.auth_cookie_name))
    if not email:
        raise HTTPException(status_code=401, detail="请先使用邮箱登录")
    services = _service_status()
    return {"status": "ok" if all(services.values()) else "degraded", "services": services}