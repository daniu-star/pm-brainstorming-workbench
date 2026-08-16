"""会话持久化存储：基于 JSON 文件，带锁、原子写与消息上限保护。"""
import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from threading import RLock

from core.config import settings

logger = logging.getLogger(__name__)

# 统一从 settings 读取（settings 已加载 backend/.env），保留环境变量覆盖语义（B123）。
SESSION_DATA_DIR = settings.session_data_dir

# 会话 ID 必须是 32 位小写十六进制（uuid4().hex），阻止路径穿越（B002）。
_SESSION_ID_RE = re.compile(r"^[0-9a-f]{32}$")

# 单会话消息上限，超出时截断保留最近 N 条（B039）。
MAX_MESSAGES = 200


class SessionStore:
    def __init__(self, data_dir: str | None = None):
        self.data_dir = data_dir or SESSION_DATA_DIR
        os.makedirs(self.data_dir, exist_ok=True)
        # 每会话一把锁，配合全局守卫锁创建（B027）。
        self._locks: dict[str, RLock] = {}
        self._locks_guard = threading.Lock()

    # ---------- 内部工具 ----------

    def _validate_session_id(self, session_id: str) -> None:
        if not isinstance(session_id, str) or not _SESSION_ID_RE.fullmatch(session_id):
            raise ValueError("无效的会话 ID")

    def _path(self, session_id: str) -> Path:
        return Path(self.data_dir) / f"{session_id}.json"

    def _get_lock(self, session_id: str) -> RLock:
        with self._locks_guard:
            lock = self._locks.get(session_id)
            if lock is None:
                lock = RLock()
                self._locks[session_id] = lock
            return lock

    def _save(self, session: dict) -> None:
        """临时文件 + os.replace 原子写，避免崩溃时损坏会话文件（B028）。"""
        path = self._path(session["id"])
        tmp_path = path.with_name(f".{path.name}.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(session, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)

    # ---------- 对外接口 ----------

    def create(self, problem_statement: str, owner_email: str = "") -> dict:
        session_id = uuid.uuid4().hex
        session = {
            "id": session_id,
            "problem_statement": problem_statement,
            "phase": "brainstorm",
            "owner_email": owner_email,
            "messages": [],
            "canvas_tree": None,
            "canvas_last_msg_index": 0,
            "interview_dimensions_covered": [],
            "interview_question_count": 0,
            "created_at": datetime.now().isoformat(),
        }
        with self._get_lock(session_id):
            self._save(session)
        return session

    def get(self, session_id: str) -> dict | None:
        self._validate_session_id(session_id)
        path = self._path(session_id)
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            logger.warning("会话文件读取失败，已忽略：%s", path.name)
            return None

    def update(self, session_id: str, updates: dict) -> None:
        self._validate_session_id(session_id)
        with self._get_lock(session_id):
            session = self.get(session_id)
            if session is None:
                raise ValueError(f"会话 {session_id} 未找到")
            session.update(updates)
            self._save(session)

    def add_message(self, session_id: str, role: str, content: str, role_name: str | None = None) -> None:
        self._validate_session_id(session_id)
        with self._get_lock(session_id):
            session = self.get(session_id)
            if session is None:
                raise ValueError(f"会话 {session_id} 未找到")
            msg = {"role": role, "content": content, "timestamp": datetime.now().isoformat()}
            if role_name:
                msg["role_name"] = role_name
            session["messages"].append(msg)
            # 超限截断，保留最近 MAX_MESSAGES 条（B039）。
            if len(session["messages"]) > MAX_MESSAGES:
                session["messages"] = session["messages"][-MAX_MESSAGES:]
            self._save(session)

    def get_recent_messages(self, session_id: str, n: int = 20) -> list[dict]:
        session = self.get(session_id)
        if session is None:
            return []
        return session["messages"][-n:]

    def list_sessions(self, owner_email: str | None = None) -> list[dict]:
        """列出会话；传入 owner_email 时只返回该用户的会话（B001）。"""
        sessions: list[dict] = []
        for fname in os.listdir(self.data_dir):
            if not fname.endswith(".json"):
                continue
            stem = fname[: -len(".json")]
            if not _SESSION_ID_RE.fullmatch(stem):
                continue
            try:
                with open(Path(self.data_dir) / fname, "r", encoding="utf-8") as f:
                    s = json.load(f)
                if owner_email is not None and s.get("owner_email") != owner_email:
                    continue
                sessions.append({
                    "id": s["id"],
                    "problem_statement": s["problem_statement"],
                    "phase": s["phase"],
                    "message_count": len(s["messages"]),
                    "created_at": s["created_at"],
                })
            except (json.JSONDecodeError, OSError, KeyError):
                logger.warning("会话列表加载失败，已跳过文件：%s", fname)
        sessions.sort(key=lambda s: s["created_at"], reverse=True)
        return sessions

    def delete(self, session_id: str) -> None:
        self._validate_session_id(session_id)
        with self._get_lock(session_id):
            path = self._path(session_id)
            if path.exists():
                path.unlink()


session_store = SessionStore()
