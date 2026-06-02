import os
import random
import time
import logging
from datetime import datetime, timedelta, timezone

import jwt

logger = logging.getLogger(__name__)

JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-me")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = 72

_jwt_secret_is_default = False


def _check_jwt_secret():
    global _jwt_secret_is_default
    if JWT_SECRET == "dev-secret-change-me":
        logger.error("JWT_SECRET 使用默认值，生产环境不安全！请设置环境变量 JWT_SECRET")
        _jwt_secret_is_default = True


def is_jwt_secret_safe() -> bool:
    return not _jwt_secret_is_default


_check_jwt_secret()

SMS_CODES: dict[str, dict] = {}

SMS_CODE_EXPIRE_MINUTES = 5


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
    SMS_CODES[phone] = {"code": code, "expires_at": expires_at}

    from core.sms import send_sms_via_alibaba
    result = send_sms_via_alibaba(phone, code)

    if result["success"]:
        return code, None
    else:
        logger.warning("短信发送失败 phone=%s reason=%s", phone[-4:], result.get("reason", "unknown"))
        return code, "sms_unavailable"


def verify_sms_code(phone: str, code: str) -> bool:
    entry = SMS_CODES.get(phone)
    if entry is None:
        return False
    if time.time() > entry["expires_at"]:
        del SMS_CODES[phone]
        return False
    if entry["code"] != code:
        return False
    del SMS_CODES[phone]
    return True
