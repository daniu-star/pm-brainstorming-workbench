"""验证码「邮件投递失败 → 写日志兜底」测试（第 40 批）。

线上 Space 封了出站 SMTP 端口时，``/api/auth/email/code`` 会卡到超时（经 Vercel
反代变成 502），整个登录入口不可用。打开 ``AUTH_CODE_LOG_FALLBACK`` 后：
投递失败立刻把验证码写进服务日志并返回 ``delivery="logs"``，平台所有者可以
从日志里读到验证码转达给用户——入口可用、验证码仍然只能被正确的人拿到。
"""

from __future__ import annotations

import socket
import unittest
import uuid
from unittest.mock import patch

from core import auth
from core.config import settings


class AuthCodeLogFallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original = {
            name: getattr(settings, name)
            for name in (
                "auth_secret_key",
                "smtp_username",
                "smtp_password",
                "smtp_from",
                "smtp_use_ssl",
                "smtp_host",
                "smtp_port",
                "email_proxy_url",
                "email_proxy_key",
                "auth_code_log_fallback",
            )
        }
        settings.auth_secret_key = "test-secret-key-that-is-longer-than-32-characters"
        settings.smtp_host = "smtp.163.com"
        settings.smtp_port = 465
        settings.smtp_username = "sender@163.com"
        settings.smtp_password = "smtp-authorization-code"
        settings.smtp_from = "sender@163.com"
        settings.smtp_use_ssl = True
        settings.email_proxy_url = ""
        settings.email_proxy_key = ""
        settings.auth_code_log_fallback = True
        auth._verification_codes.clear()
        self.email = f"fallback-{uuid.uuid4().hex[:12]}@example.com"

    def tearDown(self) -> None:
        auth._verification_codes.clear()
        for name, value in self.original.items():
            setattr(settings, name, value)

    def _capture_logged_code(self) -> str:
        with self.assertLogs("core.auth", level="WARNING") as captured:
            retry_after, delivery = auth.issue_verification_code_with_delivery(self.email)
        self.assertEqual(retry_after, 60)
        self.assertEqual(delivery, "logs")
        joined = "\n".join(captured.output)
        self.assertIn("event=auth.code_log_fallback", joined)
        marker = "code="
        index = joined.rindex(marker) + len(marker)
        code = joined[index : index + 6]
        self.assertTrue(code.isdigit(), f"日志里应带 6 位验证码，实际：{joined!r}")
        return code

    def test_smtp_failure_falls_back_to_log_and_code_still_works(self) -> None:
        """SMTP 连不上时写日志，且日志里的验证码能正常登录（不是废码）。"""
        with patch(
            "core.auth.smtplib.SMTP_SSL",
            side_effect=ConnectionRefusedError("容器平台封禁出站 465"),
        ):
            code = self._capture_logged_code()

        self.assertTrue(auth.verify_email_code(self.email, code))

    def test_socket_timeout_also_falls_back(self) -> None:
        """超时（最常见表现）同样兜底，不能只认 ConnectionRefused。"""
        with patch("core.auth.smtplib.SMTP_SSL", side_effect=socket.timeout("timed out")):
            code = self._capture_logged_code()

        self.assertTrue(auth.verify_email_code(self.email, code))

    def test_proxy_failure_then_smtp_failure_also_falls_back(self) -> None:
        """代理与直连都不可用时仍要给出可用验证码。"""
        settings.email_proxy_url = "https://mail-proxy.example.com/send"
        with patch(
            "core.auth.urllib.request.urlopen",
            side_effect=OSError("proxy down"),
        ), patch("core.auth.smtplib.SMTP_SSL", side_effect=ConnectionRefusedError("blocked")):
            code = self._capture_logged_code()

        self.assertTrue(auth.verify_email_code(self.email, code))

    def test_fallback_disabled_keeps_raising_and_invalidates_code(self) -> None:
        """关闭开关时保持旧行为：抛错、作废验证码、接口返回错误码。"""
        settings.auth_code_log_fallback = False
        with patch("core.auth.smtplib.SMTP_SSL", side_effect=ConnectionRefusedError("blocked")):
            with self.assertRaises(ConnectionRefusedError):
                auth.issue_verification_code_with_delivery(self.email)

        # 失败时验证码必须作废，避免「发出去了但其实没送达」的悬空记录。
        self.assertFalse(auth.verify_email_code(self.email, "123456"))

    def test_success_path_reports_smtp_delivery(self) -> None:
        """正常发送时 delivery 必须是 smtp，前端据此显示常规提示。"""

        class FakeSmtp:
            def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):  # noqa: ANN002, ANN003
                return False

            def login(self, username, password):  # noqa: ANN001
                pass

            def send_message(self, message):  # noqa: ANN001
                pass

        with patch("core.auth.smtplib.SMTP_SSL", FakeSmtp):
            retry_after, delivery = auth.issue_verification_code_with_delivery(self.email)

        self.assertEqual((retry_after, delivery), (60, "smtp"))


if __name__ == "__main__":
    unittest.main()
