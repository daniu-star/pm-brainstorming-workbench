import time
import unittest
from unittest.mock import patch

from core import auth
from core.config import settings


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.original_values = {
            "auth_secret_key": settings.auth_secret_key,
            "smtp_username": settings.smtp_username,
            "smtp_password": settings.smtp_password,
            "smtp_from": settings.smtp_from,
            "auth_code_expire_minutes": settings.auth_code_expire_minutes,
            "auth_code_resend_seconds": settings.auth_code_resend_seconds,
        }
        settings.auth_secret_key = "test-secret-key-that-is-longer-than-32-characters"
        settings.smtp_username = "sender@163.com"
        settings.smtp_password = "smtp-authorization-code"
        settings.smtp_from = "sender@163.com"
        settings.auth_code_expire_minutes = 10
        settings.auth_code_resend_seconds = 60
        auth._verification_codes.clear()

    def tearDown(self):
        auth._verification_codes.clear()
        for name, value in self.original_values.items():
            setattr(settings, name, value)

    def test_session_token_round_trip_and_tamper_rejection(self):
        token = auth.create_session_token("USER@example.com")
        self.assertEqual(auth.verify_session_token(token), "user@example.com")
        self.assertIsNone(auth.verify_session_token(token + "tampered"))

    def test_expired_session_token_is_rejected(self):
        with patch("core.auth.time.time", return_value=100):
            token = auth.create_session_token("user@example.com")
        with patch("core.auth.time.time", return_value=100 + settings.auth_session_days * 86400 + 1):
            self.assertIsNone(auth.verify_session_token(token))

    def test_verification_code_can_only_be_used_once(self):
        sent: dict[str, str] = {}

        def capture(email: str, code: str):
            sent["email"] = email
            sent["code"] = code

        with patch("core.auth._send_verification_email", side_effect=capture):
            retry_after = auth.issue_verification_code("USER@example.com")

        self.assertEqual(retry_after, 60)
        self.assertEqual(sent["email"], "user@example.com")
        self.assertTrue(auth.verify_email_code("user@example.com", sent["code"]))
        self.assertFalse(auth.verify_email_code("user@example.com", sent["code"]))

    def test_expired_verification_code_is_rejected(self):
        auth._verification_codes["user@example.com"] = auth.VerificationCode(
            code_hash=auth._hash_code("user@example.com", "123456"),
            expires_at=time.time() - 1,
            next_send_at=0,
        )
        self.assertFalse(auth.verify_email_code("user@example.com", "123456"))


if __name__ == "__main__":
    unittest.main()
