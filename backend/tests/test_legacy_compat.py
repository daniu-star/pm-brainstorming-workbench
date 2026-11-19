"""旧路由兼容测试（PRD §7.3）：adapter 只能走 command_service / v2 查询层。

覆盖：
1. `/api/session` 读写走 DB（不再产生 data/sessions JSON 文件），phase 输出 v1 旧值；
2. `/api/canvas` 为确定性投影：generate/incremental 不调模型，PUT → 409 CANVAS_IS_DERIVED；
3. `/api/brainstorm` 旧 SSE：v2 事件翻译为 role_start/token/role_done/done，done 恰好一次；
4. 已删除会话在旧路由上一律 404；
5. `/api/interview/start` 在错误阶段返回旧语义 409。
"""
from __future__ import annotations

import atexit
import gc
import os
import shutil
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import main
from core import auth
from core.config import settings
from domain import contracts as C
from domain.errors import ErrorCode
from repositories import database, run_repo, session_repo

TEST_SECRET = "test-secret-key-that-is-longer-than-32-characters"

_TEMP_DIRS: list[str] = []


def _make_db_dir(prefix: str) -> str:
    """创建临时库目录；进程退出时统一清理（本机 TEMP 不可用时 mkdtemp 会落在 cwd）。"""

    path = tempfile.mkdtemp(prefix=prefix)
    _TEMP_DIRS.append(path)
    return path


def _cleanup_temp_dirs() -> None:
    """先释放 SQLite 引擎（Windows 上不释放会锁住文件），再删除临时目录。

    Windows 上 sqlite 文件句柄可能延迟释放，因此带重试；清理是测试卫生的一部分，
    必须保证跑完后工作树里不残留 ``pm_legacy_*`` 目录。
    """

    try:
        gc.collect()
        database.dispose_engine()
    except Exception:  # pragma: no cover - 清理路径容错
        pass
    for path in list(_TEMP_DIRS):
        for attempt in range(4):
            shutil.rmtree(path, ignore_errors=True)
            if not os.path.exists(path):
                break
            try:
                gc.collect()
                database.dispose_engine()
            except Exception:  # pragma: no cover - 清理路径容错
                pass
            time.sleep(0.1 * (attempt + 1))
    _TEMP_DIRS.clear()


atexit.register(_cleanup_temp_dirs)


class LegacyCompatTestBase(unittest.TestCase):
    _tmpdir: str = ""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmpdir = _make_db_dir("pm_legacy_")
        db_path = (Path(cls._tmpdir) / "test.db").as_posix()
        database.reset_for_tests(f"sqlite+pysqlite:///{db_path}")
        cls._originals = {
            "auth_secret_key": settings.auth_secret_key,
            "smtp_username": settings.smtp_username,
            "smtp_password": settings.smtp_password,
            "smtp_from": settings.smtp_from,
        }
        settings.auth_secret_key = TEST_SECRET
        cls.client = TestClient(main.app)

    @classmethod
    def tearDownClass(cls) -> None:
        for key, value in cls._originals.items():
            setattr(settings, key, value)
        cls.client.close()

    def setUp(self) -> None:
        self.email = f"legacy-{uuid.uuid4().hex[:10]}@example.com"

    def headers(self, **extra: str) -> dict:
        token = auth.create_session_token(self.email)
        headers = {"Cookie": f"{settings.auth_cookie_name}={token}"}
        headers.update(extra)
        return headers

    def post(self, url: str, **kwargs):
        headers = self.headers()
        headers.update(kwargs.pop("headers", {}) or {})
        return self.client.post(url, headers=headers, **kwargs)

    def get(self, url: str, **kwargs):
        headers = self.headers()
        headers.update(kwargs.pop("headers", {}) or {})
        return self.client.get(url, headers=headers, **kwargs)

    def put(self, url: str, **kwargs):
        headers = self.headers()
        headers.update(kwargs.pop("headers", {}) or {})
        return self.client.put(url, headers=headers, **kwargs)

    def delete(self, url: str, **kwargs):
        headers = self.headers()
        headers.update(kwargs.pop("headers", {}) or {})
        return self.client.delete(url, headers=headers, **kwargs)

    def create_legacy_session(self) -> dict:
        resp = self.post(
            "/api/session", json={"problem_statement": "帮我梳理一个内容社区的产品定义"}
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def seed_v2_session(self) -> str:
        resp = self.post(
            "/api/v2/sessions", json={"problem_statement": "验证旧路由兼容性"}
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        return resp.json()["id"]

    def create_terminal_run(self, session_id: str) -> str:
        """造一个带角色输出事件、已终结的 Run（不经过 worker）。"""

        with database.session_scope() as db:
            run = run_repo.create_run(
                db,
                session_id=session_id,
                owner_email=self.email,
                request_id=C.new_request_id(),
                action=C.Action.brainstorm.value,
                input_hash="1" * 64,
                base_session_version=1,
                base_product_version=1,
                now_iso=C.utc_now_iso(),
            )
            run_repo.append_event(
                db,
                session_id=session_id,
                run_id=run.id,
                type=C.EventType.step_started.value,
                payload={"role_code": C.RoleCode.cto.value, "step": "cto"},
                now_iso=C.utc_now_iso(),
            )
            run_repo.append_event(
                db,
                session_id=session_id,
                run_id=run.id,
                type=C.EventType.role_output_updated.value,
                payload={"role_code": C.RoleCode.cto.value, "delta": "先验证技术可行性。"},
                now_iso=C.utc_now_iso(),
            )
            run_repo.append_event(
                db,
                session_id=session_id,
                run_id=run.id,
                type=C.EventType.role_completed.value,
                payload={
                    "role_code": C.RoleCode.cto.value,
                    "content": "先验证技术可行性。",
                },
                now_iso=C.utc_now_iso(),
            )
            run.status = C.RunStatus.running.value
            run.lease_owner = "test-worker"
            run.lease_epoch = 1
            db.flush()
            run_repo.release_run(
                db,
                run,
                worker_id="test-worker",
                lease_epoch=1,
                status=C.RunStatus.succeeded.value,
                now_iso=C.utc_now_iso(),
            )
            run_repo.append_event(
                db,
                session_id=session_id,
                run_id=run.id,
                type=C.EventType.run_completed.value,
                payload={"status": C.RunStatus.succeeded.value},
                now_iso=C.utc_now_iso(),
            )
            return run.id


class LegacySessionTests(LegacyCompatTestBase):
    def test_legacy_session_payload_uses_v1_phase_and_db(self) -> None:
        data_dir = Path(settings.session_data_dir)
        before = set(data_dir.glob("*")) if data_dir.exists() else set()

        created = self.create_legacy_session()
        self.assertEqual(created["phase"], "coach")  # v1 旧值
        # 会话创建即写入首条 user 消息（FR-02），旧接口不再返回空列表。
        self.assertEqual(len(created["messages"]), 1)
        self.assertEqual(created["messages"][0]["role"], "user")
        self.assertEqual(created["messages"][0]["content"], created["problem_statement"])
        self.assertIn("canvas_tree", created)
        self.assertIsNone(created["canvas_tree"])
        session_id = created["id"]

        # 连续刷新 3 次：消息不重复追加、不产生 4xx。
        for _ in range(3):
            refreshed = self.get(f"/api/session/{session_id}")
            self.assertEqual(refreshed.status_code, 200, refreshed.text)
            self.assertEqual(len(refreshed.json()["messages"]), 1)

        detail = self.get(f"/api/session/{session_id}")
        self.assertEqual(detail.status_code, 200, detail.text)
        payload = detail.json()
        self.assertEqual(payload["id"], session_id)
        self.assertEqual(payload["phase"], "coach")
        self.assertEqual(len(payload["messages"]), 1)
        first = payload["messages"][0]
        self.assertEqual(first["role"], "user")
        self.assertEqual(first["content"], created["problem_statement"])

        listing = self.get("/api/session")
        self.assertEqual(listing.status_code, 200, listing.text)
        ids = [item["id"] for item in listing.json()]
        self.assertIn(session_id, ids)

        after = set(data_dir.glob("*")) if data_dir.exists() else set()
        self.assertEqual(before, after, "旧路由不得再写 data/sessions JSON 存储")

    def test_legacy_delete_then_all_legacy_routes_404(self) -> None:
        session = self.create_legacy_session()
        session_id = session["id"]

        headers = {"Idempotency-Key": str(uuid.uuid4())}
        removed = self.delete(f"/api/v2/sessions/{session_id}", headers=headers)
        self.assertEqual(removed.status_code, 202, removed.text)

        self.assertEqual(self.get(f"/api/session/{session_id}").status_code, 404)
        self.assertEqual(self.get(f"/api/canvas/{session_id}").status_code, 404)
        self.assertEqual(
            self.put(
                f"/api/canvas/{session_id}",
                json={"session_id": session_id, "tree": {"root": "x", "branches": []}},
            ).status_code,
            404,
        )
        self.assertEqual(
            self.post(
                "/api/brainstorm/message",
                json={"session_id": session_id, "content": "还在吗", "target_role": "cto"},
            ).status_code,
            404,
        )
        # 列表不再包含已删会话
        listing = self.get("/api/session")
        ids = [item["id"] for item in listing.json()]
        self.assertNotIn(session_id, ids)


class LegacyCanvasTests(LegacyCompatTestBase):
    def test_canvas_is_deterministic_projection(self) -> None:
        session_id = self.seed_v2_session()

        tree = self.get(f"/api/canvas/{session_id}")
        self.assertEqual(tree.status_code, 200, tree.text)
        payload = tree.json()
        self.assertIn("root", payload)
        self.assertIn("branches", payload)
        self.assertIsInstance(payload["branches"], list)

        generated = self.post("/api/canvas/generate", json={"session_id": session_id})
        self.assertEqual(generated.status_code, 200, generated.text)
        self.assertEqual(generated.json()["root"], payload["root"])

        incremental = self.post(
            "/api/canvas/incremental", json={"session_id": session_id}
        )
        self.assertEqual(incremental.status_code, 200, incremental.text)
        self.assertEqual(incremental.json(), generated.json())

    def test_canvas_put_is_conflict(self) -> None:
        session_id = self.seed_v2_session()

        put_tree = self.put(
            "/api/canvas/tree", json={"root": "手改画布", "branches": []}
        )
        self.assertEqual(put_tree.status_code, 409, put_tree.text)
        self.assertEqual(
            put_tree.json()["error"]["code"], ErrorCode.CANVAS_IS_DERIVED
        )

        put_session = self.put(
            f"/api/canvas/{session_id}",
            json={"session_id": session_id, "tree": {"root": "x", "branches": []}},
        )
        self.assertEqual(put_session.status_code, 409, put_session.text)
        self.assertEqual(
            put_session.json()["error"]["code"], ErrorCode.CANVAS_IS_DERIVED
        )


class LegacyBrainstormTests(LegacyCompatTestBase):
    def test_brainstorm_sse_translates_events_and_done_once(self) -> None:
        session_id = self.seed_v2_session()
        run_id = self.create_terminal_run(session_id)

        with mock.patch(
            "api.brainstorm_routes._submit", return_value=(run_id, "brainstorm")
        ):
            resp = self.post(
                "/api/brainstorm/message",
                json={"session_id": session_id, "content": "技术上怎么做？", "target_role": "cto"},
            )

        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.headers["content-type"].startswith("text/event-stream"))
        body = resp.text
        self.assertIn('"type": "role_start"', body)
        self.assertIn('"type": "token"', body)
        self.assertIn('"type": "role_done"', body)
        self.assertIn('"type": "phase_change"', body)
        self.assertEqual(body.count('"type": "done"'), 1, body)
        self.assertNotIn('"type": "error"', body)

    def test_brainstorm_invalid_target_role(self) -> None:
        session_id = self.seed_v2_session()
        resp = self.post(
            "/api/brainstorm/message",
            json={"session_id": session_id, "content": "hi", "target_role": "cfo"},
        )
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertEqual(resp.json()["error"]["code"], ErrorCode.INVALID_REQUEST)


class LegacyInterviewTests(LegacyCompatTestBase):
    def test_interview_start_from_clarifying_returns_409(self) -> None:
        session_id = self.seed_v2_session()
        resp = self.post("/api/interview/start", json={"session_id": session_id})
        self.assertEqual(resp.status_code, 409, resp.text)
        self.assertEqual(resp.json()["error"]["code"], ErrorCode.AUDIT_ACTIVE)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
