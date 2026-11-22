"""旧会话 ID 别名表测试（PRD §8.1）。

迁移器把 12 hex 旧 ID 确定性重映射为 ``sha256(legacy_id)[:32]``，旧书签/旧链接
若直接访问会 404。别名表 ``session_id_aliases`` 只做只读重定向，让旧链接仍能打开
同一个会话，但不参与任何写路径，也不放宽归属校验。
"""

from __future__ import annotations

import contextlib
import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from core import auth
from core.config import settings
from domain.contracts import sha256_text
from domain.errors import AppError, ErrorCode
from migrations import import_json_sessions
from repositories import database, session_repo
from repositories.database import SessionIdAliasRow, SessionRow

import main

LEGACY_ID = "0b1e146622ae"  # 迁移来源里的 12 hex 旧 ID
API_ALIAS_ID = "aaaabbbbcccc"  # 手工登记的 12 hex 别名（API 用例用）
OWNER = "alias-owner@example.com"
OTHER_OWNER = "alias-other@example.com"


def _legacy_payload(legacy_id: str, **overrides: object) -> dict:
    payload = {
        "id": legacy_id,
        "problem_statement": "旧会话的问题描述",
        "phase": "brainstorm",
        "messages": [
            {"role": "user", "content": "问题一", "timestamp": "2025-07-25T15:47:26.742238"},
            {"role": "assistant", "content": "回答一", "timestamp": "2025-07-25T15:47:30"},
        ],
        "canvas_tree": None,
        "canvas_last_msg_index": None,
        "interview_dimensions_covered": [],
        "interview_question_count": 0,
        "created_at": "2025-07-25T15:47:26.742238",
    }
    payload.update(overrides)
    return payload


class SessionAliasTest(unittest.TestCase):
    """别名表的写入、解析、API 重定向与权限/删除语义。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.source_dir = self.tmp_path / "sessions"
        self.source_dir.mkdir()
        self.manifest_dir = self.tmp_path / "backups"
        db_path = (self.tmp_path / "alias.db").as_posix()
        database.reset_for_tests(f"sqlite+pysqlite:///{db_path}")
        self.client = TestClient(main.app)
        self._import_legacy_source()
        # 正常登录用户自己的会话，用于 API 侧别名重定向/权限/软删用例
        with database.session_scope() as db:
            session = session_repo.create_session(
                db, owner_email=OWNER, problem_statement="用户自建会话的问题"
            )
            self.owner_session_id = session.id
            session_repo.record_session_alias(db, API_ALIAS_ID, session.id)

    def tearDown(self) -> None:
        with contextlib.suppress(Exception):
            self.client.close()
        with contextlib.suppress(Exception):
            database.configure(database.default_database_url(), force=True)
        with contextlib.suppress(Exception):
            database.dispose_engine()
        self._tmp.cleanup()

    # ------------------------------------------------------------------ #
    # 辅助
    # ------------------------------------------------------------------ #
    def _import_legacy_source(self) -> dict:
        path = self.source_dir / "legacy.json"
        path.write_text(json.dumps(_legacy_payload(LEGACY_ID), ensure_ascii=False), "utf-8")
        return import_json_sessions.apply_import(self.source_dir, self.manifest_dir)

    @staticmethod
    def _mapped_id(legacy_id: str) -> str:
        return sha256_text(legacy_id)[:32]

    def _headers(self, email: str = OWNER) -> dict[str, str]:
        token = auth.create_session_token(email)
        return {"Cookie": f"{settings.auth_cookie_name}={token}"}

    @staticmethod
    def _alias_count(db) -> int:
        return int(db.execute(select(func.count()).select_from(SessionIdAliasRow)).scalar_one())

    # ------------------------------------------------------------------ #
    # 写入
    # ------------------------------------------------------------------ #
    def test_import_registers_alias_for_remapped_id(self) -> None:
        mapped = self._mapped_id(LEGACY_ID)
        with database.session_scope() as db:
            alias = db.get(SessionIdAliasRow, LEGACY_ID)
            self.assertIsNotNone(alias)
            self.assertEqual(alias.session_id, mapped)
            self.assertTrue(alias.source_path.endswith("legacy.json"))
            self.assertTrue(alias.created_at)
            # 32 hex → 自身
            self.assertEqual(session_repo.resolve_session_id(db, mapped), mapped)
            # 旧 ID → 映射值
            self.assertEqual(session_repo.resolve_session_id(db, LEGACY_ID), mapped)
            # 未知 ID / 空串 → None
            self.assertIsNone(session_repo.resolve_session_id(db, "ffffffffffff"))
            self.assertIsNone(session_repo.resolve_session_id(db, ""))
            self.assertEqual(len(session_repo.list_session_aliases(db, mapped)), 1)

    def test_record_alias_is_idempotent_and_validates(self) -> None:
        with database.session_scope() as db:
            before = self._alias_count(db)
            first = session_repo.record_session_alias(db, "ddddeeeeffff", self.owner_session_id)
            second = session_repo.record_session_alias(db, "ddddeeeeffff", self.owner_session_id)
            self.assertEqual(first.legacy_id, second.legacy_id)
            self.assertIsNone(first.source_path)
            self.assertEqual(self._alias_count(db), before + 1)

            with self.assertRaises(AppError) as ctx:
                session_repo.record_session_alias(db, "", self.owner_session_id)
            self.assertEqual(ctx.exception.code, ErrorCode.INVALID_REQUEST)
            with self.assertRaises(AppError) as ctx:
                session_repo.record_session_alias(
                    db, self.owner_session_id, self.owner_session_id
                )
            self.assertEqual(ctx.exception.code, ErrorCode.INVALID_REQUEST)
            self.assertIn("无需别名", str(ctx.exception))

    # ------------------------------------------------------------------ #
    # API 重定向
    # ------------------------------------------------------------------ #
    def test_legacy_link_resolves_to_current_session(self) -> None:
        response = self.client.get(f"/api/v2/sessions/{API_ALIAS_ID}", headers=self._headers())
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["id"], self.owner_session_id)
        self.assertEqual(body["problem_statement"], "用户自建会话的问题")

    def test_unknown_legacy_id_returns_404(self) -> None:
        response = self.client.get("/api/v2/sessions/ffffffffffff", headers=self._headers())
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], ErrorCode.NOT_FOUND)

    def test_alias_does_not_bypass_ownership(self) -> None:
        response = self.client.get(
            f"/api/v2/sessions/{API_ALIAS_ID}", headers=self._headers(OTHER_OWNER)
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], ErrorCode.FORBIDDEN)

    def test_soft_deleted_session_hides_alias_link(self) -> None:
        with database.session_scope() as db:
            session = db.get(SessionRow, self.owner_session_id)
            assert session is not None
            session_repo.soft_delete_session(db, session)
        response = self.client.get(f"/api/v2/sessions/{API_ALIAS_ID}", headers=self._headers())
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
