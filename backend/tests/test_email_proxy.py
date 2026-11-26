"""邮箱验证码投递的部署适配测试（第 40 批）。

容器平台（Hugging Face Space 等）常封禁出站 25/465/587，直连 SMTP 会一直超时。
因此 ``_send_verification_email`` 支持先走 HTTP 代理、失败再回落直连。
这两条路径都不能在 CI 里真连网，用替身验证「选哪条路」和「失败必须回落」。

注意：验证码与重发限流落在共享 SQLite（不是进程内存），同一邮箱 60 秒内连发会
被 ``AuthRateLimitError`` 挡住，所以每个用例用独立邮箱。
"""

from __future__ import annotations

import io
import json
import unittest
import uuid
from unittest.mock import patch

from core import auth
from core.config import settings


class _FakeResponse(io.BytesIO):
    """冒充 urllib 的返回值（支持 with 语句）。"""

    def __enter__(self):
        return self

    def __exit__(self, *exc):  # noqa: ANN002, ANN003
        self.close()
        return False


def _fake_smtp_class(calls: list[str], *, fail: bool = False):
    """造一个假 SMTP 类，把调用轨迹记进 ``calls``。"""

    class FakeSmtp:
        def __init__(self, host, port, timeout=None, context=None):  # noqa: ANN001
            if fail:
                raise AssertionError(f"不该直连 SMTP: {host}:{port}")
            calls.append(f"{host}:{port}")

        def __enter__(self):
            return self

        def __exit__(self, *exc):  # noqa: ANN002, ANN003
            return False

        def ehlo(self):
            calls.append("ehlo")

        def starttls(self, context=None):  # noqa: ANN001
            calls.append("starttls")

        def login(self, username, password):  # noqa: ANN001
            calls.append(f"login:{username}")

        def send_message(self, message):  # noqa: ANN001
            calls.append(f"send:{message['To']}")

    return FakeSmtp


class EmailProxyDeliveryTests(unittest.TestCase):
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
        auth._verification_codes.clear()
        self.email = f"proxy-{uuid.uuid4().hex[:12]}@example.com"

    def tearDown(self) -> None:
        auth._verification_codes.clear()
        for name, value in self.original.items():
            setattr(settings, name, value)

    def test_proxy_success_path_skips_smtp_entirely(self) -> None:
        """代理成功时不应再连 SMTP（容器里连不通，白等一个超时）。"""
        settings.email_proxy_url = "https://mail-proxy.example.com/send"
        settings.email_proxy_key = "proxy-key"
        captured: dict[str, object] = {}

        def fake_urlopen(request, timeout=None):  # noqa: ANN001
            captured["url"] = request.full_url
            captured["headers"] = dict(request.headers)
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return _FakeResponse(json.dumps({"success": True}).encode("utf-8"))

        smtp_calls: list[str] = []
        with patch("core.auth.urllib.request.urlopen", side_effect=fake_urlopen), patch(
            "core.auth.smtplib.SMTP_SSL", _fake_smtp_class(smtp_calls, fail=True)
        ):
            retry_after = auth.issue_verification_code(self.email)

        self.assertEqual(retry_after, 60)
        self.assertEqual(smtp_calls, [], "代理成功后不应触碰 SMTP")
        self.assertEqual(captured["url"], "https://mail-proxy.example.com/send")
        self.assertEqual(captured["headers"].get("X-api-key"), "proxy-key")
        payload = captured["payload"]
        self.assertEqual(payload["to_email"], self.email)
        self.assertIn("登录验证码", payload["subject"])
        self.assertIn("PM BRAINSTORM", payload["html"])
        self.assertTrue(payload["text"])
        # 代理需要凭据去投递，因此 SMTP 配置随请求一起发给代理。
        self.assertEqual(payload["smtp_host"], "smtp.163.com")
        self.assertEqual(payload["smtp_user"], "sender@163.com")

    def test_proxy_failure_falls_back_to_direct_smtp(self) -> None:
        """代理返回 success=false 时必须回落直连，否则用户收不到验证码。"""
        settings.email_proxy_url = "https://mail-proxy.example.com/send"
        settings.email_proxy_key = "proxy-key"
        calls: list[str] = []

        with patch(
            "core.auth.urllib.request.urlopen",
            return_value=_FakeResponse(json.dumps({"success": False}).encode("utf-8")),
        ), patch("core.auth.smtplib.SMTP_SSL", _fake_smtp_class(calls)):
            auth.issue_verification_code(self.email)

        self.assertIn("smtp.163.com:465", calls)
        self.assertIn("login:sender@163.com", calls)
        self.assertIn(f"send:{self.email}", calls)

    def test_proxy_network_error_falls_back_to_direct_smtp(self) -> None:
        """代理抛异常（DNS/超时/500）同样回落，不能把登录直接打挂。"""
        settings.email_proxy_url = "https://mail-proxy.example.com/send"
        calls: list[str] = []

        with patch(
            "core.auth.urllib.request.urlopen",
            side_effect=OSError("connection refused"),
        ), patch("core.auth.smtplib.SMTP_SSL", _fake_smtp_class(calls)):
            auth.issue_verification_code(self.email)

        self.assertEqual(calls[-1], f"send:{self.email}")

    def test_no_proxy_configured_uses_direct_smtp(self) -> None:
        """未配置代理（本地 start.bat 默认）时行为与之前完全一致。"""
        calls: list[str] = []

        with patch(
            "core.auth.urllib.request.urlopen",
            side_effect=AssertionError("未配置代理时不该发起 HTTP"),
        ), patch("core.auth.smtplib.SMTP_SSL", _fake_smtp_class(calls)):
            auth.issue_verification_code(self.email)

        self.assertEqual(calls, ["smtp.163.com:465", "login:sender@163.com", f"send:{self.email}"])

    def test_plain_smtp_uses_starttls_when_ssl_disabled(self) -> None:
        """SMTP_USE_SSL=false 时应走 SMTP + starttls（V3 老配置的等价行为）。"""
        settings.smtp_use_ssl = False
        calls: list[str] = []

        with patch("core.auth.smtplib.SMTP", _fake_smtp_class(calls)):
            auth.issue_verification_code(self.email)

        self.assertIn("starttls", calls)
        self.assertEqual(calls[-1], f"send:{self.email}")


if __name__ == "__main__":
    unittest.main()
