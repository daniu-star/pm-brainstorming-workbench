"""邮箱验证码登录：验证码与限流状态存放在数据库（``repositories.auth_repo``）。

M1 改造要点（B033/B034/B035、PRD §7.1）：

* 一次性验证码与限流计数不再放在**进程内存**，而是经 ``auth_repo`` 写入 SQLite，
  多 Worker / 重启后仍然有效（旧 ``_verification_codes`` 等内存结构仅作为兼容垫片保留，
  不再是权威存储）；
* 签名 Cookie（HMAC-SHA256，``AUTH_SECRET_KEY``）的逻辑**完全不变**，避免登录态失效；
* 对外函数名与签名保持不变：``issue_verification_code`` / ``verify_email_code`` /
  ``check_send_rate_limit`` / ``create_session_token`` / ``verify_session_token``。

数据库访问一律走 ``repositories.database.session_scope()``（短事务），块内不做 SMTP 调用。
"""
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
import urllib.request
from collections import deque
from dataclasses import dataclass
from email.message import EmailMessage
from threading import RLock

from core.config import settings

logger = logging.getLogger(__name__)


EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
MAX_VERIFY_ATTEMPTS = 5

# 限流阈值（B035）：每 IP 每小时最多发送 10 次，全局每分钟最多 30 次。
IP_SEND_LIMIT_PER_HOUR = 10
GLOBAL_SEND_LIMIT_PER_MINUTE = 30
IP_SEND_WINDOW_SECONDS = 3600
GLOBAL_SEND_WINDOW_SECONDS = 60
# 验证码记录在过期后再保留 30 分钟，供 GC 清理（B033）。
CODE_GC_GRACE_SECONDS = 30 * 60


class AuthConfigurationError(RuntimeError):
    pass


class AuthRateLimitError(RuntimeError):
    pass


@dataclass
class VerificationCode:
    """兼容旧测试/旧调用方的内存结构；权威存储已迁移到 ``auth_codes`` 表。"""

    code_hash: str
    expires_at: float
    next_send_at: float
    attempts: int = 0


#: 兼容垫片：旧代码（含 ``tests/test_auth.py`` 的 setUp/tearDown）会 ``clear()`` 它。
_verification_codes: dict[str, VerificationCode] = {}
_code_lock = RLock()

_ip_send_times: dict[str, deque[float]] = {}
_ip_send_lock = RLock()

_schema_ready = False
_schema_lock = RLock()


# --------------------------------------------------------------------------- #
# 数据库准备与兼容 GC
# --------------------------------------------------------------------------- #


def _ensure_schema() -> None:
    """确保表已建立（本地/测试快速路径；生产由迁移负责）。"""

    global _schema_ready
    if _schema_ready:
        return
    with _schema_lock:
        if _schema_ready:
            return
        from repositories import database as database_module

        database_module.initialize_database()
        _schema_ready = True


def _now_epoch() -> int:
    # 保持 ``core.auth.time.time`` 可被测试 patch（现有 test_auth 依赖这一点）。
    return int(time.time())


def _gc_verification_codes(now: float | None = None) -> int:
    """兼容旧接口：清理内存垫片中过期太久的记录（DB 行由活动码失效机制处理）。"""

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
    """兼容旧接口：内存限流窗口已退役，这里只清理垫片。"""

    with _ip_send_lock:
        for ip in list(_ip_send_times.keys()):
            times = _ip_send_times[ip]
            while times and now - times[0] >= IP_SEND_WINDOW_SECONDS:
                times.popleft()
            if not times:
                _ip_send_times.pop(ip, None)


# --------------------------------------------------------------------------- #
# 限流
# --------------------------------------------------------------------------- #


def check_send_rate_limit(ip: str) -> None:
    """发送验证码前的限流：每 IP 10 次/小时 + 全局 30 次/分钟（B035）。

    计数落在 ``rate_limits`` 表（整窗口对齐、原子自增），跨进程共享。
    数据库不可用时降级放行并记录告警——登录可用性优先于严格限流。
    """

    from repositories import auth_repo, database as database_module

    _ensure_schema()
    now = _now_epoch()
    key = (ip or "unknown").strip() or "unknown"
    try:
        with database_module.session_scope() as db:
            allowed_ip = auth_repo.hit_rate_limit(
                db,
                scope="auth_send_ip",
                key=key,
                limit=IP_SEND_LIMIT_PER_HOUR,
                window_seconds=IP_SEND_WINDOW_SECONDS,
                now_epoch=now,
            )
            if not allowed_ip:
                raise AuthRateLimitError("发送过于频繁，请一小时后再试")
            allowed_global = auth_repo.hit_rate_limit(
                db,
                scope="auth_send_global",
                key="global",
                limit=GLOBAL_SEND_LIMIT_PER_MINUTE,
                window_seconds=GLOBAL_SEND_WINDOW_SECONDS,
                now_epoch=now,
            )
            if not allowed_global:
                raise AuthRateLimitError("当前发送请求过多，请稍后再试")
    except AuthRateLimitError:
        raise
    except Exception:  # noqa: BLE001 - 数据库故障时降级放行，但必须留痕
        logger.warning("验证码发送限流降级（数据库不可用）", exc_info=True)


# --------------------------------------------------------------------------- #
# 邮箱与配置
# --------------------------------------------------------------------------- #


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


def _send_via_proxy(message: EmailMessage) -> bool:
    """用 HTTP 代理投递验证码邮件（第 40 批部署适配）。

    为什么需要：Hugging Face Space 等容器平台通常封禁出站 25/465/587，
    直连 ``smtplib`` 会一直超时到 ``smtp_timeout_seconds``，登录体验极差。
    配置 ``EMAIL_PROXY_URL`` + ``EMAIL_PROXY_KEY`` 后改为一次 HTTP POST，
    由外部服务（Vercel Serverless / 自建中转）完成真正的 SMTP 投递。

    只用标准库 ``urllib``，不给镜像增加 ``requests`` 依赖。
    返回 ``True`` 表示代理投递成功；未配置或失败返回 ``False``，调用方回落直连 SMTP。
    """
    proxy_url = (settings.email_proxy_url or "").strip()
    if not proxy_url:
        return False

    plain = message.get_body(preferencelist=("plain",))
    html = message.get_body(preferencelist=("html",))
    payload = {
        "to_email": message["To"],
        "subject": message["Subject"],
        "text": plain.get_content() if plain is not None else "",
        "html": html.get_content() if html is not None else "",
        "smtp_host": settings.smtp_host,
        "smtp_port": str(settings.smtp_port),
        "smtp_user": settings.smtp_username,
        "smtp_password": settings.smtp_password,
        "smtp_from_name": "PM Brainstorm",
    }
    request = urllib.request.Request(
        proxy_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-API-Key": settings.email_proxy_key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            body = response.read().decode("utf-8", "replace")
        parsed = json.loads(body or "{}")
        if not parsed.get("success"):
            logger.warning("event=auth.email_proxy_rejected body=%s", body[:200])
            return False
        logger.info("event=auth.email_proxy_sent")
        return True
    except Exception as error:  # noqa: BLE001 - 代理不可用时必须能回落直连
        logger.warning("event=auth.email_proxy_failed error=%s", type(error).__name__)
        return False


def _deliver_code_via_log(email: str, code: str) -> str:
    """把验证码写进服务日志，作为「邮件通道不可用」时的最后兜底。

    只应在 ``AUTH_CODE_LOG_FALLBACK=true`` 的私有部署里走到这里：
    日志只有平台所有者能读，公开站点必须改配 ``EMAIL_PROXY_URL`` 或可用 SMTP。
    """
    logger.warning(
        "event=auth.code_log_fallback email=%s code=%s "
        "hint=邮件通道不可用，验证码已写入服务日志（仅平台所有者可见）",
        email,
        code,
    )
    return "logs"


def _send_verification_email(email: str, code: str) -> str:  # pragma: no cover - 网络
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

    # 先试 HTTP 代理（容器平台常封出站 SMTP 端口），失败再回落直连。
    if _send_via_proxy(message):
        return "proxy"

    try:
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
            return "smtp"

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
        return "smtp"
    except Exception as error:  # noqa: BLE001 - 兜底开关要把任何投递失败都转成日志交付
        if not settings.auth_code_log_fallback:
            raise
        logger.warning(
            "event=auth.email_delivery_failed error=%s fallback=logs",
            type(error).__name__,
        )
        return _deliver_code_via_log(email, code)


# --------------------------------------------------------------------------- #
# 验证码签发 / 校验（DB 为权威存储）
# --------------------------------------------------------------------------- #


def issue_verification_code_with_delivery(email: str) -> tuple[int, str]:
    """签发验证码并返回 ``(重发等待秒数, 投递方式)``。

    投递方式：``proxy`` / ``smtp`` / ``logs``（仅当 AUTH_CODE_LOG_FALLBACK 打开且
    邮件投递失败时出现，验证码写进服务日志由平台所有者转达）。
    """
    _require_auth_configuration()
    normalized = normalize_email(email)
    now = _now_epoch()

    from repositories import auth_repo, database as database_module

    _ensure_schema()
    code = f"{secrets.randbelow(1_000_000):06d}"
    code_hash = _hash_code(normalized, code)

    with database_module.session_scope() as db:
        existing = auth_repo.get_active_code(db, normalized, now)
        if existing is not None:
            last_sent = int(existing.last_sent_at or existing.created_at or now)
            next_send_at = last_sent + int(settings.auth_code_resend_seconds)
            if next_send_at > now:
                wait_seconds = max(1, next_send_at - now)
                raise AuthRateLimitError(f"请等待 {wait_seconds} 秒后重新发送")
        auth_repo.issue_code(
            db,
            email=normalized,
            code_hash=code_hash,
            ttl_seconds=int(settings.auth_code_expire_minutes * 60),
            now_epoch=now,
        )

    try:
        delivery = _send_verification_email(normalized, code)
    except Exception:  # noqa: BLE001 - 发送失败必须让用户能立即重试
        with database_module.session_scope() as db:
            auth_repo.invalidate_codes(db, email=normalized, now_epoch=now)
        raise
    return int(settings.auth_code_resend_seconds), delivery


def issue_verification_code(email: str) -> int:
    """兼容旧调用方：只关心重发等待秒数。"""
    retry_after, _delivery = issue_verification_code_with_delivery(email)
    return retry_after


def verify_email_code(email: str, code: str) -> bool:
    normalized = normalize_email(email)
    normalized_code = code.strip()
    if not re.fullmatch(r"\d{6}", normalized_code):
        return False

    from repositories import auth_repo, database as database_module

    _ensure_schema()
    now = _now_epoch()
    code_hash = _hash_code(normalized, normalized_code)
    with database_module.session_scope() as db:
        return bool(
            auth_repo.verify_code(
                db,
                email=normalized,
                code_hash=code_hash,
                now_epoch=now,
                max_attempts=MAX_VERIFY_ATTEMPTS,
            )
        )


# --------------------------------------------------------------------------- #
# 签名 Cookie（逻辑不变，避免登录态失效）
# --------------------------------------------------------------------------- #


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
