"""Worker 维护性清理：幂等记录与过期验证码。

背景：``idempotency_records`` 每条命令一行、``auth_codes`` 每次登录一行，两者都没有
读者会在过期后再看它们；不清理时库会随使用线性增长（长期运行把磁盘吃满，且拖慢
既有索引）。修复：``RunWorker.tick()`` 每 ``MAINTENANCE_INTERVAL_SECONDS`` 调一次
``run_repo.prune_idempotency`` 与 ``auth_repo.prune_expired_codes``，与 ``compact_events``
同一模式（按间隔触发、异常只记日志、绝不阻塞任务执行）。

用例全部真 sqlite + 真 ORM，不联网、不跑模型。
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
import unittest

from sqlalchemy import select

import domain.contracts as C
from repositories import auth_repo, database, run_repo
from repositories.database import (
    AuthCodeRow,
    IdempotencyRow,
    session_scope,
)
from workers.run_worker import RunWorker

OWNER = "worker-maintenance@example.com"


class _NoExecWorker(RunWorker):
    """只跑维护阶段：本文件不关心 Run 执行（那属于 test_runner 的职责）。"""

    def __init__(self) -> None:
        super().__init__(worker_id="maintenance-test", interval=0.1, max_runs=1)

    def _execute(self, run_id: str, *, lease_epoch: int) -> None:  # noqa: D401 - 覆写
        raise AssertionError(f"维护用例不应执行 Run：{run_id}")


class WorkerMaintenanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.makedirs(".tmp_tests", exist_ok=True)
        cls._tmpdir = tempfile.mkdtemp(
            prefix="worker_maint_", dir=os.path.abspath(".tmp_tests")
        )
        cls._db_url = "sqlite+pysqlite:///" + os.path.join(cls._tmpdir, "m.db").replace(
            "\\", "/"
        )
        database.configure(cls._db_url, force=True)
        database.initialize_database(database.get_engine(), drop_first=True)

    @classmethod
    def tearDownClass(cls):
        database.dispose_engine()
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def setUp(self):
        with session_scope() as db:
            for row in db.execute(select(IdempotencyRow)).scalars().all():
                db.delete(row)
            for row in db.execute(select(AuthCodeRow)).scalars().all():
                db.delete(row)

    # ------------------------------------------------------------------ 夹具

    def _add_idempotency(self, request_id: str, *, created_at: str) -> None:
        with session_scope() as db:
            db.add(
                IdempotencyRow(
                    owner_email=OWNER,
                    request_id=request_id,
                    action="clarify",
                    resource_scope="",
                    input_hash="hash",
                    response_status=202,
                    response_json="{}",
                    created_at=created_at,
                    expires_at=int(time.time()) + 3600,
                )
            )

    def _add_code(self, code_id: str, *, expires_at: int) -> None:
        now = int(time.time())
        with session_scope() as db:
            db.add(
                AuthCodeRow(
                    id=code_id,
                    email=OWNER,
                    code_hash="hash",
                    expires_at=expires_at,
                    attempts=0,
                    consumed_at=None,
                    created_at=now,
                    last_sent_at=now,
                )
            )

    def _idempotency_ids(self) -> set[str]:
        with session_scope() as db:
            return set(db.execute(select(IdempotencyRow.request_id)).scalars().all())

    def _code_ids(self) -> set[str]:
        with session_scope() as db:
            return set(db.execute(select(AuthCodeRow.id)).scalars().all())

    # ------------------------------------------------------------------ 用例

    def test_prune_idempotency_removes_only_rows_outside_retention(self):
        self._add_idempotency("old-1", created_at="2020-01-01T00:00:00Z")
        self._add_idempotency("old-2", created_at="2020-01-02T00:00:00Z")
        self._add_idempotency("fresh", created_at=C.utc_now_iso())

        with session_scope() as db:
            removed = run_repo.prune_idempotency(
                db, before_iso="2021-01-01T00:00:00Z"
            )

        self.assertEqual(removed, 2)
        self.assertEqual(self._idempotency_ids(), {"fresh"})

    def test_prune_idempotency_is_bounded_and_idempotent(self):
        for index in range(5):
            self._add_idempotency(f"old-{index}", created_at="2020-01-01T00:00:00Z")

        with session_scope() as db:
            first = run_repo.prune_idempotency(
                db, before_iso="2021-01-01T00:00:00Z", limit=2
            )
        self.assertEqual(first, 2)
        with session_scope() as db:
            second = run_repo.prune_idempotency(db, before_iso="2021-01-01T00:00:00Z")
        self.assertEqual(second, 3)
        with session_scope() as db:
            third = run_repo.prune_idempotency(db, before_iso="2021-01-01T00:00:00Z")
        self.assertEqual(third, 0, "清空后必须幂等返回 0")

    def test_prune_expired_codes_keeps_unexpired(self):
        now = int(time.time())
        self._add_code("expired-1", expires_at=now - 600)
        self._add_code("expired-2", expires_at=now - 1)
        self._add_code("active", expires_at=now + 600)

        with session_scope() as db:
            removed = auth_repo.prune_expired_codes(db, now_epoch=now)

        self.assertEqual(removed, 2)
        self.assertEqual(self._code_ids(), {"active"})

    def test_tick_runs_maintenance_once_per_interval(self):
        self._add_idempotency("old", created_at="2020-01-01T00:00:00Z")
        now = int(time.time())
        self._add_code("expired", expires_at=now - 600)

        worker = _NoExecWorker()
        worker._last_maintenance = 0.0  # 模拟「距上次维护已超过间隔」
        worker.tick()

        self.assertEqual(self._idempotency_ids(), set())
        self.assertEqual(self._code_ids(), set())
        self.assertGreater(worker._last_maintenance, 0.0)

        # 再塞一条老数据，但刚跑过维护 → 本轮按间隔跳过。
        self._add_idempotency("old-2", created_at="2020-01-01T00:00:00Z")
        worker.tick()
        self.assertEqual(self._idempotency_ids(), {"old-2"})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
