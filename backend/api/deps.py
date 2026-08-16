"""API 公共依赖：会话归属校验（B001）。"""
import asyncio
import logging

from fastapi import HTTPException, Request

from db.session_store import session_store

logger = logging.getLogger(__name__)


async def require_session_owner(session_id: str, request: Request) -> dict:
    """校验 session_id 合法且属于当前登录用户，返回会话数据。

    - ID 格式非法（含路径穿越尝试）→ 400
    - 会话不存在 → 404
    - 会话不属于当前用户 → 403
    """
    caller_email = getattr(request.state, "user_email", None)
    try:
        session = await asyncio.to_thread(session_store.get, session_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="无效的会话 ID") from exc
    if session is None:
        raise HTTPException(status_code=404, detail="会话未找到")
    owner = session.get("owner_email", "")
    if not caller_email or owner != caller_email:
        logger.warning("会话访问被拒绝：owner 不匹配（session=%s）", session_id)
        raise HTTPException(status_code=403, detail="无权访问该会话")
    return session
