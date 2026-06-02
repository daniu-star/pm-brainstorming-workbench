import json
import os
import uuid
from datetime import datetime
from typing import List, Optional

SESSION_DATA_DIR = os.getenv("SESSION_DATA_DIR", "./data/sessions")


def _validate_id(value: str) -> str:
    if not value or ".." in value or "/" in value or "\\" in value:
        raise ValueError(f"Invalid ID: {value}")
    return value


class SessionStore:
    def __init__(self):
        self.data_dir = SESSION_DATA_DIR
        os.makedirs(self.data_dir, exist_ok=True)

    def create(self, problem_statement: str, user_token: str = "") -> dict:
        session_id = uuid.uuid4().hex[:12]
        session = {
            "id": session_id,
            "problem_statement": problem_statement,
            "phase": "brainstorm",
            "messages": [],
            "canvas_tree": None,
            "interview_dimensions_covered": [],
            "interview_question_count": 0,
            "created_at": datetime.now().isoformat(),
            "user_token": user_token,
        }
        self._save(session)
        return session

    def get(self, session_id: str, user_token: str | None = None) -> Optional[dict]:
        try:
            _validate_id(session_id)
        except ValueError:
            return None
        path = os.path.join(self.data_dir, f"{session_id}.json")
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            session = json.load(f)
        if user_token is not None and session.get("user_token") != user_token:
            return None
        return session

    def update(self, session_id: str, updates: dict):
        session = self.get(session_id)
        if session is None:
            raise ValueError(f"会话 {session_id} 未找到")
        session.update(updates)
        self._save(session)

    def add_message(self, session_id: str, role: str, content: str, role_name: str = None):
        session = self.get(session_id)
        if session is None:
            raise ValueError(f"会话 {session_id} 未找到")
        msg = {"role": role, "content": content, "timestamp": datetime.now().isoformat()}
        if role_name:
            msg["role_name"] = role_name
        session["messages"].append(msg)
        self._save(session)

    def get_recent_messages(self, session_id: str, n: int = 20) -> List[dict]:
        session = self.get(session_id)
        if session is None:
            return []
        return session["messages"][-n:]

    def list_sessions(self, user_token: str | None = None) -> List[dict]:
        sessions = []
        for fname in os.listdir(self.data_dir):
            if fname.endswith(".json"):
                path = os.path.join(self.data_dir, fname)
                with open(path, "r", encoding="utf-8") as f:
                    s = json.load(f)
                if user_token is not None and s.get("user_token") != user_token:
                    continue
                sessions.append({
                    "id": s["id"],
                    "problem_statement": s["problem_statement"],
                    "phase": s["phase"],
                    "message_count": len(s["messages"]),
                    "created_at": s["created_at"],
                })
        sessions.sort(key=lambda s: s["created_at"], reverse=True)
        return sessions

    def delete(self, session_id: str, user_token: str | None = None) -> bool:
        try:
            _validate_id(session_id)
        except ValueError:
            return False
        path = os.path.join(self.data_dir, f"{session_id}.json")
        if not os.path.exists(path):
            return False
        if user_token is not None:
            with open(path, "r", encoding="utf-8") as f:
                session = json.load(f)
            if session.get("user_token") != user_token:
                return False
        os.remove(path)
        return True

    def migrate_add_user_token(self):
        for fname in os.listdir(self.data_dir):
            if fname.endswith(".json"):
                path = os.path.join(self.data_dir, fname)
                with open(path, "r", encoding="utf-8") as f:
                    session = json.load(f)
                if "user_token" not in session:
                    session["user_token"] = ""
                    self._save(session)

    def _save(self, session: dict):
        _validate_id(session['id'])
        path = os.path.join(self.data_dir, f"{session['id']}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(session, f, ensure_ascii=False, indent=2)


session_store = SessionStore()
