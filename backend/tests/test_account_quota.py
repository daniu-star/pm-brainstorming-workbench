"""账户级配额（第 32 批）：计量口径 / 门禁 / API。

覆盖三件事：
1. 计量口径：终结 Run 用真实 usage，在途/排队 Run 按预算上限预占；
2. 门禁：模型类命令超额抛 ``ACCOUNT_QUOTA_EXCEEDED``，同步命令仍可执行；
3. 接口：``GET /api/v2/quota`` 返回可用额度，超额时命令返回 429。
"""
from __future__ import annotations

import json
import shutil
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from api.v2 import common as v2_common
from core.config import settings
from domain import contracts as C
from domain.errors import AppError, ErrorCode
from repositories import database, run_repo, session_repo
from repositories.database import RunRow
from services import quota_service
from tests.test_api_v2 import V2ApiTestBase

BACKEND = Path(__file__).resolve().parent.parent


class _QuotaCase(unittest.TestCase):
    """临时库 + 一个会话，用于直接读计量结果。"""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="quota_", dir=str(BACKEND / ".tmp_tests"))
        database.configure(
            f"sqlite+pysqlite:///{(Path(self._tmpdir) / 'quota.db').as_posix()}", force=True
        )
        database.initialize_database(drop_first=True)
        self.owner = f"quota-{uuid.uuid4().hex[:8]}@example.com"
        with database.session_scope() as db:
            session = session_repo.create_session(
                db, owner_email=self.owner, problem_statement="配额测试会话"
            )
            self.session_id = session.id

    def tearDown(self) -> None:
        database.dispose_engine()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _add_run(self, *, status: str, usage: dict | None = None, budget: dict | None = None) -> str:
        with database.session_scope() as db:
            run = run_repo.create_run(
                db,
                session_id=self.session_id,
                owner_email=self.owner,
                request_id=f"quota-{uuid.uuid4().hex[:12]}",
                action=C.Action.clarify.value,
                input_hash="quota-hash",
                base_session_version=1,
                base_product_version=1,
                budget=budget
                or {"max_run_input_tokens": 1000, "max_run_output_tokens": 200, "max_upstream_calls": 2},
            )
            row = db.get(RunRow, run.id)
            row.status = C.RunStatus(status).value
            if usage is not None:
                row.usage_json = json.dumps(usage, ensure_ascii=False)
            return run.id

    def _limits(self, *, tokens: int = 0, calls: int = 0) -> None:
        original = (settings.account_daily_token_limit, settings.account_daily_call_limit)
        settings.account_daily_token_limit = tokens
        settings.account_daily_call_limit = calls
        self.addCleanup(
            lambda: (
                setattr(settings, "account_daily_token_limit", original[0]),
                setattr(settings, "account_daily_call_limit", original[1]),
            )
        )


class UsageTests(_QuotaCase):
    def test_terminal_usage_and_queued_reservation_are_both_counted(self) -> None:
        self._add_run(
            status="succeeded",
            usage={"prompt_tokens": 100, "completion_tokens": 50, "upstream_calls": 1},
        )
        self._add_run(status="queued")
        with database.session_scope() as db:
            stats = quota_service.usage_since(
                db, owner_email=self.owner, since_iso="2000-01-01T00:00:00Z"
            )
        self.assertEqual(stats["used_tokens"], 150)
        self.assertEqual(stats["used_calls"], 1)
        # queued Run 按预算上限预占：1000 + 200 token、2 次调用
        self.assertEqual(stats["reserved_tokens"], 1200)
        self.assertEqual(stats["reserved_calls"], 2)

    def test_other_owners_are_not_counted(self) -> None:
        self._add_run(status="succeeded", usage={"prompt_tokens": 500, "completion_tokens": 500})
        with database.session_scope() as db:
            stats = quota_service.usage_since(
                db, owner_email="someone-else@example.com", since_iso="2000-01-01T00:00:00Z"
            )
        self.assertEqual(stats["scanned_runs"], 0)
        self.assertEqual(stats["used_tokens"], 0)


class GateTests(_QuotaCase):
    def test_snapshot_is_disabled_by_default(self) -> None:
        with database.session_scope() as db:
            snap = quota_service.snapshot(
                db, owner_email=self.owner, now_epoch=int(time.time())
            )
        self.assertFalse(snap["enabled"])
        self.assertFalse(snap["exceeded"])
        self.assertEqual(snap["window_hours"], 24)
        self.assertTrue(snap["reset_at"].endswith("Z"))

    def test_async_action_is_rejected_when_quota_is_used_up(self) -> None:
        self._limits(tokens=1000, calls=0)
        # 一条在途 Run 的预算预占（1200 token）就已经超过 1000 上限
        self._add_run(status="queued")
        with database.session_scope() as db:
            with self.assertRaises(AppError) as ctx:
                quota_service.ensure_within_quota(
                    db,
                    owner_email=self.owner,
                    action=C.Action.brainstorm.value,
                    now_epoch=int(time.time()),
                )
        self.assertEqual(ctx.exception.code, ErrorCode.ACCOUNT_QUOTA_EXCEEDED)
        self.assertEqual(ctx.exception.http_status, 429)
        self.assertIn("额度", ctx.exception.message)
        self.assertEqual(ctx.exception.details["token_limit"], 1000)
        self.assertGreaterEqual(ctx.exception.details["total_tokens"], 1000)

    def test_sync_action_still_allowed_when_quota_is_used_up(self) -> None:
        self._limits(tokens=1000, calls=0)
        self._add_run(status="queued")
        with database.session_scope() as db:
            snap = quota_service.ensure_within_quota(
                db,
                owner_email=self.owner,
                action=C.Action.revise_brief.value,
                now_epoch=int(time.time()),
            )
        self.assertTrue(snap["exceeded"])
        self.assertTrue(
            C.Action.revise_brief.value in quota_service.EXEMPT_ACTION_VALUES
        )

    def test_call_limit_alone_can_reject(self) -> None:
        self._limits(tokens=0, calls=1)
        self._add_run(status="succeeded", usage={"upstream_calls": 1, "prompt_tokens": 10})
        with database.session_scope() as db:
            with self.assertRaises(AppError) as ctx:
                quota_service.ensure_within_quota(
                    db,
                    owner_email=self.owner,
                    action=C.Action.clarify.value,
                    now_epoch=int(time.time()),
                )
        self.assertEqual(ctx.exception.code, ErrorCode.ACCOUNT_QUOTA_EXCEEDED)
        self.assertEqual(ctx.exception.details["call_limit"], 1)


class QuotaApiTests(V2ApiTestBase):
    def setUp(self) -> None:
        super().setUp()
        database.reset_for_tests(
            f"sqlite+pysqlite:///{(Path(self._tmpdir) / 'test.db').as_posix()}"
        )
        v2_common.reset_module_cache()

    def test_quota_endpoint_reports_usage_and_limits(self) -> None:
        created = self.create_session()
        session_id = created["id"]
        original = (settings.account_daily_token_limit, settings.account_daily_call_limit)
        settings.account_daily_token_limit = 5_000_000
        settings.account_daily_call_limit = 100
        self.addCleanup(
            lambda: (
                setattr(settings, "account_daily_token_limit", original[0]),
                setattr(settings, "account_daily_call_limit", original[1]),
            )
        )
        resp = self.get("/api/v2/quota")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body["enabled"])
        self.assertEqual(body["token_limit"], 5_000_000)
        self.assertEqual(body["call_limit"], 100)
        self.assertGreaterEqual(body["reserved_tokens"], 0)
        self.assertIn("reset_at", body)
        # 新建会话时已排入一个 clarify Run（预算 60000+14000），应体现在预占里
        self.assertGreater(body["reserved_tokens"], 0)
        self.assertFalse(body["exceeded"])
        self.assertTrue(session_id)

    def test_command_is_rejected_with_429_when_quota_used_up(self) -> None:
        created = self.create_session()
        session_id = created["id"]
        original = (settings.account_daily_token_limit, settings.account_daily_call_limit)
        settings.account_daily_token_limit = 1_000
        settings.account_daily_call_limit = 0
        self.addCleanup(
            lambda: (
                setattr(settings, "account_daily_token_limit", original[0]),
                setattr(settings, "account_daily_call_limit", original[1]),
            )
        )
        resp = self.post(
            f"/api/v2/sessions/{session_id}/commands",
            json={
                "request_id": f"quota-api-{uuid.uuid4().hex[:10]}",
                "action": "brainstorm",
                "expected_session_version": created["session_version"],
                "payload": {"content": "请四位专家给出评审意见。", "target": "all"},
            },
        )
        self.assertEqual(resp.status_code, 429, resp.text)
        body = resp.json()
        self.assertEqual(body["error"]["code"], ErrorCode.ACCOUNT_QUOTA_EXCEEDED)
        self.assertIn("额度", body["error"]["message"])


    def test_creating_session_is_rejected_when_quota_used_up(self) -> None:
        original = (settings.account_daily_token_limit, settings.account_daily_call_limit)
        settings.account_daily_token_limit = 1_000
        settings.account_daily_call_limit = 0
        self.addCleanup(
            lambda: (
                setattr(settings, "account_daily_token_limit", original[0]),
                setattr(settings, "account_daily_call_limit", original[1]),
            )
        )
        # 第一个会话此时没有已用/预占，可以创建；但它会排入一个 clarify Run（预占 74000 token）
        first = self.post("/api/v2/sessions", json={"problem_statement": "配额门禁会话一"})
        self.assertEqual(first.status_code, 201, first.text)
        # 第二个会话立刻被拒：预占已经超过 1000 token 上限
        second = self.post("/api/v2/sessions", json={"problem_statement": "配额门禁会话二"})
        self.assertEqual(second.status_code, 429, second.text)
        self.assertEqual(
            second.json()["error"]["code"], ErrorCode.ACCOUNT_QUOTA_EXCEEDED
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
