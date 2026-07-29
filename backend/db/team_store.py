import hashlib
import json
import os
import secrets
import threading
import uuid
from datetime import datetime, timedelta, timezone


TEAM_DATA_DIR = os.getenv("TEAM_DATA_DIR", "./data/teams")
MAX_CHAT_MESSAGES = 1000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_id(value: str) -> str:
    if not value or ".." in value or "/" in value or "\\" in value:
        raise ValueError(f"Invalid ID: {value}")
    return value


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class TeamStore:
    def __init__(self, data_dir: str | None = None):
        self.data_dir = data_dir or TEAM_DATA_DIR
        self._lock = threading.RLock()
        os.makedirs(self.data_dir, exist_ok=True)

    def create(self, owner: dict, name: str = "") -> dict:
        owner_id = owner["user_token"]
        team_id = f"team_{uuid.uuid4().hex[:12]}"
        now = _now()
        team = {
            "id": team_id,
            "name": name.strip() or f"{owner.get('nickname') or '产品团队'}的工作空间",
            "owner_user_token": owner_id,
            "members": [
                {
                    "id": f"member_{uuid.uuid4().hex[:10]}",
                    "user_token": owner_id,
                    "email": "",
                    "nickname": owner.get("nickname") or "团队负责人",
                    "role": "owner",
                    "status": "active",
                    "joined_at": now,
                }
            ],
            "invitations": [],
            "chat_messages": [],
            "created_at": now,
            "updated_at": now,
        }
        self._save(team)
        return team

    def get(self, team_id: str) -> dict | None:
        try:
            _validate_id(team_id)
        except ValueError:
            return None
        path = os.path.join(self.data_dir, f"{team_id}.json")
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)

    def list_for_user(self, user_token: str) -> list[dict]:
        teams = []
        for filename in os.listdir(self.data_dir):
            if not filename.endswith(".json"):
                continue
            with open(os.path.join(self.data_dir, filename), "r", encoding="utf-8") as file:
                team = json.load(file)
            if self._member(team, user_token):
                teams.append(team)
        teams.sort(key=lambda item: item.get("created_at", ""))
        return teams

    def is_member(self, team_id: str, user_token: str) -> bool:
        team = self.get(team_id)
        return bool(team and self._member(team, user_token))

    def member_role(self, team_id: str, user_token: str) -> str | None:
        team = self.get(team_id)
        member = self._member(team, user_token) if team else None
        return member.get("role") if member else None

    def rename(self, team_id: str, name: str) -> dict:
        with self._lock:
            team = self._require(team_id)
            team["name"] = name.strip()
            team["updated_at"] = _now()
            self._save(team)
            return team

    def create_invitation(
        self,
        team_id: str,
        email: str,
        role: str,
        invited_by: str,
        expires_hours: int,
    ) -> tuple[dict, str]:
        with self._lock:
            team = self._require(team_id)
            normalized_email = email.strip().lower()
            for member in team.get("members", []):
                if member.get("email", "").lower() == normalized_email and member.get("status") == "active":
                    raise ValueError("该邮箱已经是团队成员")
            for invitation in team.get("invitations", []):
                if invitation.get("email") == normalized_email and invitation.get("status") == "pending":
                    invitation["status"] = "superseded"

            raw_token = secrets.token_urlsafe(32)
            now = datetime.now(timezone.utc)
            invitation = {
                "id": f"invite_{uuid.uuid4().hex[:10]}",
                "email": normalized_email,
                "role": role,
                "token_hash": _token_hash(raw_token),
                "status": "pending",
                "delivery_status": "pending",
                "invited_by": invited_by,
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(hours=expires_hours)).isoformat(),
                "accepted_at": None,
                "accepted_by": None,
            }
            team.setdefault("invitations", []).insert(0, invitation)
            team["updated_at"] = _now()
            self._save(team)
            return dict(invitation), raw_token

    def update_invitation_delivery(self, team_id: str, invitation_id: str, status: str) -> None:
        with self._lock:
            team = self._require(team_id)
            invitation = self._find_invitation(team, invitation_id)
            if invitation is None:
                raise ValueError("邀请不存在")
            invitation["delivery_status"] = status
            team["updated_at"] = _now()
            self._save(team)

    def preview_invitation(self, token: str) -> tuple[dict, dict] | None:
        match = self._find_invitation_by_token(token)
        if match is None:
            return None
        team, invitation = match
        if not self._is_invitation_valid(invitation):
            return None
        return team, invitation

    def accept_invitation(self, token: str, user: dict) -> dict:
        with self._lock:
            match = self._find_invitation_by_token(token)
            if match is None:
                raise ValueError("邀请链接无效")
            team, invitation = match
            if not self._is_invitation_valid(invitation):
                raise ValueError("邀请链接已失效或已被使用")

            user_token = user["user_token"]
            existing = self._member(team, user_token)
            if existing is None:
                team.setdefault("members", []).append(
                    {
                        "id": f"member_{uuid.uuid4().hex[:10]}",
                        "user_token": user_token,
                        "email": invitation["email"],
                        "nickname": user.get("nickname") or invitation["email"].split("@", 1)[0],
                        "role": invitation["role"],
                        "status": "active",
                        "joined_at": _now(),
                    }
                )
            invitation["status"] = "accepted"
            invitation["accepted_at"] = _now()
            invitation["accepted_by"] = user_token
            team["updated_at"] = _now()
            self._save(team)
            return team

    def remove_member(self, team_id: str, member_id: str) -> tuple[dict, str]:
        with self._lock:
            team = self._require(team_id)
            member = next(
                (item for item in team.get("members", []) if item.get("id") == member_id),
                None,
            )
            if member is None:
                raise ValueError("团队成员不存在")
            user_token = member.get("user_token", "")
            if user_token == team.get("owner_user_token"):
                raise ValueError("不能移除团队负责人")
            team["members"] = [
                item for item in team.get("members", []) if item.get("id") != member_id
            ]
            team["updated_at"] = _now()
            self._save(team)
            return team, user_token

    def add_chat_message(self, team_id: str, user: dict, content: str) -> dict:
        with self._lock:
            team = self._require(team_id)
            member = self._member(team, user["user_token"])
            if member is None:
                raise PermissionError("无权访问该团队聊天室")
            message = {
                "id": f"chat_{uuid.uuid4().hex[:12]}",
                "author_user_token": user["user_token"],
                "author_name": member.get("nickname") or user.get("nickname") or "团队成员",
                "content": content.strip(),
                "created_at": _now(),
            }
            messages = team.setdefault("chat_messages", [])
            messages.append(message)
            if len(messages) > MAX_CHAT_MESSAGES:
                team["chat_messages"] = messages[-MAX_CHAT_MESSAGES:]
            team["updated_at"] = _now()
            self._save(team)
            return message

    def list_chat_messages(self, team_id: str, limit: int = 100, after: str = "") -> list[dict]:
        team = self._require(team_id)
        messages = team.get("chat_messages", [])
        if after:
            index = next((idx for idx, item in enumerate(messages) if item.get("id") == after), -1)
            if index >= 0:
                messages = messages[index + 1 :]
        return messages[-limit:]

    def _find_invitation_by_token(self, token: str) -> tuple[dict, dict] | None:
        hashed = _token_hash(token)
        for filename in os.listdir(self.data_dir):
            if not filename.endswith(".json"):
                continue
            with open(os.path.join(self.data_dir, filename), "r", encoding="utf-8") as file:
                team = json.load(file)
            for invitation in team.get("invitations", []):
                if secrets.compare_digest(invitation.get("token_hash", ""), hashed):
                    return team, invitation
        return None

    @staticmethod
    def _is_invitation_valid(invitation: dict) -> bool:
        if invitation.get("status") != "pending":
            return False
        try:
            expires_at = datetime.fromisoformat(invitation["expires_at"])
        except (KeyError, ValueError):
            return False
        return expires_at > datetime.now(timezone.utc)

    @staticmethod
    def _find_invitation(team: dict, invitation_id: str) -> dict | None:
        return next(
            (item for item in team.get("invitations", []) if item.get("id") == invitation_id),
            None,
        )

    @staticmethod
    def _member(team: dict | None, user_token: str) -> dict | None:
        if not team:
            return None
        return next(
            (
                member
                for member in team.get("members", [])
                if member.get("user_token") == user_token and member.get("status") == "active"
            ),
            None,
        )

    def _require(self, team_id: str) -> dict:
        team = self.get(team_id)
        if team is None:
            raise ValueError("团队不存在")
        return team

    def _save(self, team: dict) -> None:
        _validate_id(team["id"])
        path = os.path.join(self.data_dir, f"{team['id']}.json")
        temp_path = f"{path}.{uuid.uuid4().hex}.tmp"
        with open(temp_path, "w", encoding="utf-8") as file:
            json.dump(team, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, path)


team_store = TeamStore()
