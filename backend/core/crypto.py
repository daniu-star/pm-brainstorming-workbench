"""对称加密：把用户自己填写的模型 API Key 加密后落库（BYOK / 账户自带密钥）。

约定：
- 密钥由 ``settings.auth_secret_key`` 派生（Fernet 需要 32 字节 base64 密钥）；
  换机器/换 ``AUTH_SECRET_KEY`` 后旧密文无法解密，此时要求用户重新填写即可，
  不允许把明文写进日志或接口响应。
- 只在服务端使用；数据库被拷走时密文不能直接当密钥用。
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from core.config import settings


class SecretDecodeError(ValueError):
    """密文无法解密：密钥变了或数据损坏。"""


def _fernet() -> Fernet:
    seed = f"llm-key::{settings.auth_secret_key}".encode("utf-8")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(seed).digest()))


def encrypt_secret(value: str) -> str:
    """加密明文密钥，返回可直接存 Text 列的 ASCII 字符串。"""

    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    """解密密文；失败统一抛 :class:`SecretDecodeError`（不泄露原始异常细节）。"""

    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise SecretDecodeError("模型密钥无法解密，请重新填写") from exc


def mask_secret(value: str) -> str:
    """只回显末 4 位；过短时整体打码，避免接口回显完整密钥。"""

    text = (value or "").strip()
    if len(text) <= 4:
        return "****"
    return f"****{text[-4:]}"
