import os
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from api.session_routes import router as session_router
from api.brainstorm_routes import router as brainstorm_router
from api.interview_routes import router as interview_router
from api.canvas_routes import router as canvas_router
from api.voice_routes import router as voice_router
from api.user_routes import router as user_router
from api.recharge_routes import router as recharge_router
from api.auth_routes import router as auth_router
from api.product_routes import router as product_router
from api.attachment_routes import router as attachment_router
from core.config import settings

RATE_LIMIT_MAX = 20
RATE_LIMIT_WINDOW = 60
_rate_requests: dict[str, list[float]] = defaultdict(list)


def _get_client_id(request: Request) -> str:
    return (
        request.headers.get("x-user-token")
        or request.headers.get("authorization", "").replace("Bearer ", "")
        or request.client.host
        if request.client
        else "unknown"
    )


def _cleanup_timestamps(client_id: str, now: float) -> None:
    timestamps = _rate_requests[client_id]
    cutoff = now - RATE_LIMIT_WINDOW
    while timestamps and timestamps[0] < cutoff:
        timestamps.pop(0)


async def rate_limit_middleware(request: Request, call_next):
    client_id = _get_client_id(request)
    now = time.time()
    _cleanup_timestamps(client_id, now)
    if len(_rate_requests[client_id]) >= RATE_LIMIT_MAX:
        return JSONResponse(
            status_code=429,
            content={"detail": "请求过于频繁，请稍后再试"},
        )
    _rate_requests[client_id].append(now)
    response = await call_next(request)
    return response

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def _has_static_files() -> bool:
    if not os.path.isdir(STATIC_DIR):
        return False
    return bool(os.listdir(STATIC_DIR))


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.llm_api_key:
        print("WARNING: LLM_API_KEY 未设置，仅支持 BYOK 模式（用户自带 API Key）")
    if _has_static_files():
        print(f"Serving frontend from {STATIC_DIR}")
    else:
        print("No frontend static files found, running in API-only mode")
    yield


app = FastAPI(title="产品脑暴工作台 API", version="0.1.0", lifespan=lifespan)

app.middleware("http")(rate_limit_middleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(session_router)
app.include_router(brainstorm_router)
app.include_router(interview_router)
app.include_router(canvas_router)
app.include_router(voice_router)
app.include_router(user_router)
app.include_router(recharge_router)
app.include_router(auth_router)
app.include_router(product_router)
app.include_router(attachment_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api-info")
async def api_info():
    return {
        "name": "PM Brainstorm Workbench API",
        "version": "0.1.0",
        "status": "running",
        "endpoints": {
            "health": "/health",
            "sessions": "/api/sessions",
            "brainstorm": "/api/brainstorm",
            "interview": "/api/interview",
            "canvas": "/api/canvas",
            "voice": "/api/voice",
        },
    }


if _has_static_files():
    _next_dir = os.path.join(STATIC_DIR, "_next")
    _avatars_dir = os.path.join(STATIC_DIR, "avatars")
    if os.path.isdir(_next_dir):
        app.mount("/_next", StaticFiles(directory=_next_dir), name="next-static")
    if os.path.isdir(_avatars_dir):
        app.mount("/avatars", StaticFiles(directory=_avatars_dir), name="avatars")

    @app.get("/qrcode.jpg")
    async def qrcode():
        path = os.path.join(STATIC_DIR, "qrcode.jpg")
        if os.path.exists(path):
            return FileResponse(path)
        return {"error": "not found"}

    SESSION_HTML = os.path.join(STATIC_DIR, "session", "__placeholder__", "index.html")
    INTERVIEW_HTML = os.path.join(STATIC_DIR, "session", "__placeholder__", "interview", "index.html")
    INDEX_HTML = os.path.join(STATIC_DIR, "index.html")

    @app.get("/{path:path}")
    async def serve_spa(request: Request, path: str):
        if path and os.path.isfile(os.path.join(STATIC_DIR, path)):
            return FileResponse(os.path.join(STATIC_DIR, path))

        parts = path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "session":
            if len(parts) >= 4 and parts[2] == "interview":
                if os.path.exists(INTERVIEW_HTML):
                    return FileResponse(INTERVIEW_HTML)
            else:
                if os.path.exists(SESSION_HTML):
                    return FileResponse(SESSION_HTML)

        if os.path.exists(INDEX_HTML):
            return FileResponse(INDEX_HTML)
        return {"error": "Frontend not built. Set STATIC_DIR correctly."}
