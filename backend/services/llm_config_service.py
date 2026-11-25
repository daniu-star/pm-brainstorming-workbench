"""账户自带模型（BYOK）配置服务。

产品约定：平台前期免费，但**模型调用由用户自己买单**——登录后必须先填写自己的
``base_url`` / ``model`` / ``api_key``，否则不发任何模型请求（前端会强制弹窗，
后端在「建会话 / 提交需要模型的命令 / Worker 执行 Run」三处兜底拦截）。

为什么密钥要进库而不是只读环境变量：
- 每个用户用自己的 Key，平台不承担模型费用，也不需要共享一把 Key；
- 密钥加密后落库（``core.crypto``），接口只回显末 4 位。
"""
from __future__ import annotations

import time
from typing import Any

from sqlalchemy.orm import Session

from core.config import settings
from core.crypto import SecretDecodeError, decrypt_secret, encrypt_secret, mask_secret
from domain import contracts as C
from domain.errors import AppError, ErrorCode
from repositories import llm_config_repo

__all__ = [
    "KEY_MODE_BYOK",
    "KEY_MODE_SYSTEM",
    "SYNC_ACTIONS",
    "build_client",
    "client_kwargs",
    "gate",
    "is_configured",
    "key_mode",
    "remove",
    "require_configured",
    "save",
    "test_connection",
    "view",
]

#: ``LLM_KEY_MODE=byok``（默认）：必须用户自带 Key；``system``：用 .env 里的共享 Key。
KEY_MODE_BYOK = "byok"
KEY_MODE_SYSTEM = "system"

#: 不消耗模型的命令：改稿、跳过澄清、登记证据、确认决策、更正评估、取消审计。
SYNC_ACTIONS: frozenset[str] = frozenset(
    {
        C.Action.confirm_brief.value,
        C.Action.skip_clarification.value,
        C.Action.revise_brief.value,
        C.Action.add_evidence.value,
        C.Action.confirm_decision.value,
        C.Action.correct_audit_assessment.value,
        C.Action.cancel_audit.value,
    }
)

_CONFIG_HINT = (
    "登录后在「模型 API 设置」里填写你自己的 base_url、model 与 api_key（平台不自带模型额度）。"
)


def key_mode() -> str:
    mode = str(getattr(settings, "llm_key_mode", KEY_MODE_BYOK) or KEY_MODE_BYOK).strip().lower()
    return KEY_MODE_SYSTEM if mode == KEY_MODE_SYSTEM else KEY_MODE_BYOK


def _row(db: Session, owner_email: str) -> Any | None:
    return llm_config_repo.get_config(db, owner_email)


def is_configured(db: Session, owner_email: str) -> bool:
    if key_mode() == KEY_MODE_SYSTEM:
        return bool(str(getattr(settings, "llm_api_key", "") or "").strip())
    return _row(db, owner_email) is not None


def client_kwargs(db: Session, owner_email: str) -> dict[str, Any]:
    """构造 :class:`core.llm_client.LLMClient` 的关键字参数（未配置返回空字典）。"""

    if key_mode() == KEY_MODE_SYSTEM:
        return {}
    row = _row(db, owner_email)
    if row is None:
        return {}
    try:
        api_key = decrypt_secret(row.api_key_enc)
    except SecretDecodeError:
        return {}
    return {"base_url": row.base_url, "model": row.model, "api_key": api_key}


def build_client(db: Session, owner_email: str) -> Any | None:
    """按账户配置创建 LLMClient；未配置或密钥损坏返回 ``None``（由调用方给可操作错误）。"""

    kwargs = client_kwargs(db, owner_email)
    if not kwargs:
        return None
    from core.llm_client import LLMClient  # 延迟导入：避免 core -> services 的循环

    return LLMClient(**kwargs)


def require_configured(db: Session, owner_email: str) -> None:
    if is_configured(db, owner_email):
        return
    raise AppError(
        ErrorCode.LLM_CONFIG_REQUIRED,
        f"还没有配置模型 API：{_CONFIG_HINT}",
        details={"settings_path": "模型 API 设置", "mode": key_mode()},
    )


def gate(db: Session, owner_email: str, *, action: str | None = None) -> None:
    """命令入口的拦截：同步命令（不耗模型）放行，其余要求已配置。"""

    if action is not None and action in SYNC_ACTIONS:
        return
    require_configured(db, owner_email)


def view(db: Session, owner_email: str) -> dict[str, Any]:
    """返回给前端的配置视图：**永远不回显完整密钥**。"""

    mode = key_mode()
    row = _row(db, owner_email)
    configured = bool(row is not None) if mode == KEY_MODE_BYOK else is_configured(db, owner_email)
    return {
        "mode": mode,
        "configured": configured,
        "requires_config": mode == KEY_MODE_BYOK and not configured,
        "base_url": (row.base_url if row is not None else C.LLM_CONFIG_DEFAULT_BASE_URL),
        "model": (row.model if row is not None else ""),
        "key_hint": (row.key_hint if row is not None else ""),
        "disable_reasoning": (
            bool(row.disable_reasoning) if row is not None else bool(settings.llm_disable_reasoning)
        ),
        "updated_at": (row.updated_at if row is not None else None),
        "last_verified_at": (row.last_verified_at if row is not None else None),
        "last_error": (row.last_error if row is not None else None),
        "defaults": {
            "base_url": C.LLM_CONFIG_DEFAULT_BASE_URL,
            "model": C.LLM_CONFIG_DEFAULT_MODEL,
        },
        "settings_path": "模型 API 设置",
        "key_present": bool(str(getattr(settings, "llm_api_key", "") or "").strip()),
    }


def save(db: Session, owner_email: str, payload: C.LlmConfigPayload) -> dict[str, Any]:
    """保存（或覆盖）账户配置；密钥只落密文，返回视图。"""

    hint = mask_secret(payload.api_key)
    row = llm_config_repo.upsert_config(
        db,
        owner_email,
        base_url=payload.base_url,
        model=payload.model,
        api_key_enc=encrypt_secret(payload.api_key),
        key_hint=hint,
        disable_reasoning=bool(payload.disable_reasoning),
        now_iso=C.utc_now_iso(),
    )
    if row.last_error:
        row.last_error = None
        db.flush()
    return view(db, owner_email)


def remove(db: Session, owner_email: str) -> dict[str, Any]:
    llm_config_repo.delete_config(db, owner_email)
    return view(db, owner_email)


async def test_connection(db: Session, owner_email: str) -> dict[str, Any]:
    """用一次极小的补全请求验证配置是否可用；结果写回 ``last_verified_at`` / ``last_error``。"""

    row = _row(db, owner_email)
    if row is None or key_mode() == KEY_MODE_SYSTEM:
        raise AppError(
            ErrorCode.LLM_CONFIG_REQUIRED,
            f"还没有保存自己的模型 API 配置：{_CONFIG_HINT}",
        )
    client = build_client(db, owner_email)
    if client is None:
        raise AppError(ErrorCode.LLM_CONFIG_REQUIRED, "配置无法解密，请重新填写模型 API Key。")
    started = time.time()
    try:
        result = await client.complete(
            messages=[{"role": "user", "content": "只回复 ok 两个字符，不要输出其它内容。"}],
            max_output_tokens=16,
            timeout_seconds=float(settings.llm_timeout_seconds),
            purpose="config.test",
            temperature=0.0,
        )
    except Exception as exc:  # noqa: BLE001 - 把上游失败翻译成可读结论，不让 500 冒泡
        message = str(exc)[:500] or type(exc).__name__
        llm_config_repo.mark_result(db, row, ok=False, error=message)
        return {
            "ok": False,
            "error": message,
            "code": str(getattr(exc, "code", "") or ""),
            "elapsed_ms": int((time.time() - started) * 1000),
            "view": view(db, owner_email),
        }
    llm_config_repo.mark_result(db, row, ok=True)
    return {
        "ok": True,
        "reply": (getattr(result, "text", "") or "").strip()[:40],
        "model": getattr(result, "model", ""),
        "elapsed_ms": int((time.time() - started) * 1000),
        "view": view(db, owner_email),
    }
