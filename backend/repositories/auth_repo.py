"""鉴权仓储（接口契约 §3.4）。

把一次性验证码与限流计数从进程内内存（旧 ``core/auth.py``）迁移到数据库，
使多进程/多 Worker 共享同一份状态：

- ``issue_code``：签发新码并立即失效该邮箱的旧码；
- ``get_active_code``：读取未消费且未过期的最近一条码；
- ``record_attempt`` / ``consume_code``：错误尝试计数与消费；
- ``hit_rate_limit``：整窗口对齐的原子计数限流（SQLite 单写者语义）。

事务边界由调用方通过 ``repositories.database.session_scope()`` 控制；
本模块只 ``flush`` 不 commit。
"""
from __future__ import annotations

import hmac

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from domain.contracts import new_id
from domain.errors import AppError, ErrorCode
from repositories.database import AuthCodeRow, RateLimitRow

MAX_CODE_ATTEMPTS = 5
"""同一验证码允许的最大错误尝试次数（与旧 ``core.auth.MAX_VERIFY_ATTEMPTS`` 对齐）。"""

__all__ = [
    "MAX_CODE_ATTEMPTS",
    "consume_code",
    "get_active_code",
    "hit_rate_limit",
    "invalidate_codes",
    "issue_code",
    "prune_expired_codes",
    "record_attempt",
    "verify_code",
]


def _normalize_email(email: str) -> str:
    normalized = (email or "").strip().lower()
    if not normalized:
        raise AppError(ErrorCode.INVALID_REQUEST, "邮箱不能为空")
    return normalized


def issue_code(
    db: Session,
    *,
    email: str,
    code_hash: str,
    ttl_seconds: int,
    now_epoch: int,
) -> None:
    """签发验证码并失效该邮箱此前未消费的旧码。"""

    normalized = _normalize_email(email)
    if not code_hash:
        raise AppError(ErrorCode.INVALID_REQUEST, "code_hash 不能为空")
    if not isinstance(ttl_seconds, int) or ttl_seconds <= 0:
        raise AppError(ErrorCode.INVALID_REQUEST, "ttl_seconds 必须是正整数")

    # 同一时刻只允许一条有效码：旧码直接标记为已消费（幂等失效）。
    db.execute(
        update(AuthCodeRow)
        .where(
            func.lower(AuthCodeRow.email) == normalized,
            AuthCodeRow.consumed_at.is_(None),
        )
        .values(consumed_at=now_epoch)
    )
    row = AuthCodeRow(
        id=new_id(),
        email=normalized,
        code_hash=code_hash,
        expires_at=int(now_epoch) + int(ttl_seconds),
        attempts=0,
        consumed_at=None,
        created_at=int(now_epoch),
        last_sent_at=int(now_epoch),
    )
    db.add(row)
    db.flush()


def get_active_code(db: Session, email: str, now_epoch: int) -> AuthCodeRow | None:
    """返回该邮箱最近一条未消费且未过期的验证码；没有则 None。"""

    normalized = _normalize_email(email)
    stmt = (
        select(AuthCodeRow)
        .where(
            func.lower(AuthCodeRow.email) == normalized,
            AuthCodeRow.consumed_at.is_(None),
            AuthCodeRow.expires_at > int(now_epoch),
        )
        .order_by(AuthCodeRow.created_at.desc(), AuthCodeRow.id.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


def record_attempt(db: Session, row: AuthCodeRow, now_epoch: int) -> int:
    """原子累加一次错误尝试并返回新计数（并发安全的条件自增）。"""

    if row is None:  # pragma: no cover - 防御性检查
        raise AppError(ErrorCode.INVALID_REQUEST, "验证码记录不存在")
    result = db.execute(
        update(AuthCodeRow)
        .where(AuthCodeRow.id == row.id, AuthCodeRow.consumed_at.is_(None))
        .values(attempts=AuthCodeRow.attempts + 1, last_sent_at=row.last_sent_at)
    )
    if result.rowcount == 0:
        # 已被消费/失效：仍返回当前计数，由调用方按“验证失败”处理。
        db.expire(row, ["attempts"])
        return int(row.attempts or 0)
    db.expire(row, ["attempts"])
    return int(row.attempts)


def consume_code(db: Session, row: AuthCodeRow, now_epoch: int) -> None:
    """把验证码标记为已消费（重复调用幂等）。"""

    if row is None:  # pragma: no cover - 防御性检查
        raise AppError(ErrorCode.INVALID_REQUEST, "验证码记录不存在")
    result = db.execute(
        update(AuthCodeRow)
        .where(AuthCodeRow.id == row.id, AuthCodeRow.consumed_at.is_(None))
        .values(consumed_at=int(now_epoch))
    )
    if result.rowcount:
        row.consumed_at = int(now_epoch)
    db.flush()


def invalidate_codes(db: Session, *, email: str, now_epoch: int) -> int:
    """失效某邮箱全部未消费验证码，返回受影响行数。"""

    normalized = _normalize_email(email)
    result = db.execute(
        update(AuthCodeRow)
        .where(
            func.lower(AuthCodeRow.email) == normalized,
            AuthCodeRow.consumed_at.is_(None),
        )
        .values(consumed_at=int(now_epoch))
    )
    return int(result.rowcount or 0)


def verify_code(
    db: Session,
    *,
    email: str,
    code_hash: str,
    now_epoch: int,
    max_attempts: int = MAX_CODE_ATTEMPTS,
) -> bool:
    """校验验证码：成功即消费；超过 ``max_attempts`` 次错误后一律拒绝并失效。

    这是 ``get_active_code``/``record_attempt``/``consume_code`` 的组合便捷函数，
    便于上层服务与测试复用“5 次错码上限”的语义。
    """

    row = get_active_code(db, email, now_epoch)
    if row is None:
        return False
    if int(row.attempts or 0) >= int(max_attempts):
        consume_code(db, row, now_epoch)
        return False
    matched = bool(code_hash) and hmac.compare_digest(str(row.code_hash), str(code_hash))
    if matched:
        consume_code(db, row, now_epoch)
        return True
    record_attempt(db, row, now_epoch)
    return False


def hit_rate_limit(
    db: Session,
    *,
    scope: str,
    key: str,
    limit: int,
    window_seconds: int,
    now_epoch: int,
) -> bool:
    """整窗口计数限流：返回 True 表示允许并已计数，False 表示超限。

    窗口起点 ``window_start = now_epoch // window_seconds * window_seconds``，
    计数通过 ``UPDATE ... WHERE count < limit`` 条件自增保证同一窗口内不越界；
    行不存在时插入，并发插入冲突则回退为条件更新（SQLite 单写者）。
    """

    if not scope or not key:
        raise AppError(ErrorCode.INVALID_REQUEST, "scope 与 key 不能为空")
    if not isinstance(limit, int) or limit <= 0:
        raise AppError(ErrorCode.INVALID_REQUEST, "limit 必须是正整数")
    if not isinstance(window_seconds, int) or window_seconds <= 0:
        raise AppError(ErrorCode.INVALID_REQUEST, "window_seconds 必须是正整数")

    now = int(now_epoch)
    window_start = now // window_seconds * window_seconds

    # 单条 UPSERT 完成“行不存在则插入 1，否则条件自增”，避免任何异常污染调用方事务。
    stmt = (
        sqlite_insert(RateLimitRow)
        .values(
            id=new_id(),
            scope=scope,
            key=key,
            window_start=window_start,
            count=1,
            updated_at=now,
        )
        .on_conflict_do_update(
            index_elements=["scope", "key", "window_start"],
            set_={"count": RateLimitRow.count + 1, "updated_at": now},
            where=(RateLimitRow.count < limit),
        )
        .returning(RateLimitRow.count)
    )
    granted = db.execute(stmt).scalar_one_or_none()
    return granted is not None


def prune_expired_codes(db: Session, *, now_epoch: int, limit: int = 2000) -> int:
    """删除已过期的一次性验证码，返回删除行数。

    验证码 10 分钟过期，过期后 ``verify_code`` 不会再读它；不清理时每次登录都会留一行。
    只删 ``expires_at < now_epoch`` 的行，未过期（含刚签发）的一行不动。
    """

    cutoff = int(now_epoch)
    result = db.execute(
        delete(AuthCodeRow)
        .where(
            AuthCodeRow.id.in_(
                select(AuthCodeRow.id)
                .where(AuthCodeRow.expires_at < cutoff)
                .limit(max(1, int(limit)))
            )
        )
    )
    return int(result.rowcount or 0)
