import json
from pathlib import Path

import pytest

from core import auth, email_service
from core.config import settings


def test_email_code_is_hashed_single_use_and_case_insensitive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    codes_file = tmp_path / "email_codes.json"
    delivered: dict[str, str] = {}
    monkeypatch.setattr(auth, "EMAIL_CODES_FILE", codes_file)
    monkeypatch.setattr(
        email_service,
        "send_login_code",
        lambda recipient, code: delivered.update(recipient=recipient, code=code),
    )
    monkeypatch.setattr(settings, "email_code_resend_seconds", 60)
    monkeypatch.setattr(settings, "email_code_expire_minutes", 10)

    assert auth.send_email_code(" PM@Example.com ") == 60
    assert delivered["recipient"] == "pm@example.com"
    assert delivered["code"].isdigit() and len(delivered["code"]) == 6

    stored = json.loads(codes_file.read_text("utf-8"))
    assert delivered["code"] not in json.dumps(stored)
    assert auth.verify_email_code("PM@example.com", delivered["code"]) is True
    assert auth.verify_email_code("pm@example.com", delivered["code"]) is False


def test_email_code_send_rate_limit(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(auth, "EMAIL_CODES_FILE", tmp_path / "email_codes.json")
    monkeypatch.setattr(email_service, "send_login_code", lambda _recipient, _code: None)
    monkeypatch.setattr(settings, "email_code_resend_seconds", 60)

    auth.send_email_code("user@example.com")
    with pytest.raises(auth.AuthRateLimitError, match="重新发送"):
        auth.send_email_code("user@example.com")


@pytest.mark.parametrize("email", ["", "missing-at.example.com", "a@b", "a b@example.com"])
def test_invalid_email_is_rejected(email: str):
    with pytest.raises(ValueError, match="有效的邮箱"):
        auth.normalize_email(email)
