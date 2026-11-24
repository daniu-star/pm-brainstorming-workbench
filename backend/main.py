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


def C_budget_defaults() -> dict[str, int]:
    """当前预算档位实际生效的上限（供前端/运维核对成本口径）。"""

    from services.command_service import default_budget

    limits = default_budget()
    return {k: int(v) for k, v in limits.items() if isinstance(v, (int, float))}


def _service_status() -> dict:
    return {
        "llm": bool(settings.llm_api_key),
        "smtp": smtp_is_configured(),
        "auth": auth_is_configured(),
    }


WORKER_STALL_SECONDS = 120.0

#: 报告质量统计的回看窗口（`/health/detail` 的 report_quality 块，第 30 批）
REPORT_QUALITY_WINDOW_SECONDS = 24 * 3600



def _parse_iso_seconds(value: str | None) -> float | None:
    """把 ``runs.heartbeat_at``（``%Y-%m-%dT%H:%M:%SZ``）转成 epoch 秒；解析失败返回 None。"""

    if not value:
        return None
    from datetime import datetime

    try:
        return float(datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").timestamp())
    except (TypeError, ValueError):
        return None


def _iso_seconds_ago(seconds: float) -> str:
    """当前时间往前 ``seconds`` 秒，格式化为与库内一致的 ``%Y-%m-%dT%H:%M:%SZ``。"""

    from datetime import datetime, timedelta, timezone

    moment = datetime.now(timezone.utc) - timedelta(seconds=float(seconds))
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _report_quality_status(stats: dict) -> dict:
    """把仓储统计翻译成「模型是不是老在编数字」的一句话判断（第 30 批）。

    只做阈值判断，不臆测原因：报告样本太少时明确说样本不足，避免拿
    一两条报告下结论。
    """

    scanned = int(stats.get("scanned") or 0)
    annotated = int(stats.get("reports_with_unverified") or 0)
    numbers = int(stats.get("unverified_numbers") or 0)
    fallback = int(stats.get("fallback_reports") or 0)

    if scanned == 0:
        verdict, hint = "no_data", "窗口内没有新的审计报告，暂时无法判断"
    elif scanned < 3:
        verdict, hint = "insufficient", f"窗口内只有 {scanned} 份报告，样本不足以下结论"
    else:
        ratio = annotated / scanned
        if ratio >= 0.5:
            verdict = "frequent"
            hint = (
                f"{scanned} 份报告里 {annotated} 份含无来源数字（共 {numbers} 个）："
                "建议在提示词里要求写清数据来源与统计口径"
            )
        elif annotated > 0:
            verdict = "occasional"
            hint = f"{scanned} 份报告里 {annotated} 份含无来源数字（共 {numbers} 个）"
        else:
            verdict = "clean"
            hint = f"{scanned} 份报告的正文数字都能在消息/证据里找到"

    return {
        "verdict": verdict,
        "hint": hint,
        "reports_without_source": annotated,
        "fallback_reports": fallback,
    }


def _report_quality(days: float = REPORT_QUALITY_WINDOW_SECONDS / 86400) -> dict:
    """只读聚合最近窗口内的报告数字标注情况；异常时降级为 ``db`` 为假。"""

    from repositories import audit_repo
    from repositories.database import new_session

    since = _iso_seconds_ago(float(days) * 86400)
    db = new_session()
    try:
        stats = audit_repo.report_quality_stats(db, since_iso=since)
        return {"db": True, "window": f"{int(float(days))}d", **stats, **_report_quality_status(stats)}
    except Exception:  # pragma: no cover - 只在库异常时走到
        logger.exception("event=health.report_quality_failed")
        return {"db": False, "window": f"{int(float(days))}d", "verdict": "unknown", "hint": None}
    finally:
        db.close()


def _worker_stall_hint(
    *, queued_runs: int, running_runs: int, heartbeat_age_seconds: float | None
) -> str | None:
    """有排队任务但没有 Worker 在跑/心跳过旧时的可操作提示（否则 None）。

    历史故障：Worker 进程没起来时，命令只会一直排队到 QUEUE_TIMEOUT（10 分钟）才失败，
    用户看到的是「点了没反应」。这里把「排队 + 无心跳」直接判成 stalled，
    让 /health/detail 与监控能在几秒内定位，而不是等超时。
    """

    if queued_runs <= 0:
        return None
    if running_runs > 0 and heartbeat_age_seconds is not None:
        if heartbeat_age_seconds <= WORKER_STALL_SECONDS:
            return None
        reason = f"最近心跳在 {int(heartbeat_age_seconds)} 秒前（阈值 {int(WORKER_STALL_SECONDS)} 秒）"
    elif running_runs > 0:
        reason = "有任务在跑但读不到心跳时间"
    else:
        reason = "没有任何 Worker 领取任务"
    return (
        f"{queued_runs} 个任务在排队但没有 Worker 在工作：{reason}。"
        "请启动 Worker（start.bat，或在 backend 目录执行 python -m workers.run_worker）。"
    )


def _worker_status() -> dict:
    """Worker 实时状态（WP-10 可观测性）。

    只做只读聚合：未领取的排队数、正在执行的 run 数、最近一次心跳时间。
    心跳由 ``run_repo.claim_next_run``/``renew_lease`` 写入 ``runs.heartbeat_at``，
    因此它反映的是「真实有 Worker 在跑」而不只是进程存在。

    第 20 批：增加 ``stalled``/``hint``——「排队中但没有 Worker」是最常见的
    「发消息一直没反应」根因，必须能一眼看出来。
    """

    from sqlalchemy import func, select

    from repositories.database import new_session
    from repositories.database import RunRow

    import time as _time

    db = new_session()
    try:
        running = db.execute(
            select(func.count()).select_from(RunRow).where(RunRow.status == "running")
        ).scalar_one()
        queued = db.execute(
            select(func.count()).select_from(RunRow).where(RunRow.status == "queued")
        ).scalar_one()
        heartbeat = db.execute(
            select(func.max(RunRow.heartbeat_at)).where(RunRow.status == "running")
        ).scalar_one()
        heartbeat_epoch = _parse_iso_seconds(heartbeat)
        age = None if heartbeat_epoch is None else max(0.0, _time.time() - heartbeat_epoch)
        hint = _worker_stall_hint(
            queued_runs=int(queued), running_runs=int(running), heartbeat_age_seconds=age
        )
        return {
            "db": True,
            "running_runs": int(running),
            "queued_runs": int(queued),
            "last_heartbeat_at": heartbeat,
            "heartbeat_age_seconds": None if age is None else round(age, 1),
            "stalled": hint is not None,
            "hint": hint,
        }
    except Exception:  # pragma: no cover - 诊断接口不应让健康检查崩溃
        logger.exception("worker status unavailable")
        return {
            "db": False,
            "running_runs": 0,
            "queued_runs": 0,
            "last_heartbeat_at": None,
            "heartbeat_age_seconds": None,
            "stalled": False,
            "hint": None,
        }
    finally:
        db.close()


@app.get("/health")
async def health():
    """公开健康检查：仅返回总体状态，不暴露配置细节（B070）。"""
    status = "ok" if all(_service_status().values()) else "degraded"
    return {"status": status}


def _url_host(value: str) -> str:
    """只取主机名用于诊断（避免把完整 URL 的凭据/路径写进日志或响应）。"""

    try:
        from urllib.parse import urlparse

        return urlparse(value or "").hostname or ""
    except ValueError:
        return ""


@app.get("/health/detail")
async def health_detail(request: Request):
    """详细健康检查：需登录（走正常认证，B070）。"""
    email = verify_session_token(request.cookies.get(settings.auth_cookie_name))
    if not email:
        raise HTTPException(status_code=401, detail="请先使用邮箱登录")
    services = _service_status()
    worker = _worker_status()
    return {
        "status": "ok"
        if all(services.values()) and worker.get("db") and not worker.get("stalled")
        else "degraded",
        "services": {**services, "worker": bool(worker.get("db"))},
        "worker": worker,
        "budget_profile": (settings.llm_budget_profile or "standard"),
        "budget_limits": C_budget_defaults(),
        # 模型层诊断（第 18 批）：联调时最常问的两个问题——「用的哪个模型」和
        # 「为什么结构化输出是空的」。只暴露模型名/主机名/开关，不暴露 Key。
        "llm": {
            "model": settings.llm_model,
            "base_url_host": _url_host(settings.llm_base_url),
            "disable_reasoning": bool(settings.llm_disable_reasoning),
            "timeout_seconds": float(settings.llm_timeout_seconds),
            "max_retries": int(settings.llm_max_retries),
        },
        # 报告质量（第 30 批）：模型是否老在报告里写没有来源的数字。
        "report_quality": _report_quality(),
    }