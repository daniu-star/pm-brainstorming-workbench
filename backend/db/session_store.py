import json
import os
import uuid
from datetime import datetime
from typing import List, Optional

SESSION_DATA_DIR = os.getenv("SESSION_DATA_DIR", "./data/sessions")
INTERVIEW_SESSION_DATA_DIR = os.getenv("INTERVIEW_SESSION_DATA_DIR", "./data/interview_sessions")


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
            "decision_hub": {
                "evidence": [],
                "initiatives": [],
                "experiments": [],
                "roadmap_items": [],
                "prd_versions": [],
                "review_space": {
                    "comments": [],
                    "votes": [],
                    "approvals": [],
                    "audit_log": [],
                    "share_token": "",
                    "share_enabled": False,
                },
                "agent_config": {
                    "template": "saas",
                    "company_knowledge": "",
                    "audit_rules": [],
                    "agents": [],
                },
                "metric_reviews": [],
                "updated_at": datetime.now().isoformat(),
            },
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

    def list_all_raw(self) -> List[dict]:
        """Internal lookup for deliberately shared, read-only session views."""
        sessions = []
        for fname in os.listdir(self.data_dir):
            if fname.endswith(".json"):
                with open(os.path.join(self.data_dir, fname), "r", encoding="utf-8") as f:
                    sessions.append(json.load(f))
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


class InterviewSessionStore:
    """独立面试空间存储，与脑暴会话分离，避免污染主会话状态。"""

    def __init__(self):
        self.data_dir = INTERVIEW_SESSION_DATA_DIR
        os.makedirs(self.data_dir, exist_ok=True)

    def create(
        self,
        parent_session_id: str,
        problem_statement: str,
        canvas_tree: Optional[dict] = None,
        user_token: str = "",
    ) -> dict:
        interview_id = "iv_" + uuid.uuid4().hex[:10]
        space = {
            "id": interview_id,
            "parent_session_id": parent_session_id,
            "problem_statement": problem_statement,
            "canvas_tree": canvas_tree,
            "messages": [],
            "dimensions_covered": [],
            "question_count": 0,
            "status": "active",
            "created_at": datetime.now().isoformat(),
            "user_token": user_token,
        }
        self._save(space)
        return space

    def get(self, interview_id: str, user_token: str | None = None) -> Optional[dict]:
        try:
            _validate_id(interview_id)
        except ValueError:
            return None
        path = os.path.join(self.data_dir, f"{interview_id}.json")
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            space = json.load(f)
        if user_token is not None and space.get("user_token") != user_token:
            return None
        return space

    def update(self, interview_id: str, updates: dict):
        space = self.get(interview_id)
        if space is None:
            raise ValueError(f"面试空间 {interview_id} 未找到")
        space.update(updates)
        self._save(space)

    def add_message(self, interview_id: str, role: str, content: str, role_name: str = None):
        space = self.get(interview_id)
        if space is None:
            raise ValueError(f"面试空间 {interview_id} 未找到")
        msg = {"role": role, "content": content, "timestamp": datetime.now().isoformat()}
        if role_name:
            msg["role_name"] = role_name
        space["messages"].append(msg)
        self._save(space)

    def get_recent_messages(self, interview_id: str, n: int = 20) -> List[dict]:
        space = self.get(interview_id)
        if space is None:
            return []
        return space["messages"][-n:]

    def list_by_parent(self, parent_session_id: str) -> List[dict]:
        try:
            _validate_id(parent_session_id)
        except ValueError:
            return []
        results = []
        for fname in os.listdir(self.data_dir):
            if fname.endswith(".json"):
                path = os.path.join(self.data_dir, fname)
                with open(path, "r", encoding="utf-8") as f:
                    s = json.load(f)
                if s.get("parent_session_id") == parent_session_id:
                    results.append({
                        "id": s["id"],
                        "status": s.get("status", "active"),
                        "question_count": s.get("question_count", 0),
                        "dimensions_covered": s.get("dimensions_covered", []),
                        "created_at": s.get("created_at", ""),
                    })
        results.sort(key=lambda x: x["created_at"], reverse=True)
        return results

    def delete(self, interview_id: str, user_token: str | None = None) -> bool:
        try:
            _validate_id(interview_id)
        except ValueError:
            return False
        path = os.path.join(self.data_dir, f"{interview_id}.json")
        if not os.path.exists(path):
            return False
        if user_token is not None:
            with open(path, "r", encoding="utf-8") as f:
                space = json.load(f)
            if space.get("user_token") != user_token:
                return False
        os.remove(path)
        return True

    def _save(self, space: dict):
        _validate_id(space["id"])
        path = os.path.join(self.data_dir, f"{space['id']}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(space, f, ensure_ascii=False, indent=2)


interview_session_store = InterviewSessionStore()
