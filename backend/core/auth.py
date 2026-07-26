import json
import os
import random
import time
import logging
import secrets
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
