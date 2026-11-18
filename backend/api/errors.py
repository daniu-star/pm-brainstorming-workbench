"""统一错误处理（PRD §7.1 / docs/v4-m1-interfaces.md §5.1）。

错误体固定为::

    {"error": {"code": "...", "message": "...", "retryable": false,
               "request_id": "...", "details": {...}}}

* 领域层抛 :class:`domain.errors.AppError` → HTTP 状态取 ``exc.http_status``；
* 请求体校验失败 → 422 ``INVALID_REQUEST``（details 带字段错误，不回显原始输入）；
* 未捕获异常 → 500 ``INTERNAL_ERROR``，只记录日志，不把堆栈回显给客户端。
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from domain.errors import AppError, ErrorCode

logger = logging.getLogger(__name__)

#: 测试与排查用：最近一次 500 的异常类型（不对外暴露）。
LAST_INTERNAL_ERROR: dict[str, str] = {}


def request_id_of(request: Request) -> str:
    """请求级 request_id：中间件写入 → X-Request-ID / Idempotency-Key 头 → 空串。"""

    value = getattr(request.state, "request_id", None)
    if value:
        return str(value)
    for header in ("X-Request-ID", "Idempotency-Key"):
        raw = (request.headers.get(header) or "").strip()
        if raw:
            return raw
    return ""


def error_payload(
    code: str,
    message: str,
    *,
    retryable: bool = False,
    request_id: str = "",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "retryable": bool(retryable),
            "request_id": request_id,
            "details": details or {},
        }
    }


def error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    retryable: bool = False,
    request_id: str = "",
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=error_payload(
            code,
            message,
            retryable=retryable,
            request_id=request_id,
            details=details,
        ),
    )


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    request_id = exc.request_id or request_id_of(request)
    if exc.http_status >= 500:
        logger.error(
            "领域错误 %s（%s %s）：%s", exc.code, request.method, request.url.path, exc.message
        )
    else:
        logger.info(
            "请求被拒绝 %s（%s %s）：%s", exc.code, request.method, request.url.path, exc.message
        )
    return JSONResponse(
        status_code=exc.http_status,
        content=exc.to_body(request_id=request_id),
    )


def _validation_details(exc: RequestValidationError) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for error in exc.errors():
        location = [str(part) for part in error.get("loc", ()) if part not in ("body", "query", "path")]
        fields.append(
            {
                "field": ".".join(location) or "body",
                "type": str(error.get("type", "invalid")),
                "message": str(error.get("msg", "参数不合法")),
            }
        )
    return fields


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return error_response(
        422,
        ErrorCode.INVALID_REQUEST,
        "请求参数不合法",
        request_id=request_id_of(request),
        details={"fields": _validation_details(exc)},
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """保留旧前端的 ``detail`` 字段，同时补上统一错误体（双写，向后兼容）。"""

    if isinstance(exc.detail, dict) and "error" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)

    code_by_status = {
        401: ErrorCode.UNAUTHENTICATED,
        403: ErrorCode.FORBIDDEN,
        404: ErrorCode.NOT_FOUND,
        405: ErrorCode.INVALID_REQUEST,
        409: ErrorCode.VERSION_CONFLICT,
        422: ErrorCode.INVALID_REQUEST,
        429: ErrorCode.RATE_LIMITED,
        503: ErrorCode.FEATURE_NOT_READY,
    }
    code = code_by_status.get(exc.status_code, ErrorCode.INTERNAL)
    if isinstance(exc.detail, dict):
        message = str(exc.detail.get("message") or exc.detail.get("detail") or "请求失败")
    else:
        message = exc.detail if isinstance(exc.detail, str) else "请求失败"
    payload = error_payload(
        code, message, retryable=exc.status_code >= 500, request_id=request_id_of(request)
    )
    payload["detail"] = exc.detail
    return JSONResponse(status_code=exc.status_code, content=payload)


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    LAST_INTERNAL_ERROR["type"] = type(exc).__name__
    LAST_INTERNAL_ERROR["path"] = f"{request.method} {request.url.path}"
    logger.exception("未捕获异常：%s %s", request.method, request.url.path)
    return error_response(
        500,
        ErrorCode.INTERNAL,
        "服务器内部错误",
        retryable=True,
        request_id=request_id_of(request),
    )


def install_error_handlers(app: FastAPI) -> None:
    """注册全局异常处理器（幂等，可重复调用）。"""

    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)


__all__ = [
    "LAST_INTERNAL_ERROR",
    "error_payload",
    "error_response",
    "install_error_handlers",
    "request_id_of",
]