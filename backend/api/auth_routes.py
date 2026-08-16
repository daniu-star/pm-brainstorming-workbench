import asyncio
import logging
import re
import smtplib
import socket
import ssl

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from core.auth import (
    AuthConfigurationError,
    AuthRateLimitError,
    check_send_rate_limit,
    create_session_token,
    issue_verification_code,
    normalize_email,
    verify_email_code,
    verify_session_token,
)
from core.config import settings

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/api/auth", tags=["auth"])


class EmailCodeRequest(BaseModel):
    email: str


class EmailCodeVerifyRequest(BaseModel):
    email: str
    code: str


@router.post("/email/code")
async def send_email_code(payload: EmailCodeRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    try:
        email = normalize_email(payload.email)
        # IP 维度限流：每 IP 10 次/小时，全局 30 次/分钟（B035）。
        check_send_rate_limit(client_ip)
        retry_after = await asyncio.to_thread(issue_verification_code, email)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except AuthRateLimitError as exc:
        # 从限流文案中解析等待秒数，供前端启动冷却倒计时（B120）。
        match = re.search(r"\d+", str(exc))
        wait_seconds = int(match.group()) if match else 60
        raise HTTPException(
            status_code=429,
            detail={"message": str(exc), "retry_after": wait_seconds},
        ) from exc
    except AuthConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except smtplib.SMTPAuthenticationError as exc:
        logger.error("SMTP 认证失败，请检查授权码配置：%s", exc.smtp_error)
        raise HTTPException(status_code=503, detail="邮件服务认证失败，请联系管理员") from exc
    except smtplib.SMTPRecipientsRefused as exc:
        logger.warning("SMTP 收件人被拒收：%s", exc.recipients)
        raise HTTPException(status_code=422, detail="目标邮箱拒收邮件，请确认邮箱地址有效") from exc
    except (socket.timeout, ConnectionError) as exc:
        logger.warning("SMTP 连接超时/失败：%s", type(exc).__name__)
        raise HTTPException(status_code=504, detail="邮件服务连接超时，请稍后重试") from exc
    except ssl.SSLError as exc:
        logger.warning("SMTP SSL 握手失败：%s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="邮件服务安全连接失败，请稍后重试") from exc
    except (smtplib.SMTPException, OSError) as exc:
        logger.warning("SMTP 发送失败：%s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="验证码邮件发送失败，请检查 SMTP 配置") from exc

    return {"ok": True, "retry_after": retry_after}


@router.post("/email/verify")
async def verify_code(payload: EmailCodeVerifyRequest, response: Response):
    try:
        email = normalize_email(payload.email)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if not verify_email_code(email, payload.code):
        raise HTTPException(status_code=401, detail="验证码错误或已过期")

    token = create_session_token(email)
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        max_age=settings.auth_session_days * 24 * 60 * 60,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        domain=settings.auth_cookie_domain or None,
        path="/",
    )
    return {"authenticated": True, "email": email}


@router.get("/me")
async def current_user(request: Request):
    email = verify_session_token(request.cookies.get(settings.auth_cookie_name))
    if not email:
        raise HTTPException(status_code=401, detail="登录状态已失效")
    return {"authenticated": True, "email": email}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(
        key=settings.auth_cookie_name,
        domain=settings.auth_cookie_domain or None,
        path="/",
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite=settings.auth_cookie_samesite,
    )
    return {"ok": True}
