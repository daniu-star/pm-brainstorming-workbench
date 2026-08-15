import smtplib
from email.message import EmailMessage
from html import escape

import requests

from core.config import settings


class EmailDeliveryError(RuntimeError):
    pass


def _header_value(value: str) -> str:
    return " ".join(value.splitlines()).strip()


def _deliver_message(
    message: EmailMessage,
    recipient: str,
    subject: str,
    plain_text: str,
    html_content: str,
    failure_message: str,
) -> None:
    if settings.email_proxy_url and settings.email_proxy_key:
        try:
            _send_with_proxy(recipient, subject, plain_text, html_content)
            return
        except (EmailDeliveryError, requests.RequestException, ValueError):
            pass
    try:
        smtp_factory = smtplib.SMTP_SSL if settings.smtp_use_ssl else smtplib.SMTP
        with smtp_factory(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
            if settings.smtp_use_tls and not settings.smtp_use_ssl:
                smtp.starttls()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)
    except (OSError, ValueError, smtplib.SMTPException) as error:
        raise EmailDeliveryError(failure_message) from error


def _send_with_proxy(
    recipient: str,
    subject: str,
    plain_text: str,
    html_content: str,
) -> None:
    response = requests.post(
        settings.email_proxy_url,
        headers={"X-API-Key": settings.email_proxy_key},
        json={
            "to_email": recipient,
            "subject": subject,
            "text": plain_text,
            "html": html_content,
            "smtp_host": settings.smtp_host,
            "smtp_port": str(settings.smtp_port),
            "smtp_user": settings.smtp_username,
            "smtp_password": settings.smtp_password,
            "smtp_from_name": settings.smtp_from_name,
        },
        timeout=25,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("success"):
        raise EmailDeliveryError("邮件代理投递失败")


def send_team_invitation(
    recipient: str,
    team_name: str,
    inviter_name: str,
    invitation_url: str,
) -> None:
    if not settings.smtp_host or not settings.smtp_from_email:
        raise EmailDeliveryError("SMTP 服务尚未配置")

    subject = f"加入 {_header_value(team_name)} 的产品决策空间"
    plain_text = (
        f"{inviter_name} 邀请你加入「{team_name}」。\n\n"
        f"打开以下链接接受邀请：\n{invitation_url}\n\n"
        "该链接具有时效性，请勿转发。"
    )
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = f"{_header_value(settings.smtp_from_name)} <{settings.smtp_from_email}>"
    message["To"] = recipient
    message.set_content(plain_text)
    safe_team_name = escape(team_name)
    safe_inviter_name = escape(inviter_name)
    safe_invitation_url = escape(invitation_url, quote=True)
    html_content = f"""
        <div style="font-family:Microsoft YaHei,Arial,sans-serif;background:#07111f;padding:32px;color:#dbeafe">
          <div style="max-width:560px;margin:auto;border:1px solid #164e63;border-radius:20px;padding:28px;background:#0b1728">
            <p style="color:#67e8f9;font-size:12px;letter-spacing:2px">PM BRAINSTORM · TEAM INVITE</p>
            <h1 style="font-size:24px;color:#ffffff">加入 {safe_team_name}</h1>
            <p style="line-height:1.8;color:#a5b4c7">{safe_inviter_name} 邀请你进入团队产品决策空间，共同查看 PRD、评审决策并参与实时讨论。</p>
            <p style="margin:28px 0">
              <a href="{safe_invitation_url}" style="display:inline-block;padding:12px 22px;border-radius:12px;background:#22d3ee;color:#082f49;text-decoration:none;font-weight:700">接受团队邀请</a>
            </p>
            <p style="font-size:12px;color:#64748b">该链接具有时效性，请勿转发。</p>
          </div>
        </div>
        """
    message.add_alternative(html_content, subtype="html")

    _deliver_message(
        message,
        recipient,
        subject,
        plain_text,
        html_content,
        "邀请邮件发送失败",
    )


def send_login_code(recipient: str, code: str) -> None:
    if not settings.smtp_host or not settings.smtp_from_email:
        raise EmailDeliveryError("SMTP 服务尚未配置")

    subject = "PM Brainstorm 登录验证码"
    plain_text = (
        f"你的登录验证码是：{code}\n\n"
        f"验证码在 {settings.email_code_expire_minutes} 分钟内有效。"
        "如非本人操作，请忽略此邮件。"
    )
    safe_code = escape(code)
    html_content = f"""
        <div style="font-family:Microsoft YaHei,Arial,sans-serif;background:#07111f;padding:32px;color:#dbeafe">
          <div style="max-width:520px;margin:auto;border:1px solid #164e63;border-radius:20px;padding:28px;background:#0b1728">
            <p style="color:#67e8f9;font-size:12px;letter-spacing:2px">PM BRAINSTORM · SECURE ACCESS</p>
            <h1 style="font-size:24px;color:#ffffff">邮箱登录验证码</h1>
            <p style="line-height:1.8;color:#a5b4c7">请在登录页面输入以下六位验证码：</p>
            <p style="font-size:34px;font-weight:700;letter-spacing:8px;color:#67e8f9;margin:26px 0">{safe_code}</p>
            <p style="font-size:12px;color:#64748b">验证码在 {settings.email_code_expire_minutes} 分钟内有效。如非本人操作，请忽略此邮件。</p>
          </div>
        </div>
        """
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = f"{_header_value(settings.smtp_from_name)} <{settings.smtp_from_email}>"
    message["To"] = recipient
    message.set_content(plain_text)
    message.add_alternative(html_content, subtype="html")
    _deliver_message(
        message,
        recipient,
        subject,
        plain_text,
        html_content,
        "验证码邮件发送失败",
    )
