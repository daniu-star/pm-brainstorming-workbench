import json
import os
import threading
import uuid
from datetime import datetime
from typing import List, Optional

SESSION_DATA_DIR = os.getenv("SESSION_DATA_DIR", "./data/sessions")
INTERVIEW_SESSION_DATA_DIR = os.getenv("INTERVIEW_SESSION_DATA_DIR", "./data/interview_sessions")
SESSION_PHASES = {
    "draft",
    "clarify",
    "brainstorm",
    "audit",
    "decision_ready",
    "completed",
    "archived",
}

CLARIFICATION_FIELD_ORDER = [
    "target_user",
    "current_alternative",
    "product_form",
    "success_metric",
    "constraints",
]


def _new_clarification_state(problem_statement: str) -> dict:
    now = datetime.now().isoformat()
    return {
        "status": "collecting",
        "current_field": CLARIFICATION_FIELD_ORDER[0],
        "asked_fields": [],
        "answered_fields": ["core_problem"] if problem_statement.strip() else [],
        "fields": {
            "target_user": "",
            "core_problem": problem_statement.strip(),
            "current_alternative": "",
            "product_form": "",
            "success_metric": "",
            "constraints": "",
        },
        "updated_at": now,
    }


def _validate_id(value: str) -> str:
    if not value or ".." in value or "/" in value or "\\" in value:
        raise ValueError(f"Invalid ID: {value}")
    return value


class SessionStore:
    def __init__(self, data_dir: str | None = None):
        self.data_dir = data_dir or SESSION_DATA_DIR
        self._lock = threading.RLock()
        os.makedirs(self.data_dir, exist_ok=True)

    def create(self, problem_statement: str, user_token: str = "", team_id: str = "") -> dict:
        session_id = uuid.uuid4().hex[:12]
        session = {
            "id": session_id,
            "problem_statement": problem_statement,
            "phase": "clarify",
            "phase_history": [
                {
                    "from": None,
                    "to": "clarify",
                    "reason": "session_created",
                    "timestamp": datetime.now().isoformat(),
                }
            ],
            "clarification_state": _new_clarification_state(problem_statement),
            "messages": [],
            "canvas_tree": None,
            "canvas_status": "idle",
            "canvas_version": 0,
            "canvas_last_event_id": None,
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
            "team_id": team_id,
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
        with self._lock:
            session = self.get(session_id)
            if session is None:
                raise ValueError(f"会话 {session_id} 未找到")
            session.update(updates)
            self._save(session)

    def transition_phase(self, session_id: str, phase: str, reason: str) -> dict:
        if phase not in SESSION_PHASES:
            raise ValueError(f"无效会话阶段: {phase}")
        with self._lock:
            session = self.get(session_id)
            if session is None:
                raise ValueError(f"会话 {session_id} 未找到")
            previous = session.get("phase", "draft")
            if previous != phase:
                session["phase"] = phase
                session.setdefault("phase_history", []).append(
                    {
                        "from": previous,
                        "to": phase,
                        "reason": reason,
                        "timestamp": datetime.now().isoformat(),
                    }
                )
                self._save(session)
            return session

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        role_name: str = None,
        stage: str = None,
        round_id: str = None,
        audit_run_id: str = None,
        agent_role: str = None,
    ):
        with self._lock:
            session = self.get(session_id)
            if session is None:
                raise ValueError(f"会话 {session_id} 未找到")
            msg = {
                "id": uuid.uuid4().hex[:16],
                "role": role,
                "content": content,
                "timestamp": datetime.now().isoformat(),
                "stage": stage or session.get("phase", "draft"),
            }
            if role_name:
                msg["role_name"] = role_name
            if round_id:
                msg["round_id"] = round_id
            if audit_run_id:
                msg["audit_run_id"] = audit_run_id
            if agent_role:
                msg["agent_role"] = agent_role
            session["messages"].append(msg)
            self._save(session)
            return msg

    def get_recent_messages(
        self,
        session_id: str,
        n: int = 20,
        max_chars: int = 30000,
    ) -> List[dict]:
        session = self.get(session_id)
        if session is None:
            return []
        selected = []
        remaining = max_chars
        for message in reversed(session["messages"][-n:]):
            if remaining <= 0:
                break
            copy = dict(message)
            content = str(copy.get("content", ""))
            copy["content"] = content[:remaining]
            selected.append(copy)
            remaining -= len(copy["content"])
        return list(reversed(selected))

    def list_sessions(
        self,
        user_token: str | None = None,
        team_ids: set[str] | None = None,
    ) -> List[dict]:
        sessions = []
        for fname in os.listdir(self.data_dir):
            if fname.endswith(".json"):
                path = os.path.join(self.data_dir, fname)
                with open(path, "r", encoding="utf-8") as f:
                    s = json.load(f)
                is_owner = user_token is not None and s.get("user_token") == user_token
                is_team_member = bool(team_ids and s.get("team_id") in team_ids)
                if user_token is not None and not is_owner and not is_team_member:
                    continue
                sessions.append({
                    "id": s["id"],
                    "problem_statement": s["problem_statement"],
                    "phase": s["phase"],
                    "message_count": len(s["messages"]),
                    "created_at": s["created_at"],
                    "team_id": s.get("team_id", ""),
                })
        sessions.sort(key=lambda s: s["created_at"], reverse=True)
        return sessions

    def list_by_team(self, team_id: str) -> List[dict]:
        return [
            session
            for session in self.list_all_raw()
            if session.get("team_id") == team_id
        ]

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
        temp_path = f"{path}.{uuid.uuid4().hex}.tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(session, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)


session_store = SessionStore()


class InterviewSessionStore:
    """独立面试空间存储，与脑暴会话分离，避免污染主会话状态。"""

    def __init__(self):
        self.data_dir = INTERVIEW_SESSION_DATA_DIR
        self._lock = threading.RLock()
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
            "audit_run_id": interview_id,
            "parent_session_id": parent_session_id,
            "problem_statement": problem_statement,
            "canvas_tree": canvas_tree,
            "messages": [],
            "dimensions_covered": [],
            "dimension_plan": [
                "problem_validity",
                "solution_effectiveness",
                "technical_risk",
                "business_viability",
                "user_adoption",
                "execution_risk",
                "problem_validity",
                "execution_risk",
            ],
            "current_dimension": None,
            "question_count": 0,
            "status": "not_started",
            "report": None,
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
        with self._lock:
            space = self.get(interview_id)
            if space is None:
                raise ValueError(f"面试空间 {interview_id} 未找到")
            space.update(updates)
            self._save(space)

    def add_message(self, interview_id: str, role: str, content: str, role_name: str = None):
        with self._lock:
            space = self.get(interview_id)
            if space is None:
                raise ValueError(f"面试空间 {interview_id} 未找到")
            msg = {
                "id": uuid.uuid4().hex[:16],
                "role": role,
                "content": content,
                "timestamp": datetime.now().isoformat(),
                "stage": "audit",
                "audit_run_id": interview_id,
            }
            if role_name:
                msg["role_name"] = role_name
            space["messages"].append(msg)
            self._save(space)
            return msg

    def get_recent_messages(
        self,
        interview_id: str,
        n: int = 20,
        max_chars: int = 30000,
    ) -> List[dict]:
        space = self.get(interview_id)
        if space is None:
            return []
        selected = []
        remaining = max_chars
        for message in reversed(space["messages"][-n:]):
            if remaining <= 0:
                break
            copy = dict(message)
            content = str(copy.get("content", ""))
            copy["content"] = content[:remaining]
            selected.append(copy)
            remaining -= len(copy["content"])
        return list(reversed(selected))

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

    def get_latest_by_parent(
        self,
        parent_session_id: str,
        user_token: str | None = None,
        statuses: tuple[str, ...] | None = None,
    ) -> Optional[dict]:
        candidates = []
        for fname in os.listdir(self.data_dir):
            if not fname.endswith(".json"):
                continue
            with open(os.path.join(self.data_dir, fname), "r", encoding="utf-8") as f:
                space = json.load(f)
            if space.get("parent_session_id") != parent_session_id:
                continue
            if user_token is not None and space.get("user_token") != user_token:
                continue
            if statuses and space.get("status") not in statuses:
                continue
            candidates.append(space)
        if not candidates:
            return None
        return max(candidates, key=lambda item: item.get("created_at", ""))

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
        temp_path = f"{path}.{uuid.uuid4().hex}.tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(space, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)


interview_session_store = InterviewSessionStore()
