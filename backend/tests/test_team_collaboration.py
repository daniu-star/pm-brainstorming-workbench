import json
from pathlib import Path

import pytest

from core import email_service
from db.session_store import SessionStore
from db.team_store import TeamStore


def user(token: str, nickname: str) -> dict:
    return {"user_token": token, "nickname": nickname}


def test_invitation_token_is_hashed_and_single_use(tmp_path: Path):
    store = TeamStore(str(tmp_path / "teams"))
    owner = user("owner-token", "负责人")
    team = store.create(owner, "增长产品组")

    invitation, raw_token = store.create_invitation(
        team["id"], "PM@Example.com", "member", owner["user_token"], 72
    )
    stored = json.loads((tmp_path / "teams" / f"{team['id']}.json").read_text("utf-8"))

    assert raw_token not in json.dumps(stored, ensure_ascii=False)
    assert stored["invitations"][0]["token_hash"] != raw_token
    assert invitation["email"] == "pm@example.com"
    assert store.preview_invitation(raw_token) is not None

    accepted = store.accept_invitation(raw_token, user("member-token", "产品经理"))
    assert len(accepted["members"]) == 2
    assert store.member_role(team["id"], "member-token") == "member"
    with pytest.raises(ValueError, match="失效"):
        store.accept_invitation(raw_token, user("another-token", "其他人"))


def test_tampered_invitation_and_owner_removal_are_rejected(tmp_path: Path):
    store = TeamStore(str(tmp_path / "teams"))
    owner = user("owner-token", "负责人")
    team = store.create(owner)
    _, raw_token = store.create_invitation(
        team["id"], "member@example.com", "admin", owner["user_token"], 72
    )

    assert store.preview_invitation(raw_token + "tampered") is None
    with pytest.raises(ValueError, match="负责人"):
        store.remove_member(team["id"], team["members"][0]["id"])


def test_team_chat_requires_membership_and_returns_incremental_messages(tmp_path: Path):
    store = TeamStore(str(tmp_path / "teams"))
    owner = user("owner-token", "负责人")
    team = store.create(owner)
    first = store.add_chat_message(team["id"], owner, "开始 PRD 评审")
    second = store.add_chat_message(team["id"], owner, "重点关注验收标准")

    assert store.list_chat_messages(team["id"], after=first["id"]) == [second]
    with pytest.raises(PermissionError):
        store.add_chat_message(team["id"], user("outsider", "外部用户"), "越权消息")


def test_sessions_are_shared_only_when_team_id_is_explicit(tmp_path: Path):
    sessions = SessionStore(str(tmp_path / "sessions"))
    private_session = sessions.create("历史私有项目", "owner-token")
    shared_session = sessions.create("团队项目", "owner-token", "team-1")

    assert sessions.list_by_team("team-1") == [shared_session]
    assert private_session["id"] not in {item["id"] for item in sessions.list_by_team("team-1")}


def test_smtp_ssl_delivery(monkeypatch):
    calls = {"started_tls": False, "sent": False, "logged_in": False}

    class FakeSmtp:
        def __init__(self, host, port, timeout):
            assert host == "smtp.example.com"
            assert port == 465
            assert timeout == 20

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def starttls(self):
            calls["started_tls"] = True

        def login(self, username, password):
            calls["logged_in"] = (username, password) == ("mailer", "secret")

        def send_message(self, message):
            calls["sent"] = message["To"] == "pm@example.com"

    monkeypatch.setattr(email_service.settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(email_service.settings, "smtp_port", 465)
    monkeypatch.setattr(email_service.settings, "smtp_from_email", "no-reply@example.com")
    monkeypatch.setattr(email_service.settings, "smtp_username", "mailer")
    monkeypatch.setattr(email_service.settings, "smtp_password", "secret")
    monkeypatch.setattr(email_service.settings, "smtp_use_ssl", True)
    monkeypatch.setattr(email_service.settings, "smtp_use_tls", True)
    monkeypatch.setattr(email_service.settings, "email_proxy_url", "")
    monkeypatch.setattr(email_service.settings, "email_proxy_key", "")
    monkeypatch.setattr(email_service.smtplib, "SMTP_SSL", FakeSmtp)

    email_service.send_team_invitation(
        "pm@example.com",
        "增长产品组",
        "负责人",
        "https://www.brainstorming.top/team/invite?token=test",
    )

    assert calls == {"started_tls": False, "sent": True, "logged_in": True}


def test_email_proxy_is_preferred_when_configured(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"success": True}

    def fake_post(url, headers, json, timeout):
        captured.update(
            {
                "url": url,
                "has_key": bool(headers.get("X-API-Key")),
                "recipient": json["to_email"],
                "timeout": timeout,
            }
        )
        return FakeResponse()

    monkeypatch.setattr(email_service.settings, "smtp_host", "smtp.163.com")
    monkeypatch.setattr(email_service.settings, "smtp_port", 465)
    monkeypatch.setattr(email_service.settings, "smtp_username", "sender@example.com")
    monkeypatch.setattr(email_service.settings, "smtp_password", "secret")
    monkeypatch.setattr(email_service.settings, "smtp_from_email", "sender@example.com")
    monkeypatch.setattr(email_service.settings, "email_proxy_url", "https://mail.example.com/api/team")
    monkeypatch.setattr(email_service.settings, "email_proxy_key", "proxy-secret")
    monkeypatch.setattr(email_service.requests, "post", fake_post)

    email_service.send_team_invitation(
        "pm@example.com",
        "增长产品组",
        "负责人",
        "https://www.brainstorming.top/team/invite?token=test",
    )

    assert captured == {
        "url": "https://mail.example.com/api/team",
        "has_key": True,
        "recipient": "pm@example.com",
        "timeout": 25,
    }
