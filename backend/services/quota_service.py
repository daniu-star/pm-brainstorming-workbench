"""账户级配额：限制同一 owner 在滚动窗口内的累计模型消耗。

与 :class:`workflows.runner.BudgetTracker`（单 Run 预算）互补：

- 单 Run 预算防「一次任务烧太多」（输入/输出 token、上游调用次数、超时）；
- 账户配额防「一个账号反复烧」——例如脚本循环提交 brainstorm、或多人共用同一账号。

计量口径（保守，宁可早拒也不放行）：

- 已终结 Run：用 ``usage_json`` 的实际值（含崩溃遗留的 ``reserved_*`` 预留，AC-10）；
- 未终结 Run（queued/running/interrupted/cancel_requested）：按 ``budget_json`` 的上限
  计入「预占」，所以并发提交一串任务不会集体越过配额。

配额为 0（默认）时不启用，``snapshot()["enabled"] is False``；只收紧不放宽。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.config import settings
from domain import contracts as C
from domain.errors import AppError, ErrorCode
from repositories.database import RunRow, SessionRow

logger = logging.getLogger(__name__)

#: 不消耗模型额度的命令：配额耗尽时仍允许改稿、登记证据、确认决策、取消、收尾报告。
EXEMPT_ACTION_VALUES = frozenset(
    {
        C.Action.revise_brief.value,
        C.Action.confirm_brief.value,
        C.Action.skip_clarification.value,
        C.Action.add_evidence.value,
        C.Action.confirm_decision.value,
        C.Action.correct_audit_assessment.value,
        C.Action.cancel_audit.value,
        # 结束审计会生成一份报告（约 3-8K token），但堵住它会让审计永久停在 closing。
        C.Action.end_audit.value,
    }
)

_TERMINAL_VALUES = frozenset(status.value for status in C.TERMINAL_RUN_STATUSES)


def limits() -> dict[str, int]:
    """当前配额上限（0 表示不限）。"""
    tokens = int(getattr(settings, "account_daily_token_limit", 0) or 0)
    calls = int(getattr(settings, "account_daily_call_limit", 0) or 0)
    return {"tokens": max(0, tokens), "calls": max(0, calls)}


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _usage_numbers(parsed: dict[str, Any]) -> tuple[int, int]:
    tokens = (
        int(parsed.get("prompt_tokens") or 0)
        + int(parsed.get("completion_tokens") or 0)
        + int(parsed.get("reserved_prompt_tokens") or 0)
        + int(parsed.get("reserved_completion_tokens") or 0)
    )
    return tokens, int(parsed.get("upstream_calls") or 0)


def _budget_numbers(parsed: dict[str, Any]) -> tuple[int, int]:
    tokens = int(parsed.get("max_run_input_tokens") or 0) + int(
        parsed.get("max_run_output_tokens") or 0
    )
    return tokens, int(parsed.get("max_upstream_calls") or 0)


def usage_since(
    db: Session, *, owner_email: str, since_iso: str, limit: int = 2000
) -> dict[str, int]:
    """统计 owner 自 ``since_iso`` 起的模型消耗（实际 + 在途预占）。"""
    rows = db.execute(
        select(RunRow.usage_json, RunRow.budget_json, RunRow.status)
        .join(SessionRow, SessionRow.id == RunRow.session_id)
        .where(SessionRow.owner_email == owner_email, RunRow.created_at >= since_iso)
        .order_by(RunRow.created_at.desc())
        .limit(max(1, int(limit)))
    ).all()
    used_tokens = used_calls = reserved_tokens = reserved_calls = 0
    for usage_json, budget_json, status in rows:
        tokens, calls = _usage_numbers(_parse(usage_json))
        used_tokens += tokens
        used_calls += calls
        if str(status) in _TERMINAL_VALUES:
            continue
        tokens, calls = _budget_numbers(_parse(budget_json))
        reserved_tokens += tokens
        reserved_calls += calls
    return {
        "used_tokens": used_tokens,
        "used_calls": used_calls,
        "reserved_tokens": reserved_tokens,
        "reserved_calls": reserved_calls,
        "scanned_runs": len(rows),
    }


def snapshot(db: Session, *, owner_email: str, now_epoch: int) -> dict[str, Any]:
    """当前配额视图（可直接返回给前端 / `/api/v2/quota`）。"""
    cap = limits()
    window = int(C.ACCOUNT_QUOTA_WINDOW_SECONDS)
    now_epoch = int(now_epoch)
    stats = usage_since(
        db, owner_email=owner_email, since_iso=_iso(now_epoch - window)
    )
    total_tokens = stats["used_tokens"] + stats["reserved_tokens"]
    total_calls = stats["used_calls"] + stats["reserved_calls"]
    over_tokens = cap["tokens"] > 0 and total_tokens >= cap["tokens"]
    over_calls = cap["calls"] > 0 and total_calls >= cap["calls"]
    return {
        "enabled": cap["tokens"] > 0 or cap["calls"] > 0,
        "window_seconds": window,
        "window_hours": window // 3600,
        "reset_at": _iso(now_epoch + window),
        "token_limit": cap["tokens"],
        "call_limit": cap["calls"],
        "used_tokens": stats["used_tokens"],
        "reserved_tokens": stats["reserved_tokens"],
        "total_tokens": total_tokens,
        "used_calls": stats["used_calls"],
        "reserved_calls": stats["reserved_calls"],
        "total_calls": total_calls,
        "scanned_runs": stats["scanned_runs"],
        "exceeded": bool(over_tokens or over_calls),
    }


def ensure_within_quota(
    db: Session, *, owner_email: str, action: str, now_epoch: int
) -> dict[str, Any]:
    """超出配额时抛 ``ACCOUNT_QUOTA_EXCEEDED``（429），否则返回当前视图。

    ``EXEMPT_ACTION_VALUES`` 里的同步命令不受限：不然配额耗尽后用户连改稿、
    登记证据、取消运行都做不了，反而更容易堆积。
    """
    snap = snapshot(db, owner_email=owner_email, now_epoch=now_epoch)
    if not snap["enabled"] or not snap["exceeded"]:
        return snap
    if str(action) in EXEMPT_ACTION_VALUES:
        return snap
    token_text = f"{snap['total_tokens']}/{snap['token_limit']}"
    call_text = f"{snap['total_calls']}/{snap['call_limit'] or '不限'}"
    logger.warning(
        "event=quota.exceeded owner=%s action=%s tokens=%s calls=%s",
        owner_email,
        action,
        token_text,
        call_text,
    )
    raise AppError(
        ErrorCode.ACCOUNT_QUOTA_EXCEEDED,
        (
            f"账户在最近 {snap['window_hours']} 小时内已用尽模型额度"
            f"（token {token_text}，调用 {call_text}）。"
            f"额度会随时间滚动恢复（最早 {snap['reset_at']} UTC 之后逐步可用）。"
            "此前可以先做改稿、登记证据、确认决策等不消耗模型的操作；"
            "如需更高额度，请调整该账户的配额上限。"
        ),
        details=snap,
    )
