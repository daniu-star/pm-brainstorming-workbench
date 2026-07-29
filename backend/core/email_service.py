import smtplib
from email.message import EmailMessage
from html import escape

from core.config import settings


class EmailDeliveryError(RuntimeError):
    pass


def _header_value(value: str) -> str:
    return " ".join(value.splitlines()).strip()


def send_team_invitation(
    recipient: str,
    team_name: str,
    inviter_name: str,
    invitation_url: str,
) -> None:
    if not settings.smtp_host or not settings.smtp_from_email:
        raise EmailDeliveryError("SMTP 服务尚未配置")

    message = EmailMessage()
    message["Subject"] = f"加入 {_header_value(team_name)} 的产品决策空间"
    message["From"] = f"{_header_value(settings.smtp_from_name)} <{settings.smtp_from_email}>"
    message["To"] = recipient
    message.set_content(
        f"{inviter_name} 邀请你加入「{team_name}」。\n\n"
        f"打开以下链接接受邀请：\n{invitation_url}\n\n"
        "该链接具有时效性，请勿转发。"
    )
    safe_team_name = escape(team_name)
    safe_inviter_name = escape(inviter_name)
    safe_invitation_url = escape(invitation_url, quote=True)
    message.add_alternative(
        f"""
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
        """,
        subtype="html",
    )

    try:
        smtp_factory = smtplib.SMTP_SSL if settings.smtp_use_ssl else smtplib.SMTP
        with smtp_factory(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
            if settings.smtp_use_tls and not settings.smtp_use_ssl:
                smtp.starttls()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)
    except (OSError, ValueError, smtplib.SMTPException) as error:
        raise EmailDeliveryError("邀请邮件发送失败") from error
