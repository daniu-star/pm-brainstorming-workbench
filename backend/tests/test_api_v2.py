"""v2 API 契约测试（PRD §7.2 / FR-06）。

覆盖：
1. 未登录 401（统一错误体）；
2. 会话创建 → 详情 / 消息分页 / 产品状态结构（limit 截断）；
3. 同步命令幂等重放（同一 request_id 返回同一 receipt、reused=true）、版本冲突 409；
4. SESSION_BUSY 409 + active_run_id；
5. SSE 游标语义（after_seq / Last-Event-ID 取较大值、after_seq=0 从头回放、
   非法游标 422、模拟保留水位后 410、终结后关闭）；
6. 任务快照单事务结构；
7. 验证码跨进程有效（DB 权威，清空进程内缓存后仍可校验）。

测试使用临时 SQLite + 签名 Cookie；不进入 TestClient 的 with 块以避免触发
lifespan 里的 RAG 构建。
"""
from __future__ import annotations

import atexit
import gc
import json
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
from repositories import audit_repo, database, run_repo, session_repo

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
    必须保证跑完后工作树里不残留 ``pm_api_v2_*`` 目录。
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


def _auth_config() -> dict:
    return {
        "auth_secret_key": TEST_SECRET,
        "smtp_username": "noreply@example.com",
        "smtp_password": "test-password",
        "smtp_from": "noreply@example.com",
        "auth_code_expire_minutes": 10,
        "auth_code_resend_seconds": 60,
    }


class V2ApiTestBase(unittest.TestCase):
    """共享临时库与签名 Cookie 的基类。"""

    _tmpdir: str = ""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmpdir = _make_db_dir("pm_api_v2_")
        db_path = (Path(cls._tmpdir) / "test.db").as_posix()
        database.reset_for_tests(f"sqlite+pysqlite:///{db_path}")

        cls._originals = {key: getattr(settings, key) for key in _auth_config()}
        for key, value in _auth_config().items():
            setattr(settings, key, value)

        cls.client = TestClient(main.app)

    @classmethod
    def tearDownClass(cls) -> None:
        for key, value in cls._originals.items():
            setattr(settings, key, value)
        cls.client.close()

    # -- helpers ---------------------------------------------------------
    def setUp(self) -> None:
        self.email = f"tester-{uuid.uuid4().hex[:10]}@example.com"

    def headers(self, email: str | None = None, **extra: str) -> dict:
        token = auth.create_session_token(email or self.email)
        headers = {"Cookie": f"{settings.auth_cookie_name}={token}"}
        headers.update(extra)
        return headers

    def post(self, url: str, *, email: str | None = None, **kwargs):
        headers = self.headers(email)
        headers.update(kwargs.pop("headers", {}) or {})
        return self.client.post(url, headers=headers, **kwargs)

    def get(self, url: str, *, email: str | None = None, **kwargs):
        headers = self.headers(email)
        headers.update(kwargs.pop("headers", {}) or {})
        return self.client.get(url, headers=headers, **kwargs)

    def delete(self, url: str, *, email: str | None = None, **kwargs):
        headers = self.headers(email)
        headers.update(kwargs.pop("headers", {}) or {})
        return self.client.delete(url, headers=headers, **kwargs)

    def create_session(self, problem: str = "帮我验证一个面向独立开发者的周报工具") -> dict:
        resp = self.post("/api/v2/sessions", json={"problem_statement": problem})
        self.assertEqual(resp.status_code, 201, resp.text)
        return resp.json()

    def seed_ready_brief(self, session_id: str) -> int:
        """模拟澄清阶段已完成（coach 尚未落地），让 confirm_brief 可执行。

        返回播种后的产品版本号。
        """

        with database.session_scope() as db:
            session = session_repo.require_session(db, session_id, owner_email=self.email)
            state = session_repo.get_product_state(db, session_id)
            brief = state.brief.model_copy(deep=True)
            values = {
                "target_users": "独立开发者",
                "usage_scenario": "每周写项目周报",
                "core_problem": "手工整理耗时且容易遗漏",
                "success_criteria": "10 分钟内生成可发布的周报",
            }
            for name, value in values.items():
                field_obj = getattr(brief, name)
                setattr(
                    brief,
                    name,
                    field_obj.model_copy(
                        update={"value": value, "status": C.BriefFieldStatus.proposed}
                    ),
                )
            new_state = state.model_copy(update={"brief": brief, "version": state.version + 1})
            session_repo.save_product_state(db, session, new_state, now_iso=C.utc_now_iso())
            session_repo.touch_session(
                db, session, product_version=new_state.version, now_iso=C.utc_now_iso()
            )
            return new_state.version

    def create_run_with_events(self, session_id: str, *, terminal: bool = True) -> str:
        """直接写库造一个带 3 个事件的 Run（不经过 worker）。"""

        with database.session_scope() as db:
            run = run_repo.create_run(
                db,
                session_id=session_id,
                owner_email=self.email,
                request_id=C.new_request_id(),
                action=C.Action.clarify.value,
                input_hash="0" * 64,
                base_session_version=1,
                base_product_version=1,
                now_iso=C.utc_now_iso(),
            )
            run_repo.append_event(
                db,
                session_id=session_id,
                run_id=run.id,
                type=C.EventType.step_started.value,
                payload={"step": "coach_question_plan"},
                now_iso=C.utc_now_iso(),
            )
            run_repo.append_event(
                db,
                session_id=session_id,
                run_id=run.id,
                type=C.EventType.role_output_updated.value,
                payload={"role": "coach", "delta": "先说说目标用户？"},
                now_iso=C.utc_now_iso(),
            )
            if terminal:
                # 走一次真实的 running → succeeded 迁移（模拟 worker 已执行完）。
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


class AuthAndSessionTests(V2ApiTestBase):
    def test_unauthenticated_request_returns_401(self) -> None:
        resp = self.client.get("/api/v2/sessions")
        self.assertEqual(resp.status_code, 401, resp.text)
        body = resp.json()
        self.assertEqual(body["error"]["code"], ErrorCode.UNAUTHENTICATED)
        self.assertIn("message", body["error"])
        self.assertIn("request_id", body["error"])
        # 兼容旧前端的 detail 字段
        self.assertIn("detail", body)

    def test_create_session_and_views(self) -> None:
        session = self.create_session()
        session_id = session["id"]
        self.assertEqual(session["phase"], C.SessionPhase.clarifying.value)
        self.assertEqual(session["session_version"], 1)
        self.assertEqual(session["current_product_version"], 1)
        self.assertEqual(session["message_count"], 1)

        detail = self.get(f"/api/v2/sessions/{session_id}")
        self.assertEqual(detail.status_code, 200, detail.text)
        payload = detail.json()
        self.assertEqual(payload["id"], session_id)
        self.assertEqual(payload["problem_statement"], session["problem_statement"])
        self.assertIn("brief", payload)
        self.assertEqual(payload["brief"]["status"], C.BriefStatus.draft.value)

        messages = self.get(f"/api/v2/sessions/{session_id}/messages")
        self.assertEqual(messages.status_code, 200, messages.text)
        page = messages.json()
        self.assertEqual(len(page["items"]), 1)
        self.assertEqual(page["items"][0]["seq"], 1)
        self.assertEqual(page["items"][0]["sender_kind"], C.SenderKind.human.value)
        self.assertEqual(page["items"][0]["stage"], C.MessageStage.clarify.value)
        self.assertIn("next_before_seq", page)

        state = self.get(f"/api/v2/sessions/{session_id}/product-state")
        self.assertEqual(state.status_code, 200, state.text)
        self.assertEqual(state.json()["version"], 1)
        self.assertEqual(state.json()["brief"]["status"], C.BriefStatus.draft.value)

    def test_messages_pagination_and_limit_clamp(self) -> None:
        session = self.create_session()
        session_id = session["id"]
        with database.session_scope() as db:
            row = session_repo.require_session(db, session_id, owner_email=self.email)
            for index in range(5):
                session_repo.add_message(
                    db,
                    row,
                    sender_kind=C.SenderKind.agent,
                    stage=C.MessageStage.clarify,
                    content=f"追问 {index}",
                    now_iso=C.utc_now_iso(),
                )

        page = self.get(
            f"/api/v2/sessions/{session_id}/messages", params={"limit": 2}
        ).json()
        self.assertEqual([item["seq"] for item in page["items"]], [5, 6])
        self.assertEqual(page["next_before_seq"], 5)

        older = self.get(
            f"/api/v2/sessions/{session_id}/messages",
            params={"before_seq": page["next_before_seq"], "limit": 2},
        ).json()
        self.assertEqual([item["seq"] for item in older["items"]], [3, 4])

        # limit 超过上限：静默截断而不是报错
        oversized = self.get(
            f"/api/v2/sessions/{session_id}/messages", params={"limit": 5000}
        )
        self.assertEqual(oversized.status_code, 200, oversized.text)
        self.assertEqual(len(oversized.json()["items"]), 6)

        # 非法 limit → 422
        invalid = self.get(
            f"/api/v2/sessions/{session_id}/messages", params={"limit": 0}
        )
        self.assertEqual(invalid.status_code, 422, invalid.text)
        self.assertEqual(invalid.json()["error"]["code"], ErrorCode.INVALID_REQUEST)

    def test_session_ownership_and_missing(self) -> None:
        session = self.create_session()
        other = f"other-{uuid.uuid4().hex[:8]}@example.com"
        forbidden = self.get(f"/api/v2/sessions/{session['id']}", email=other)
        self.assertEqual(forbidden.status_code, 403, forbidden.text)
        self.assertEqual(forbidden.json()["error"]["code"], ErrorCode.FORBIDDEN)

        missing = self.get("/api/v2/sessions/" + "0" * 32, email=other)
        self.assertEqual(missing.status_code, 404, missing.text)
        self.assertEqual(missing.json()["error"]["code"], ErrorCode.NOT_FOUND)


class CommandTests(V2ApiTestBase):
    def _confirm_brief_body(self, session_id: str, request_id: str) -> dict:
        return {
            "request_id": request_id,
            "action": C.Action.confirm_brief.value,
            "expected_session_version": 1,
            "payload": {"expected_product_version": 2},
        }

    def test_confirm_brief_idempotent_replay_and_version_conflict(self) -> None:
        session = self.create_session()
        session_id = session["id"]
        self.seed_ready_brief(session_id)
        request_id = str(uuid.uuid4())

        body = self._confirm_brief_body(session_id, request_id)
        first = self.post(f"/api/v2/sessions/{session_id}/commands", json=body)
        self.assertEqual(first.status_code, 202, first.text)
        receipt = first.json()
        self.assertEqual(receipt["status"], C.RunStatus.succeeded.value)
        self.assertFalse(receipt["reused"])
        self.assertEqual(receipt["action"], C.Action.confirm_brief.value)
        run_id = receipt["run_id"]

        replay = self.post(f"/api/v2/sessions/{session_id}/commands", json=body)
        self.assertEqual(replay.status_code, 202, replay.text)
        replay_receipt = replay.json()
        self.assertTrue(replay_receipt["reused"])
        self.assertEqual(replay_receipt["run_id"], run_id)
        self.assertEqual(replay_receipt["session_version"], receipt["session_version"])

        # 同一 request_id 用于不同 action → 409 IDEMPOTENCY_KEY_REUSED
        reused_key = self.post(
            f"/api/v2/sessions/{session_id}/commands",
            json={
                "request_id": request_id,
                "action": C.Action.add_evidence.value,
                "payload": {"kind": C.EvidenceKind.user_statement.value, "excerpt": "证据"},
            },
        )
        self.assertEqual(reused_key.status_code, 409, reused_key.text)
        self.assertEqual(
            reused_key.json()["error"]["code"], ErrorCode.IDEMPOTENCY_KEY_REUSED
        )

        # 版本冲突：用陈旧的 expected_session_version
        stale = self.post(
            f"/api/v2/sessions/{session_id}/commands",
            json={
                "request_id": str(uuid.uuid4()),
                "action": C.Action.add_evidence.value,
                "expected_session_version": 1,
                "payload": {
                    "kind": C.EvidenceKind.user_statement.value,
                    "excerpt": "一次用户访谈记录",
                },
            },
        )
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["error"]["code"], ErrorCode.VERSION_CONFLICT)
        self.assertIn("current_session_version", stale.json()["error"]["details"])

    def test_command_header_and_body_request_id_mismatch(self) -> None:
        session = self.create_session()
        session_id = session["id"]
        body = {
            "request_id": "request-from-body-1234",
            "action": C.Action.skip_clarification.value,
            "payload": {"reason": "直接进入头脑风暴"},
        }
        resp = self.post(
            f"/api/v2/sessions/{session_id}/commands",
            json=body,
            headers={"Idempotency-Key": "request-from-header-5678"},
        )
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertEqual(
            resp.json()["error"]["code"], ErrorCode.IDEMPOTENCY_KEY_MISMATCH
        )

    def test_unknown_action_returns_422(self) -> None:
        session = self.create_session()
        resp = self.post(
            f"/api/v2/sessions/{session['id']}/commands",
            json={
                "request_id": str(uuid.uuid4()),
                "action": "not_an_action",
                "payload": {},
            },
        )
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertEqual(resp.json()["error"]["code"], ErrorCode.UNKNOWN_ACTION)

    def test_session_busy_returns_active_run_id(self) -> None:
        session = self.create_session()
        session_id = session["id"]
        first = self.post(
            f"/api/v2/sessions/{session_id}/commands",
            json={
                "request_id": str(uuid.uuid4()),
                "action": C.Action.clarify.value,
                "payload": {"content": "目标用户是独立开发者"},
            },
        )
        self.assertEqual(first.status_code, 202, first.text)
        run_id = first.json()["run_id"]

        second = self.post(
            f"/api/v2/sessions/{session_id}/commands",
            json={
                "request_id": str(uuid.uuid4()),
                "action": C.Action.clarify.value,
                "payload": {"content": "再补充一句"},
            },
        )
        self.assertEqual(second.status_code, 409, second.text)
        error = second.json()["error"]
        self.assertEqual(error["code"], ErrorCode.SESSION_BUSY)
        self.assertEqual(error["details"]["active_run_id"], run_id)

        status = self.get(f"/api/v2/runs/{run_id}")
        self.assertEqual(status.status_code, 200, status.text)
        self.assertEqual(status.json()["status"], C.RunStatus.queued.value)


class ResourceDetailTests(V2ApiTestBase):
    def test_message_evidence_and_canvas_endpoints(self) -> None:
        session = self.create_session()
        session_id = session["id"]

        page = self.get(f"/api/v2/sessions/{session_id}/messages").json()
        message_id = page["items"][0]["id"]

        detail = self.get(f"/api/v2/messages/{message_id}")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["id"], message_id)
        self.assertEqual(detail.json()["content"], session["problem_statement"])

        nested = self.get(f"/api/v2/sessions/{session_id}/messages/{message_id}")
        self.assertEqual(nested.status_code, 200, nested.text)

        with database.session_scope() as db:
            row = session_repo.require_session(db, session_id, owner_email=self.email)
            evidence = session_repo.add_evidence(
                db,
                row,
                kind=C.EvidenceKind.user_statement,
                title="用户访谈",
                excerpt="用户说周报最耗时的是整理群聊记录",
                created_by=self.email,
            )
            evidence_id = evidence.id

        fetched = self.get(f"/api/v2/evidence/{evidence_id}")
        self.assertEqual(fetched.status_code, 200, fetched.text)
        self.assertEqual(fetched.json()["id"], evidence_id)
        self.assertEqual(fetched.json()["title"], "用户访谈")

        canvas = self.get(f"/api/v2/sessions/{session_id}/canvas")
        self.assertEqual(canvas.status_code, 200, canvas.text)
        projection = canvas.json()
        self.assertEqual(projection["session_id"], session_id)
        self.assertEqual(projection["product_version"], 1)

        missing_version = self.get(
            f"/api/v2/sessions/{session_id}/canvas", params={"product_version": 9}
        )
        self.assertEqual(missing_version.status_code, 404, missing_version.text)

    def test_sse_disconnect_does_not_cancel_run(self) -> None:
        """客户端断开（CancelledError）只停止订阅，服务器 Run 状态不变。"""

        import asyncio
        import contextlib

        from api import sse

        session = self.create_session()
        with database.session_scope() as db:
            run = run_repo.create_run(
                db,
                session_id=session["id"],
                owner_email=self.email,
                request_id=C.new_request_id(),
                action=C.Action.clarify.value,
                input_hash="2" * 64,
                base_session_version=1,
                base_product_version=1,
                now_iso=C.utc_now_iso(),
            )
            run_id = run.id

        async def _scenario() -> None:
            async def _consume() -> None:
                async for _event in sse.iter_run_events(
                    run_id=run_id,
                    after_seq=0,
                    keepalive_seconds=0.01,
                    poll_interval=0.005,
                ):
                    pass

            task = asyncio.create_task(_consume())
            await asyncio.sleep(0.05)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        asyncio.run(_scenario())

        with database.session_scope() as db:
            run = run_repo.get_run(db, run_id)
            self.assertIsNotNone(run)
            self.assertEqual(run.status, C.RunStatus.queued.value)

    def test_keepalive_frame_is_comment_without_seq(self) -> None:
        from api import sse

        frame = sse.format_keepalive()
        self.assertEqual(frame, ": keepalive\n\n")
        self.assertNotIn("id:", frame)


class RunAndSseTests(V2ApiTestBase):
    def test_run_detail_and_snapshot_single_transaction(self) -> None:
        session = self.create_session()
        run_id = self.create_run_with_events(session["id"])

        detail = self.get(f"/api/v2/runs/{run_id}")
        self.assertEqual(detail.status_code, 200, detail.text)
        payload = detail.json()
        self.assertEqual(payload["status"], C.RunStatus.succeeded.value)
        self.assertEqual(payload["latest_event_seq"], 4)
        self.assertEqual(len(payload["steps"]), 0)

        snapshot = self.get(f"/api/v2/runs/{run_id}/snapshot")
        self.assertEqual(snapshot.status_code, 200, snapshot.text)
        snap = snapshot.json()
        for key in ("run_id", "session_id", "action", "status", "steps", "messages"):
            self.assertIn(key, snap)
        self.assertEqual(snap["run_id"], run_id)
        self.assertLessEqual(snap["snapshot_event_seq"], snap["latest_event_seq"])
        self.assertEqual(snap["session_version"], 1)
        self.assertEqual(snap["product_version"], 1)

        other = f"other-{uuid.uuid4().hex[:8]}@example.com"
        self.assertEqual(self.get(f"/api/v2/runs/{run_id}", email=other).status_code, 403)

    def test_sse_replays_from_after_seq_and_closes_on_terminal(self) -> None:
        session = self.create_session()
        run_id = self.create_run_with_events(session["id"])

        resp = self.get(f"/api/v2/runs/{run_id}/events", params={"after_seq": 1})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.headers["content-type"].startswith("text/event-stream"))
        self.assertEqual(resp.headers.get("x-accel-buffering"), "no")
        body = resp.text
        self.assertIn("id: 2", body)
        self.assertIn("id: 3", body)
        self.assertIn("id: 4", body)
        self.assertNotIn("id: 1\n", body)
        self.assertIn("event: run.completed", body)
        # 终结事件恰好一次
        self.assertEqual(body.count("event: run.completed"), 1)
        # 连接已关闭：响应体完整读完（TestClient 不流式挂起）
        self.assertEqual(resp.text.rstrip().endswith("}") or resp.text.endswith("\n\n"), True)

    def test_sse_cursor_uses_larger_of_header_and_query(self) -> None:
        session = self.create_session()
        run_id = self.create_run_with_events(session["id"])

        resp = self.get(
            f"/api/v2/runs/{run_id}/events",
            params={"after_seq": 2},
            headers={"Last-Event-ID": "1"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.text
        self.assertNotIn("id: 1\n", body)
        self.assertNotIn("id: 2\n", body)
        self.assertIn("id: 3", body)
        self.assertIn("id: 4", body)

        # 头更大时以头为准
        resp2 = self.get(
            f"/api/v2/runs/{run_id}/events",
            params={"after_seq": 1},
            headers={"Last-Event-ID": "2"},
        )
        self.assertEqual(resp2.status_code, 200, resp2.text)
        self.assertNotIn("id: 2\n", resp2.text)
        self.assertIn("id: 3", resp2.text)

    def test_sse_after_seq_zero_replays_from_start(self) -> None:
        """after_seq=0（或省略游标）合法：从头回放，包含 seq=1。"""

        session = self.create_session()
        run_id = self.create_run_with_events(session["id"])

        explicit = self.get(f"/api/v2/runs/{run_id}/events", params={"after_seq": 0})
        self.assertEqual(explicit.status_code, 200, explicit.text)
        body = explicit.text
        for seq in (1, 2, 3, 4):
            self.assertIn(f"id: {seq}\n", body)
        self.assertIn("event: run.queued", body)
        self.assertEqual(body.count("event: run.completed"), 1)

        omitted = self.get(f"/api/v2/runs/{run_id}/events")
        self.assertEqual(omitted.status_code, 200, omitted.text)
        self.assertIn("id: 1\n", omitted.text)

    def test_sse_cursor_expired_only_below_retention_watermark(self) -> None:
        """M1 水位=0 不过期；模拟水位>0 时 after_seq=0 → 410（AC-35 走快照恢复）。"""

        from api import sse

        session = self.create_session()
        run_id = self.create_run_with_events(session["id"])

        # M1：水位为 0，任何合法游标都可回放。
        ok = self.get(f"/api/v2/runs/{run_id}/events", params={"after_seq": 0})
        self.assertEqual(ok.status_code, 200, ok.text)

        # 模拟事件已被压缩：水位 3 表示 seq<=3 不可回放，after_seq 至少为 2。
        with mock.patch.object(sse, "RETENTION_WATERMARK", 3):
            expired = self.get(
                f"/api/v2/runs/{run_id}/events", params={"after_seq": 0}
            )
            self.assertEqual(expired.status_code, 410, expired.text)
            self.assertEqual(
                expired.json()["error"]["code"], ErrorCode.EVENT_CURSOR_EXPIRED
            )

            boundary = self.get(
                f"/api/v2/runs/{run_id}/events", params={"after_seq": 2}
            )
            self.assertEqual(boundary.status_code, 200, boundary.text)
            self.assertNotIn("id: 2\n", boundary.text)

    def test_sse_invalid_cursor_errors(self) -> None:
        session = self.create_session()
        run_id = self.create_run_with_events(session["id"])

        negative = self.get(
            f"/api/v2/runs/{run_id}/events", params={"after_seq": -1}
        )
        self.assertEqual(negative.status_code, 422, negative.text)
        self.assertEqual(
            negative.json()["error"]["code"], ErrorCode.INVALID_EVENT_CURSOR
        )

        not_int = self.get(
            f"/api/v2/runs/{run_id}/events", headers={"Last-Event-ID": "abc"}
        )
        self.assertEqual(not_int.status_code, 422, not_int.text)
        self.assertEqual(
            not_int.json()["error"]["code"], ErrorCode.INVALID_EVENT_CURSOR
        )

        too_large = self.get(
            f"/api/v2/runs/{run_id}/events", params={"after_seq": 999}
        )
        self.assertEqual(too_large.status_code, 422, too_large.text)
        self.assertEqual(
            too_large.json()["error"]["code"], ErrorCode.INVALID_EVENT_CURSOR
        )

    def test_cancel_terminal_run_is_idempotent_200(self) -> None:
        session = self.create_session()
        run_id = self.create_run_with_events(session["id"])
        resp = self.post(f"/api/v2/runs/{run_id}/cancel")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], C.RunStatus.succeeded.value)


class DeleteAndAuthMigrationTests(V2ApiTestBase):
    def test_delete_session_requires_idempotency_key_and_is_soft(self) -> None:
        session = self.create_session()
        session_id = session["id"]

        missing_key = self.delete(f"/api/v2/sessions/{session_id}")
        self.assertEqual(missing_key.status_code, 422, missing_key.text)
        self.assertEqual(
            missing_key.json()["error"]["code"], ErrorCode.INVALID_REQUEST
        )

        headers = {"Idempotency-Key": str(uuid.uuid4())}
        first = self.delete(f"/api/v2/sessions/{session_id}", headers=headers)
        self.assertEqual(first.status_code, 202, first.text)
        payload = first.json()
        self.assertEqual(payload["session_id"], session_id)
        self.assertTrue(payload["deleted_at"])

        # 同 key 重放：返回同一 body
        replay = self.delete(f"/api/v2/sessions/{session_id}", headers=headers)
        self.assertEqual(replay.status_code, 202, replay.text)
        self.assertEqual(replay.json()["deleted_at"], payload["deleted_at"])

        # 软删：数据库仍有行
        with database.session_scope() as db:
            row = session_repo.get_session(db, session_id, include_deleted=True)
            self.assertIsNotNone(row)
            self.assertIsNotNone(row.deleted_at)

        # 所有读端点 404
        self.assertEqual(self.get(f"/api/v2/sessions/{session_id}").status_code, 404)
        self.assertEqual(
            self.get(f"/api/v2/sessions/{session_id}/messages").status_code, 404
        )
        self.assertEqual(
            self.get(f"/api/v2/sessions/{session_id}/product-state").status_code, 404
        )

    def test_verification_code_survives_process_cache_clear(self) -> None:
        email = f"code-{uuid.uuid4().hex[:8]}@example.com"
        captured: dict[str, str] = {}

        def fake_send(target: str, code: str) -> None:
            captured["email"] = target
            captured["code"] = code

        with mock.patch.object(auth, "_send_verification_email", side_effect=fake_send):
            self.assertEqual(auth.issue_verification_code(email), 60)

        self.assertEqual(captured["email"], email)
        code = captured["code"]

        # 模拟进程内缓存被清空（跨进程/重启场景）：DB 仍是权威。
        auth._verification_codes.clear()
        self.assertTrue(auth.verify_email_code(email, code))
        # 消费后不可复用
        self.assertFalse(auth.verify_email_code(email, code))

    def test_verification_code_rejects_too_many_attempts(self) -> None:
        email = f"attempts-{uuid.uuid4().hex[:8]}@example.com"
        captured: dict[str, str] = {}

        def fake_send(target: str, code: str) -> None:
            captured["code"] = code

        with mock.patch.object(auth, "_send_verification_email", side_effect=fake_send):
            auth.issue_verification_code(email)

        for _ in range(auth.MAX_VERIFY_ATTEMPTS):
            self.assertFalse(auth.verify_email_code(email, "000000"))
        # 超过尝试次数后，即使输入正确验证码也失败（防止爆破）。
        self.assertFalse(auth.verify_email_code(email, captured["code"]))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
