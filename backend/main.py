"""FastAPI 应用入口。

M1（v4 契约）变更：
* 所有新端点在 ``/api/v2`` 前缀下（``api.v2``），旧路由（/api/session、/api/brainstorm、
  /api/canvas、/api/interview）保留为**适配器**，共享同一套仓储与命令服务；
* 统一错误体由 ``api.errors.install_error_handlers`` 注册；
* 启动时初始化 SQLite 表（本地/测试快速路径），关闭时清理兼容垫片。
"""
import asyncio
import logging
import os
import time as _time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response
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
from core.log_context import (
    bind_request_id,
    install_request_id_filter,
    new_request_id,
    reset_request_id,
)
from domain.errors import ErrorCode
from sqlalchemy import func as _func, select as _select

# 统一日志格式：时间 / 级别 / 模块 / 请求号（B074；请求号见第 44 批）。
# `%(request_id)s` 由 core.log_context.RequestIdFilter 注入，未绑定请求时为 "-"，
# 这样用户报障时前端错误体里的 request_id 能直接在服务端日志里 grep 到。
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] [rid=%(request_id)s] %(message)s",
)
install_request_id_filter()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时初始化数据库表（幂等；生产迁移由 Alembic 负责）。
    try:
        from repositories.database import initialize_database

        await asyncio.to_thread(initialize_database)
    except Exception:  # noqa: BLE001 - 数据库不可用不应阻止只读接口启动，但必须留痕
        logger.exception("数据库初始化失败")

    # 快照持久化（第 46 批）：HF 免费实例没有持久卷，靠私有 dataset 仓库兜底。
    # 未配置 DB_SNAPSHOT_REPO/TOKEN 时 start() 就是 no-op，本地开发完全不受影响。
    from core.db_snapshot import SnapshotUploader, snapshot_config

    uploader = SnapshotUploader(snapshot_config())
    if uploader.enabled:
        logger.info("event=db_snapshot.enabled repo=%s", uploader._config.get("repo"))  # noqa: SLF001
    uploader.start()

    # 启动时确保 RAG 知识库索引存在且最新（B004）。
    try:
        from rag.index_builder import build_if_stale

        count = await asyncio.to_thread(build_if_stale)
        if count:
            logger.info("RAG 知识库索引构建完成：%d 个切片", count)
    except Exception:  # noqa: BLE001 - RAG 构建失败不影响服务启动
        logger.exception("RAG 知识库索引构建失败")

    yield

    # 退出前先把库快照推上去（第 46 批），再清理内存状态。
    try:
        uploader.stop(flush=True)
    except Exception:  # noqa: BLE001 - 收尾失败不能影响关停
        logger.exception("event=db_snapshot.shutdown_failed")

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

    # 请求号：优先用调用方传入的，缺失则生成一个（第 44 批）。
    # 它同时进入 request.state（错误体用）、日志上下文（每条日志带 rid）与响应头，
    # 用户报障时把界面上的 request_id 给过来即可在服务端日志里定位整条链路。
    raw_request_id = (
        request.headers.get("X-Request-ID") or request.headers.get("Idempotency-Key") or ""
    ).strip()
    request_id = raw_request_id or new_request_id()
    request.state.request_id = request_id
    token = bind_request_id(request_id)
    try:
        path = request.url.path
        is_public = path == "/health" or path.startswith("/api/auth")
        if path.startswith("/api/") and not is_public:
            email = verify_session_token(request.cookies.get(settings.auth_cookie_name))
            if not email:
                payload = error_payload(
                    ErrorCode.UNAUTHENTICATED,
                    "请先使用邮箱登录",
                    request_id=request_id,
                )
                payload["detail"] = "请先使用邮箱登录"  # 旧前端兼容
                response: Response = JSONResponse(status_code=401, content=payload)
            else:
                request.state.user_email = email
                response = await call_next(request)
        else:
            response = await call_next(request)
    finally:
        reset_request_id(token)

    # 回显请求号，方便浏览器 Network 面板与界面文案对齐（不覆盖上游已设置的值）。
    response.headers.setdefault("X-Request-ID", request_id)
    return response


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

#: 短窗口样本不足时回落到 7 天（仍在「近期」范围内，不会把几个月前的报告混进来）
REPORT_QUALITY_FALLBACK_DAYS = 7.0



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


def _report_quality(
    days: float = REPORT_QUALITY_WINDOW_SECONDS / 86400,
    fallback_days: float = REPORT_QUALITY_FALLBACK_DAYS,
) -> dict:
    """只读聚合窗口内的报告数字标注情况；样本不足时回落到更长的窗口（第 31 批）。

    为什么需要回落：仓库刚部署或安静一天时，24 小时窗口里可能一份报告都没有，
    接口永远只能答 ``no_data``——排障时「没有信息」和「一切正常」看起来一样糟。
    这里先用短窗口；若判定为 ``no_data``/``insufficient``，再查一次长窗口，
    并在返回里标明 ``window`` 与 ``fallback_from``，让读者知道数据来自哪个区间。
    """

    from repositories import audit_repo
    from repositories.database import new_session

    short_window = f"{int(float(days))}d"
    long_window = f"{int(float(fallback_days))}d"
    db = new_session()
    try:
        stats = audit_repo.report_quality_stats(db, since_iso=_iso_seconds_ago(float(days) * 86400))
        status = _report_quality_status(stats)
        payload = {"db": True, "window": short_window, **stats, **status}
        if status["verdict"] not in ("no_data", "insufficient"):
            return payload
        if float(fallback_days) <= float(days):
            return payload

        wide = audit_repo.report_quality_stats(
            db, since_iso=_iso_seconds_ago(float(fallback_days) * 86400)
        )
        if int(wide.get("scanned") or 0) <= int(stats.get("scanned") or 0):
            return payload
        wide_status = _report_quality_status(wide)
        return {
            "db": True,
            "window": long_window,
            "active_window": short_window,
            "fallback_from": short_window,
            **wide,
            **wide_status,
            "hint": f"{wide_status['hint']}（{short_window} 内样本不足，已回落到 {long_window}）",
        }
    except Exception:  # pragma: no cover - 只在库异常时走到
        logger.exception("event=health.report_quality_failed")
        return {"db": False, "window": short_window, "verdict": "unknown", "hint": None}
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


def _ops_storage() -> dict:
    """存储用量（第 38 批 /ops 页）：库文件大小 + 主要表的行数。

    只做只读统计：行数是 ``COUNT(*)``（本地 SQLite 足够快），
    库大小取 ``PRAGMA page_count * page_size``，不读文件系统元数据
    （WAL 模式下文件大小会滞后于逻辑页数）。
    """

    from repositories import database as _database

    try:
        engine = _database.get_engine()
        with engine.connect() as conn:
            page_count = conn.exec_driver_sql("PRAGMA page_count").scalar() or 0
            page_size = conn.exec_driver_sql("PRAGMA page_size").scalar() or 0
        counts: dict[str, int] = {}
        with _database.session_scope() as db:
            for name, model in (
                ("sessions", _database.SessionRow),
                ("messages", _database.MessageRow),
                ("runs", _database.RunRow),
                ("steps", _database.StepRow),
                ("events", _database.EventRow),
                ("evidence", _database.EvidenceRow),
                ("loops", _database.LoopRow),
                ("loop_artifacts", _database.LoopArtifactRow),
            ):
                counts[name] = int(
                    db.execute(_select(_func.count()).select_from(model)).scalar_one()
                )
        return {
            "db": True,
            "db_bytes": int(page_count) * int(page_size),
            "counts": counts,
        }
    except Exception:  # pragma: no cover - 诊断接口不应让健康检查崩溃
        logger.exception("event=health.storage_failed")
        return {"db": False, "db_bytes": 0, "counts": {}}


def _ops_queues() -> dict:
    """队列深度：排队中 / 运行中的 Run 数与最久排队时长。"""

    from repositories import database as _database

    try:
        now = _time.time()
        oldest_iso = None
        with _database.session_scope() as db:
            queued = int(
                db.execute(
                    _select(_func.count())
                    .select_from(_database.RunRow)
                    .where(_database.RunRow.status == "queued")
                ).scalar_one()
            )
            running = int(
                db.execute(
                    _select(_func.count())
                    .select_from(_database.RunRow)
                    .where(_database.RunRow.status == "running")
                ).scalar_one()
            )
            oldest_iso = db.execute(
                _select(_func.min(_database.RunRow.created_at)).where(
                    _database.RunRow.status == "queued"
                )
            ).scalar()
        oldest_epoch = _parse_iso_seconds(oldest_iso)
        oldest_age = None if oldest_epoch is None else round(max(0.0, now - oldest_epoch), 1)
        return {
            "queued": queued,
            "running": running,
            "oldest_queued_age_seconds": oldest_age,
        }
    except Exception:  # pragma: no cover
        logger.exception("event=health.queues_failed")
        return {"queued": 0, "running": 0, "oldest_queued_age_seconds": None}


def _ops_sandbox() -> dict:
    """沙箱能力开关：/ops 页要能看出「为什么执行按钮是灰的」。"""

    try:
        from services import loop_sandbox

        return loop_sandbox.sandbox_status().model_dump(mode="json")
    except Exception:  # pragma: no cover
        logger.exception("event=health.sandbox_failed")
        return {"enabled": False, "allowed_commands": [], "timeout_seconds": 0, "max_output_bytes": 0, "note": ""}


def _database_probe() -> tuple[bool, str]:
    """真正打一次数据库（SELECT 1），返回 (是否可用, 失败原因)。

    旧 /health 只看「配置是否存在」，数据库挂了也照样 200，运维据此会误判为健康
    （第 42 批评测记录 P1-6）。
    """

    try:
        from repositories import database as database_module

        with database_module.session_scope() as db:
            db.execute(_select(1))
        return True, ""
    except Exception as exc:  # noqa: BLE001 - 健康检查不能因为探测异常而 500
        logger.warning("event=health.db_probe_failed error=%s", exc)
        return False, f"数据库不可用：{type(exc).__name__}"


def _persistence_warnings() -> list[str]:
    """数据持久化告警（不阻塞服务，但必须说出来）。

    线上评测记录 P0-2：HF 免费实例没有持久卷，SQLite 落在容器内 ``/app/data``，
    平台回收/重启后用户、会话、审计、模型密钥全部清零且不可恢复。

    第 46 批起有第三条路：把 SQLite 快照周期推到私有 HF dataset（``DB_SNAPSHOT_REPO``），
    重启时先恢复再建表。这条路**不是强一致**——最坏会丢掉一个快照周期内的写入，
    所以仍然要在 ``warnings`` 里说清楚丢多少，只是不再是「全丢」。
    """

    warnings: list[str] = []
    try:
        # 数据库地址由环境变量决定（见 repositories/database.py::default_database_url），
        # 不放在 Settings 里，所以这里直接读环境变量。
        database_url = (os.environ.get("DATABASE_URL") or "").strip()
        on_space = bool(os.environ.get("SPACE_ID") or os.environ.get("SPACE_HOST"))
        if not database_url and on_space:
            from core.db_snapshot import snapshot_config

            config = snapshot_config()
            if config["enabled"]:
                interval = int(config["interval_seconds"])  # type: ignore[arg-type]
                warnings.append(
                    f"运行在 Hugging Face Space 且未设置 DATABASE_URL：数据靠每 {interval} 秒的"
                    f" SQLite 快照恢复到私有 dataset（{config['repo']}）。快照不是强一致，"
                    f"最坏会丢失最近 {interval} 秒内的写入"
                )
            else:
                warnings.append(
                    "运行在 Hugging Face Space 且未设置 DATABASE_URL：SQLite 位于容器内，"
                    "实例重启或休眠回收后数据会丢失（请挂持久卷或改用外部数据库）"
                )
    except Exception as exc:  # noqa: BLE001 - 诊断信息失败不能影响健康接口
        logger.warning("event=health.persistence_probe_failed error=%s", exc)
    return warnings


def _readiness() -> dict:
    """就绪判定：数据库 + 关键配置。返回 {"status", "blocking_reasons", "warnings"}。"""

    reasons: list[str] = []
    db_ok, db_reason = _database_probe()
    if not db_ok:
        reasons.append(db_reason)
    services = _service_status()
    for name, label in (("auth", "登录密钥未配置（AUTH_SECRET_KEY 至少 32 字符）"), ("smtp", "邮件通道未配置")):
        if not services.get(name):
            reasons.append(label)
    warnings = _persistence_warnings()
    return {
        "status": "ok" if not reasons else "degraded",
        "blocking_reasons": reasons,
        "warnings": warnings,
        # 「能跑，但有已知降级」：与 blocking_reasons 区分开，避免把「数据会丢」
        # 说成「完全健康」，监控可以直接按 persistent=false 设阈值（第 45 批）。
        "degraded_reasons": warnings,
        "persistent": not warnings,
        # 数据落在哪儿（第 46 批）：外部数据库 / 周期快照 / 容器内临时盘，一眼可辨。
        "persistence": _persistence_mode(),
    }


def _persistence_mode() -> dict:
    """描述当前持久化方式，供监控与 ``/ops`` 页直接判定（第 46 批）。"""

    mode: dict = {"kind": "ephemeral", "durable": False}
    try:
        database_url = (os.environ.get("DATABASE_URL") or "").strip()
        on_space = bool(os.environ.get("SPACE_ID") or os.environ.get("SPACE_HOST"))
        if database_url and not database_url.startswith("sqlite"):
            mode = {"kind": "external", "durable": True}
        elif database_url and not on_space:
            mode = {"kind": "local-file", "durable": True}
        else:
            from core.db_snapshot import snapshot_config

            config = snapshot_config()
            if config["enabled"]:
                mode = {
                    "kind": "snapshot",
                    "durable": True,
                    "repo": config["repo"],
                    "interval_seconds": config["interval_seconds"],
                    "worst_case_loss_seconds": config["interval_seconds"],
                }
    except Exception as exc:  # noqa: BLE001 - 诊断失败不能影响健康接口
        logger.warning("event=health.persistence_mode_failed error=%s", exc)
    return mode


@app.get("/health")
async def health():
    """公开健康检查：仅返回总体状态，不暴露配置细节（B070）。

    保留原语义（进程活着就 200），供老的 keep-warm / 反代探测继续使用；
    需要「能不能干活」请用 ``/health/ready``。
    """

    status = "ok" if all(_service_status().values()) else "degraded"
    warnings = _persistence_warnings()
    payload: dict = {"status": status}
    if warnings:
        payload["warnings"] = warnings
    return payload


@app.get("/health/live")
async def health_live():
    """存活探针：只要进程能回这个响应就是活的（不查数据库、不查依赖）。"""

    return {"status": "ok"}


@app.get("/health/ready")
async def health_ready(response: Response):
    """就绪探针：数据库可用 + 关键配置齐全，否则 503 + blocking_reasons。

    给外部监控 / 容器编排用，避免「进程活着但库连不上」被当成健康。
    """

    verdict = _readiness()
    if verdict["status"] != "ok":
        response.status_code = 503
    return verdict


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
        # /ops 运维页（第 38 批）：存储用量、队列深度、沙箱开关。
        "storage": _ops_storage(),
        "queues": _ops_queues(),
        "sandbox": _ops_sandbox(),
    }