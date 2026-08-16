"""SMTP 工具调用验证脚本

用于验证 PM Brainstorm 的 SMTP 邮件发送链路是否可正常调用。
支持以下模式：
  - --check-config       仅检查配置完整性（不发邮件）
  - --send-test <email>  发送一封测试邮件到指定邮箱
  - --health             模拟 /health 端点的服务状态检查

环境变量通过 backend/.env 自动加载（core.config 会处理）。
"""
from __future__ import annotations

import argparse
import smtplib
import ssl
import sys
import time
from email.message import EmailMessage
from pathlib import Path

# 保证从 backend 目录运行时可以导入 core 模块
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from core.auth import auth_is_configured, smtp_is_configured  # noqa: E402
from core.config import settings  # noqa: E402


def _print_section(title: str) -> None:
    print(f"\n=== {title} ===")


def _print_config() -> None:
    _print_section("SMTP 配置快照")
    print(f"  SMTP_HOST           : {settings.smtp_host}")
    print(f"  SMTP_PORT           : {settings.smtp_port}")
    print(f"  SMTP_USE_SSL        : {settings.smtp_use_ssl}")
    print(f"  SMTP_USERNAME       : {settings.smtp_username or '(空)'}")
    print(f"  SMTP_PASSWORD       : {'(已设置)' if settings.smtp_password else '(空)'}")
    print(f"  SMTP_FROM           : {settings.smtp_from or '(默认使用 SMTP_USERNAME)'}")
    print(f"  SMTP_TIMEOUT_SECONDS: {settings.smtp_timeout_seconds}")
    print(f"  AUTH_SECRET_KEY长度 : {len(settings.auth_secret_key)}")
    print(f"  smtp_is_configured(): {smtp_is_configured()}")
    print(f"  auth_is_configured(): {auth_is_configured()}")


def _build_test_message(to_email: str, code: str = "123456") -> EmailMessage:
    sender = settings.smtp_from or settings.smtp_username
    message = EmailMessage()
    message["Subject"] = "[PM Brainstorm] SMTP 工具调用验证邮件"
    message["From"] = sender
    message["To"] = to_email
    message.set_content(
        f"这是一封来自 PM Brainstorm 的 SMTP 链路验证邮件。\n\n"
        f"验证码占位：{code}\n"
        f"发送时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"如非本人操作，请忽略此邮件。"
    )
    message.add_alternative(
        f"""
        <div style="font-family:Arial,sans-serif;max-width:520px;margin:auto;padding:32px;color:#172033">
          <p style="font-size:12px;letter-spacing:.12em;color:#64748b">PM BRAINSTORM · SMTP TOOL TEST</p>
          <h1 style="font-size:22px;margin:12px 0">SMTP 工具调用验证邮件</h1>
          <p style="color:#475569">如果你收到这封邮件，说明 SMTP 调用链路正常工作。</p>
          <p style="font-size:32px;font-weight:700;letter-spacing:.28em;color:#0891b2;margin:24px 0">{code}</p>
          <p style="font-size:13px;color:#64748b">发送时间：{time.strftime('%Y-%m-%d %H:%M:%S')}</p>
        </div>
        """,
        subtype="html",
    )
    return message


def _send_via_smtp(message: EmailMessage) -> None:
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


def check_config() -> int:
    _print_config()
    _print_section("配置校验结果")
    ok = True
    if not smtp_is_configured():
        print("  [FAIL] SMTP 配置不完整（缺少 HOST/PORT/USERNAME/PASSWORD/FROM）")
        ok = False
    else:
        print("  [OK]   SMTP 配置完整")
    if not auth_is_configured():
        print("  [FAIL] AUTH_SECRET_KEY 未配置或长度不足 32 字符")
        ok = False
    else:
        print("  [OK]   AUTH_SECRET_KEY 已配置")
    print(f"\n最终结果：{'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def send_test(to_email: str) -> int:
    if not smtp_is_configured():
        print("[ERROR] SMTP 未配置，无法发送测试邮件")
        return 1
    _print_config()
    _print_section(f"发送测试邮件到 {to_email}")
    message = _build_test_message(to_email)
    try:
        start = time.time()
        _send_via_smtp(message)
        elapsed = time.time() - start
        print(f"  [OK] 邮件发送成功，耗时 {elapsed:.2f}s")
        return 0
    except smtplib.SMTPAuthenticationError as exc:
        print(f"  [FAIL] SMTP 认证失败：{exc}")
        print("  请确认 SMTP_PASSWORD 是网易邮箱的客户端授权码，而非登录密码")
        return 2
    except smtplib.SMTPException as exc:
        print(f"  [FAIL] SMTP 协议错误：{exc}")
        return 3
    except OSError as exc:
        print(f"  [FAIL] 网络或连接错误：{exc}")
        return 4


def health() -> int:
    _print_section("Health 端点模拟")
    services = {
        "llm": bool(settings.llm_api_key),
        "smtp": smtp_is_configured(),
        "auth": auth_is_configured(),
    }
    print(f"  services: {services}")
    status = "ok" if all(services.values()) else "degraded"
    print(f"  status  : {status}")
    return 0 if status == "ok" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="PM Brainstorm SMTP 工具调用验证")
    parser.add_argument("--check-config", action="store_true", help="仅检查配置完整性")
    parser.add_argument("--send-test", metavar="EMAIL", help="发送一封测试邮件")
    parser.add_argument("--health", action="store_true", help="模拟 /health 端点")
    args = parser.parse_args()

    if args.send_test:
        return send_test(args.send_test)
    if args.health:
        return health()
    return check_config()


if __name__ == "__main__":
    sys.exit(main())
