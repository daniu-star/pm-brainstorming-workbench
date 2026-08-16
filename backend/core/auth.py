import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import smtplib
import ssl
import time
from collections import deque
from dataclasses import dataclass
from email.message import EmailMessage
from threading import RLock

from core.config import settings

logger = logging.getLogger(__name__)


EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
MAX_VERIFY_ATTEMPTS = 5

# 纯内存 IP 限流：每 IP 每小时最多发送 10 次，全局每分钟最多 30 次（B035）。
IP_SEND_LIMIT_PER_HOUR = 10
GLOBAL_SEND_LIMIT_PER_MINUTE = 30
# 验证码记录在过期后再保留 30 分钟，供 GC 清理（B033）。
CODE_GC_GRACE_SECONDS = 30 * 60


class AuthConfigurationError(RuntimeError):
    pass


class AuthRateLimitError(RuntimeError):
    pass


@dataclass
class VerificationCode:
    code_hash: str
    expires_at: float
    next_send_at: float
    attempts: int = 0


_verification_codes: dict[str, VerificationCode] = {}
_code_lock = RLock()

_ip_send_times: dict[str, deque[float]] = {}
_ip_send_lock = RLock()


def _gc_verification_codes(now: float | None = None) -> int:
    """清理已过期且超过保留期的验证码记录，防止内存泄漏（B033）。"""
    now = time.time() if now is None else now
    with _code_lock:
        expired = [
            email
            for email, record in _verification_codes.items()
            if record.expires_at + CODE_GC_GRACE_SECONDS < now
        ]
        for email in expired:
            _verification_codes.pop(email, None)
    return len(expired)


def _gc_send_times(now: float) -> None:
    """清理 IP 限流窗口之外的旧记录。"""
    with _ip_send_lock:
        for ip in list(_ip_send_times.keys()):
            times = _ip_send_times[ip]
            while times and now - times[0] >= 3600:
                times.popleft()
            if not times:
                _ip_send_times.pop(ip, None)


def check_send_rate_limit(ip: str) -> None:
    """发送验证码前的 IP 维度限流（每 IP 10 次/小时 + 全局 30 次/分钟）。

    通过后记录本次时间戳；超限抛出 AuthRateLimitError（B035）。
    """
    now = time.time()
    with _ip_send_lock:
        _gc_send_times(now)
        times = _ip_send_times.setdefault(ip, deque())
        if len([t for t in times if now - t < 3600]) >= IP_SEND_LIMIT_PER_HOUR:
            raise AuthRateLimitError("发送过于频繁，请一小时后再试")
        global_count = sum(
            1
            for ts in _ip_send_times.values()
            for t in ts
            if now - t < 60
        )
        if global_count >= GLOBAL_SEND_LIMIT_PER_MINUTE:
            raise AuthRateLimitError("当前发送请求过多，请稍后再试")
        times.append(now)


def normalize_email(email: str) -> str:
    normalized = email.strip().lower()
    if len(normalized) > 254 or not EMAIL_PATTERN.fullmatch(normalized):
        raise ValueError("请输入有效的邮箱地址")
    return normalized


def smtp_is_configured() -> bool:
    return bool(
        settings.smtp_host
        and settings.smtp_port
        and settings.smtp_username
        and settings.smtp_password
        and (settings.smtp_from or settings.smtp_username)
    )


def auth_is_configured() -> bool:
    return len(settings.auth_secret_key) >= 32


def _require_auth_configuration() -> None:
    missing: list[str] = []
    if not smtp_is_configured():
        missing.append("SMTP_USERNAME / SMTP_PASSWORD / SMTP_FROM")
    if not auth_is_configured():
        missing.append("AUTH_SECRET_KEY（至少 32 个字符）")
    if missing:
        raise AuthConfigurationError("登录服务尚未配置：" + "、".join(missing))


def _hash_code(email: str, code: str) -> str:
    return hmac.new(
        settings.auth_secret_key.encode("utf-8"),
        f"{email}:{code}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _send_verification_email(email: str, code: str) -> None:
    sender = settings.smtp_from or settings.smtp_username
    message = EmailMessage()
    message["Subject"] = "PM Brainstorm 登录验证码"
    message["From"] = sender
    message["To"] = email
    message.set_content(
        f"你的登录验证码是：{code}\n\n验证码在 {settings.auth_code_expire_minutes} 分钟内有效。"
        "如非本人操作，请忽略此邮件。"
    )
    message.add_alternative(
        f"""
        <div style="font-family:Arial,sans-serif;max-width:520px;margin:auto;padding:32px;color:#172033">
          <p style="font-size:12px;letter-spacing:.12em;color:#64748b">PM BRAINSTORM</p>
          <h1 style="font-size:22px;margin:12px 0">邮箱登录验证码</h1>
          <p style="color:#475569">请在登录页面输入以下验证码：</p>
          <p style="font-size:32px;font-weight:700;letter-spacing:.28em;color:#0891b2;margin:24px 0">{code}</p>
          <p style="font-size:13px;color:#64748b">验证码在 {settings.auth_code_expire_minutes} 分钟内有效。如非本人操作，请忽略此邮件。</p>
        </div>
        """,
        subtype="html",
    )

    context = ssl.create_default_context()
    if settings.smtp_use_ssl:
        with smtplib.SMTP_SSL(
            settings.smtp_host,
            settings.smtp_port,
            timeout=settings.smtp_timeout_seconds,
            context=context,
        ) as client:
            client.login(settings.smtp_username, settings.smtp_password)
            client.send_message(message)
        return

    with smtplib.SMTP(
        settings.smtp_host,
        settings.smtp_port,
        timeout=settings.smtp_timeout_seconds,
    ) as client:
        client.ehlo()
        client.starttls(context=context)
        client.ehlo()
        client.login(settings.smtp_username, settings.smtp_password)
        client.send_message(message)


def issue_verification_code(email: str) -> int:
    _require_auth_configuration()
    normalized = normalize_email(email)
    now = time.time()
    _gc_verification_codes(now)

    code = f"{secrets.randbelow(1_000_000):06d}"

    with _code_lock:
        existing = _verification_codes.get(normalized)
        if existing and existing.next_send_at > now:
            wait_seconds = max(1, int(existing.next_send_at - now + 0.999))
            raise AuthRateLimitError(f"请等待 {wait_seconds} 秒后重新发送")
        # 锁内预占 next_send_at（发送前置标记），阻断发送期间的并发重复请求（B034）。
        _verification_codes[normalized] = VerificationCode(
            code_hash=_hash_code(normalized, code),
            expires_at=now + settings.auth_code_expire_minutes * 60,
            next_send_at=now + settings.auth_code_resend_seconds,
        )

    try:
        _send_verification_email(normalized, code)
    except Exception:
        # SMTP 发送失败：锁内回滚删除预占记录，让用户可以立即重试（B034）。
        with _code_lock:
            _verification_codes.pop(normalized, None)
        raise

    return settings.auth_code_resend_seconds


def verify_email_code(email: str, code: str) -> bool:
    normalized = normalize_email(email)
    normalized_code = code.strip()
    if not re.fullmatch(r"\d{6}", normalized_code):
        return False

    now = time.time()
    with _code_lock:
        record = _verification_codes.get(normalized)
        if not record or record.expires_at < now:
            _verification_codes.pop(normalized, None)
            return False
        if record.attempts >= MAX_VERIFY_ATTEMPTS:
            _verification_codes.pop(normalized, None)
            return False

        record.attempts += 1
        valid = hmac.compare_digest(record.code_hash, _hash_code(normalized, normalized_code))
        if valid:
            _verification_codes.pop(normalized, None)
        return valid


def create_session_token(email: str) -> str:
    if not auth_is_configured():
        raise AuthConfigurationError("AUTH_SECRET_KEY 未配置或长度不足")
    payload = {
        "email": normalize_email(email),
        "exp": int(time.time()) + settings.auth_session_days * 24 * 60 * 60,
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).rstrip(b"=")
    signature = hmac.new(
        settings.auth_secret_key.encode("utf-8"), encoded, hashlib.sha256
    ).digest()
    encoded_signature = base64.urlsafe_b64encode(signature).rstrip(b"=")
    return f"{encoded.decode('ascii')}.{encoded_signature.decode('ascii')}"


def verify_session_token(token: str | None) -> str | None:
    if not token or not auth_is_configured():
        return None
    try:
        encoded_text, signature_text = token.split(".", 1)
        encoded = encoded_text.encode("ascii")
        expected = hmac.new(
            settings.auth_secret_key.encode("utf-8"), encoded, hashlib.sha256
        ).digest()
        padding = "=" * (-len(signature_text) % 4)
        supplied = base64.urlsafe_b64decode(signature_text + padding)
        if not hmac.compare_digest(expected, supplied):
            return None

        payload_padding = "=" * (-len(encoded_text) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded_text + payload_padding))
        if int(payload.get("exp", 0)) <= int(time.time()):
            return None
        return normalize_email(str(payload["email"]))
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None
