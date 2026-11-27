"""请求级日志上下文（第 44 批）。

问题：一次故障排查要在几十条日志里靠时间戳拼出一条请求的轨迹，且前端拿到的
``request_id`` 在服务端日志里查不到 —— 用户报「点配置就失败」时无法定位。

做法：用 :mod:`contextvars` 保存当前请求的 ``request_id``，由
:class:`RequestIdFilter` 注入每条日志记录，日志格式里使用 ``%(request_id)s``。
未绑定（启动期、Worker、单元测试直接调用）时回落 ``"-"``，不会抛错。
"""
from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar, Token

#: 当前请求的 request_id；未绑定为 None。
_current_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

#: 日志里 request_id 缺失时的占位符（保证格式串永远有值）。
MISSING_REQUEST_ID = "-"


def new_request_id(prefix: str = "req") -> str:
    """生成请求级 id，例如 ``req_9f2c1a4b7d3e``（13 位，便于肉眼扫日志）。"""

    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def current_request_id() -> str | None:
    """返回当前请求的 request_id（可能为 None）。"""

    return _current_request_id.get()


def bind_request_id(request_id: str) -> Token:
    """把 request_id 绑到当前上下文，返回可用于 :func:`reset_request_id` 的 token。"""

    return _current_request_id.set(request_id)


def reset_request_id(token: Token) -> None:
    """恢复绑定前的上下文（必须在 ``finally`` 里调用，避免泄漏到下一个请求）。"""

    _current_request_id.reset(token)


class RequestIdFilter(logging.Filter):
    """给每条日志记录补上 ``request_id`` 字段。"""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D102 - Filter 接口
        record.request_id = _current_request_id.get() or MISSING_REQUEST_ID
        return True


def install_request_id_filter(logger: logging.Logger | None = None) -> None:
    """把过滤器挂到根 logger 的每个 handler 上（幂等，重复调用不会重复挂）。"""

    root = logger or logging.getLogger()
    for handler in root.handlers:
        if not any(isinstance(f, RequestIdFilter) for f in handler.filters):
            handler.addFilter(RequestIdFilter())


__all__ = [
    "MISSING_REQUEST_ID",
    "RequestIdFilter",
    "bind_request_id",
    "current_request_id",
    "install_request_id_filter",
    "new_request_id",
    "reset_request_id",
]
