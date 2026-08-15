import json
import os
import random
import time
import logging
import secrets
import hashlib
import hmac
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt

logger = logging.getLogger(__name__)

JWT_SECRET = os.getenv("JWT_SECRET") or secrets.token_urlsafe(48)
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 72

_jwt_secret_is_default = False

SMS_CODES_FILE = Path(os.getenv("SMS_CODES_FILE", "/tmp/sms_codes.json"))
SMS_CODE_EXPIRE_MINUTES = 5
EMAIL_CODES_FILE = Path(os.getenv("EMAIL_CODES_FILE", "/tmp/email_codes.json"))
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
MAX_EMAIL_VERIFY_ATTEMPTS = 5


class AuthRateLimitError(RuntimeError):
    pass


def _check_jwt_secret():
    global _jwt_secret_is_default
    if not os.getenv("JWT_SECRET"):
        logger.warning("JWT_SECRET 未配置，已生成进程级临时密钥；服务重启后用户需要重新登录")
        _jwt_secret_is_default = True


def is_jwt_secret_safe() -> bool:
    return not _jwt_secret_is_default


_check_jwt_secret()


def _load_sms_codes() -> dict:
    try:
        if SMS_CODES_FILE.exists():
            with open(SMS_CODES_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_sms_codes(codes: dict) -> None:
    try:
        SMS_CODES_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(SMS_CODES_FILE, "w") as f:
            json.dump(codes, f)
    except Exception as e:
        logger.warning("Failed to persist SMS codes: %s", e)


def _cleanup_expired_codes(codes: dict) -> dict:
    now = time.time()
    expired_keys = [k for k, v in codes.items() if now > v.get("expires_at", 0)]
    for k in expired_keys:
        del codes[k]
    return codes


def create_jwt_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + timedelta(hours=JWT_EXPIRE_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_jwt_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def derive_guest_user_id(installation_id: str) -> str:
    """Derive a stable, non-enumerable tenant id from a browser-held UUID."""
    digest = hmac.new(
        JWT_SECRET.encode("utf-8"),
        f"guest:{installation_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"guest_{digest[:32]}"


def generate_sms_code() -> str:
    return f"{random.randint(0, 999999):06d}"


def send_sms_code(phone: str) -> tuple[str, str | None]:
    code = generate_sms_code()
    expires_at = time.time() + SMS_CODE_EXPIRE_MINUTES * 60

    codes = _load_sms_codes()
    _cleanup_expired_codes(codes)
    codes[phone] = {"code": code, "expires_at": expires_at}
    _save_sms_codes(codes)

    from core.sms import send_sms_via_alibaba
    result = send_sms_via_alibaba(phone, code)

    if result["success"]:
        return code, None
    else:
        logger.warning("短信发送失败 phone=%s reason=%s", phone[-4:], result.get("reason", "unknown"))
        return code, "sms_unavailable"


def verify_sms_code(phone: str, code: str) -> bool:
    codes = _load_sms_codes()
    _cleanup_expired_codes(codes)

    entry = codes.get(phone)
    if entry is None:
        return False
    if time.time() > entry["expires_at"]:
        del codes[phone]
        _save_sms_codes(codes)
        return False
    if entry["code"] != code:
        return False
    del codes[phone]
    _save_sms_codes(codes)
    return True


def normalize_email(email: str) -> str:
    normalized = email.strip().lower()
    if len(normalized) > 254 or not EMAIL_PATTERN.fullmatch(normalized):
        raise ValueError("请输入有效的邮箱地址")
    return normalized


def _hash_email_code(email: str, code: str) -> str:
    return hmac.new(
        JWT_SECRET.encode("utf-8"),
        f"{email}:{code}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _load_email_codes() -> dict:
    try:
        if EMAIL_CODES_FILE.exists():
            with open(EMAIL_CODES_FILE, "r", encoding="utf-8") as file:
                return json.load(file)
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return {}


def _save_email_codes(codes: dict) -> None:
    EMAIL_CODES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(EMAIL_CODES_FILE, "w", encoding="utf-8") as file:
        json.dump(codes, file, ensure_ascii=False)


def send_email_code(email: str) -> int:
    from core.config import settings
    from core.email_service import send_login_code

    normalized = normalize_email(email)
    now = time.time()
    codes = _load_email_codes()
    entry = codes.get(normalized)
    if entry and float(entry.get("next_send_at", 0)) > now:
        wait_seconds = max(1, int(float(entry["next_send_at"]) - now + 0.999))
        raise AuthRateLimitError(f"请等待 {wait_seconds} 秒后重新发送")

    code = f"{secrets.randbelow(1_000_000):06d}"
    send_login_code(normalized, code)
    codes[normalized] = {
        "code_hash": _hash_email_code(normalized, code),
        "expires_at": now + settings.email_code_expire_minutes * 60,
        "next_send_at": now + settings.email_code_resend_seconds,
        "attempts": 0,
    }
    _save_email_codes(codes)
    return settings.email_code_resend_seconds


def verify_email_code(email: str, code: str) -> bool:
    normalized = normalize_email(email)
    normalized_code = code.strip()
    if not re.fullmatch(r"\d{6}", normalized_code):
        return False

    codes = _load_email_codes()
    entry = codes.get(normalized)
    if not entry or time.time() > float(entry.get("expires_at", 0)):
        codes.pop(normalized, None)
        _save_email_codes(codes)
        return False

    attempts = int(entry.get("attempts", 0)) + 1
    if attempts > MAX_EMAIL_VERIFY_ATTEMPTS:
        codes.pop(normalized, None)
        _save_email_codes(codes)
        return False
    entry["attempts"] = attempts

    valid = hmac.compare_digest(
        str(entry.get("code_hash", "")),
        _hash_email_code(normalized, normalized_code),
    )
    if valid:
        codes.pop(normalized, None)
    _save_email_codes(codes)
    return valid
