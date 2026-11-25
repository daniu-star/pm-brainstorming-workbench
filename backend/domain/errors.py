"""统一错误模型（PRD 7.1）：error.code / message / retryable / request_id / details。

领域层只抛 AppError；API 层统一转换为 §7.1 的错误体。任何用户可见文案不得
包含堆栈、密钥或供应商原始报文。
"""
from __future__ import annotations

from typing import Any


class ErrorCode:
    """稳定错误码（前端按 code 分支，不解析 message）。"""

    # 认证与归属
    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"

    # 输入与命令
    INVALID_REQUEST = "INVALID_REQUEST"
    UNKNOWN_ACTION = "UNKNOWN_ACTION"
    INPUT_TOO_LONG = "INPUT_TOO_LONG"
    IDEMPOTENCY_KEY_MISMATCH = "IDEMPOTENCY_KEY_MISMATCH"
    IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"

    # 并发与版本
    VERSION_CONFLICT = "VERSION_CONFLICT"
    SESSION_BUSY = "SESSION_BUSY"
    RATE_LIMITED = "RATE_LIMITED"
    QUEUE_LIMIT_EXCEEDED = "QUEUE_LIMIT_EXCEEDED"
    CANVAS_IS_DERIVED = "CANVAS_IS_DERIVED"

    # 执行与预算
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    ACCOUNT_QUOTA_EXCEEDED = "ACCOUNT_QUOTA_EXCEEDED"
    RUN_DEADLINE_EXCEEDED = "RUN_DEADLINE_EXCEEDED"
    QUEUE_WAIT_EXCEEDED = "QUEUE_WAIT_EXCEEDED"
    RETRY_CONTEXT_CHANGED = "RETRY_CONTEXT_CHANGED"
    RETRY_AUDIT_CONTEXT_CHANGED = "RETRY_AUDIT_CONTEXT_CHANGED"
    CONTEXT_TOO_LARGE = "CONTEXT_TOO_LARGE"
    MODEL_OUTPUT_INVALID = "MODEL_OUTPUT_INVALID"
    LEASE_LOST = "LEASE_LOST"

    # 审计
    AUDIT_CLOSED = "AUDIT_CLOSED"
    AUDIT_ACTIVE = "AUDIT_ACTIVE"
    QUESTION_ALREADY_ANSWERED = "QUESTION_ALREADY_ANSWERED"
    QUESTION_NOT_ACTIVE = "QUESTION_NOT_ACTIVE"
    CONTROL_SUPERSEDED = "CONTROL_SUPERSEDED"

    # Loop Studio
    LOOP_ACTIVE = "LOOP_ACTIVE"
    LOOP_NOT_AWAITING_DECISION = "LOOP_NOT_AWAITING_DECISION"
    LOOP_ROUNDS_EXHAUSTED = "LOOP_ROUNDS_EXHAUSTED"
    LOOP_CLAIMED = "LOOP_CLAIMED"
    LOOP_VERSION_CONFLICT = "LOOP_VERSION_CONFLICT"

    # 事件
    EVENT_CURSOR_EXPIRED = "EVENT_CURSOR_EXPIRED"
    INVALID_EVENT_CURSOR = "INVALID_EVENT_CURSOR"

    # 能力开关
    FEATURE_NOT_READY = "FEATURE_NOT_READY"
    INTERNAL = "INTERNAL"


# 默认 HTTP 状态映射；需要时可在抛错处覆盖。
_DEFAULT_STATUS: dict[str, int] = {
    ErrorCode.UNAUTHENTICATED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.INVALID_REQUEST: 422,
    ErrorCode.UNKNOWN_ACTION: 422,
    ErrorCode.INPUT_TOO_LONG: 422,
    ErrorCode.IDEMPOTENCY_KEY_MISMATCH: 422,
    ErrorCode.IDEMPOTENCY_KEY_REUSED: 409,
    ErrorCode.VERSION_CONFLICT: 409,
    ErrorCode.SESSION_BUSY: 409,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.QUEUE_LIMIT_EXCEEDED: 429,
    ErrorCode.CANVAS_IS_DERIVED: 409,
    ErrorCode.BUDGET_EXCEEDED: 409,
    ErrorCode.ACCOUNT_QUOTA_EXCEEDED: 429,
    ErrorCode.RUN_DEADLINE_EXCEEDED: 409,
    ErrorCode.QUEUE_WAIT_EXCEEDED: 503,
    ErrorCode.RETRY_CONTEXT_CHANGED: 409,
    ErrorCode.RETRY_AUDIT_CONTEXT_CHANGED: 409,
    ErrorCode.CONTEXT_TOO_LARGE: 422,
    ErrorCode.MODEL_OUTPUT_INVALID: 502,
    ErrorCode.LEASE_LOST: 409,
    ErrorCode.AUDIT_CLOSED: 409,
    ErrorCode.AUDIT_ACTIVE: 409,
    ErrorCode.QUESTION_ALREADY_ANSWERED: 409,
    ErrorCode.QUESTION_NOT_ACTIVE: 409,
    ErrorCode.CONTROL_SUPERSEDED: 409,
    ErrorCode.LOOP_ACTIVE: 409,
    ErrorCode.LOOP_NOT_AWAITING_DECISION: 409,
    ErrorCode.LOOP_ROUNDS_EXHAUSTED: 409,
    ErrorCode.LOOP_CLAIMED: 409,
    ErrorCode.LOOP_VERSION_CONFLICT: 409,
    ErrorCode.EVENT_CURSOR_EXPIRED: 410,
    ErrorCode.INVALID_EVENT_CURSOR: 422,
    ErrorCode.FEATURE_NOT_READY: 503,
    ErrorCode.INTERNAL: 500,
}


class AppError(Exception):
    """领域错误：API 层唯一需要转换的异常类型。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int | None = None,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status or _DEFAULT_STATUS.get(code, 400)
        self.retryable = retryable
        self.details = details or {}
        self.request_id = request_id

    def to_body(self, request_id: str | None = None) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "retryable": self.retryable,
                "request_id": request_id or self.request_id or "",
                "details": self.details,
            }
        }

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return f"AppError(code={self.code!r}, message={self.message!r})"


def conflict(message: str, **details: Any) -> AppError:
    return AppError(ErrorCode.VERSION_CONFLICT, message, details=details)


def not_found(message: str = "资源不存在", **details: Any) -> AppError:
    return AppError(ErrorCode.NOT_FOUND, message, details=details)


def invalid(message: str, **details: Any) -> AppError:
    return AppError(ErrorCode.INVALID_REQUEST, message, details=details)
