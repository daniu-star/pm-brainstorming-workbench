"""用户自带模型配置（BYOK）仓储：一个 owner 一行，密钥只存密文。"""
from __future__ import annotations

from sqlalchemy.orm import Session

from domain import contracts as C
from repositories.database import LlmConfigRow

__all__ = ["delete_config", "get_config", "upsert_config"]


def get_config(db: Session, owner_email: str) -> LlmConfigRow | None:
    if not owner_email:
        return None
    return db.get(LlmConfigRow, owner_email)


def upsert_config(
    db: Session,
    owner_email: str,
    *,
    base_url: str,
    model: str,
    api_key_enc: str,
    key_hint: str,
    disable_reasoning: bool,
    now_iso: str | None = None,
) -> LlmConfigRow:
    """写入或更新配置；``fail_count`` 之类运行期字段在 ：func:`mark_result` 里维护。"""

    row = db.get(LlmConfigRow, owner_email)
    if row is None:
        row = LlmConfigRow(owner_email=owner_email)
        db.add(row)
    row.base_url = base_url
    row.model = model
    row.api_key_enc = api_key_enc
    row.key_hint = key_hint
    row.disable_reasoning = 1 if disable_reasoning else 0
    row.updated_at = now_iso or C.utc_now_iso()
    db.flush()
    return row


def mark_result(
    db: Session,
    row: LlmConfigRow,
    *,
    ok: bool,
    error: str | None = None,
    now_iso: str | None = None,
) -> None:
    """记录最近一次连通性检查结果（成功清空错误，失败保留可读原因）。"""

    stamp = now_iso or C.utc_now_iso()
    if ok:
        row.last_verified_at = stamp
        row.last_error = None
    else:
        row.last_error = (error or "未知错误")[:1000]
    db.flush()


def delete_config(db: Session, owner_email: str) -> bool:
    row = db.get(LlmConfigRow, owner_email)
    if row is None:
        return False
    db.delete(row)
    db.flush()
    return True
