"""``auth_repo`` 的集成测试：真实 SQLite 临时库（跨连接/跨进程共享语义）。

覆盖契约 §3.4 的关键不变量：

- 验证码落库而非进程内内存——另一个数据库连接可直接读到；
- 签发新码即失效旧码；过期码不再有效；消费幂等；
- 错误尝试计数与 ``MAX_CODE_ATTEMPTS`` 上限（5 次后连正确码也拒绝）；
- 限流按整窗口对齐、达上限后拒绝且不越界计数。
"""

from __future__ import annotations

import contextlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import select

from domain.errors import AppError, ErrorCode
from repositories import auth_repo, database
from repositories.database import AuthCodeRow, RateLimitRow

EMAIL = "user@example.com"
CODE_HASH_OK = "a" * 64
CODE_HASH_BAD = "b" * 64
NOW = 1_800_000_000


class AuthRepoTestCase(unittest.TestCase):
    """每个测试一个独立临时 SQLite 文件。"""

    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tempdir.name) / "auth.db"
        database.configure(f"sqlite:///{self.db_path}", force=True)
        database.initialize_database()

    def tearDown(self) -> None:
        # 恢复全局默认配置，避免把指向已删除临时库的 engine 留给后续测试模块。
        with contextlib.suppress(Exception):
            database.configure(database.default_database_url(), force=True)
        self._tempdir.cleanup()

    # ------------------------------------------------------------------ #
    # 验证码生命周期
    # ------------------------------------------------------------------ #
    def test_issue_and_get_active_code(self) -> None:
        with database.session_scope() as db:
            auth_repo.issue_code(
                db,
                email=EMAIL,
                code_hash=CODE_HASH_OK,
                ttl_seconds=300,
                now_epoch=NOW,
            )
            row = auth_repo.get_active_code(db, EMAIL.upper(), NOW + 10)
            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(row.code_hash, CODE_HASH_OK)
            self.assertEqual(row.attempts, 0)
            self.assertEqual(row.expires_at, NOW + 300)

    def test_code_is_shared_across_connections(self) -> None:
        """验证码写库后，另一个独立连接（模拟其他进程）也能读到。"""

        with database.session_scope() as db:
            auth_repo.issue_code(
                db,
                email=EMAIL,
                code_hash=CODE_HASH_OK,
                ttl_seconds=300,
                now_epoch=NOW,
            )

        raw = sqlite3.connect(str(self.db_path))
        try:
            rows = raw.execute(
                "SELECT email, code_hash, expires_at FROM auth_codes ORDER BY created_at DESC"
            ).fetchall()
        finally:
            raw.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], EMAIL)
        self.assertEqual(rows[0][1], CODE_HASH_OK)
        self.assertEqual(rows[0][2], NOW + 300)

    def test_issue_code_invalidates_previous_codes(self) -> None:
        with database.session_scope() as db:
            auth_repo.issue_code(db, email=EMAIL, code_hash=CODE_HASH_BAD, ttl_seconds=300, now_epoch=NOW)
            auth_repo.issue_code(
                db, email=EMAIL, code_hash=CODE_HASH_OK, ttl_seconds=300, now_epoch=NOW + 1
            )

            active = auth_repo.get_active_code(db, EMAIL, NOW + 5)
            self.assertIsNotNone(active)
            assert active is not None
            self.assertEqual(active.code_hash, CODE_HASH_OK)

            all_rows = list(
                db.execute(select(AuthCodeRow).order_by(AuthCodeRow.created_at.asc())).scalars()
            )
            self.assertEqual([row.code_hash for row in all_rows], [CODE_HASH_BAD, CODE_HASH_OK])
            self.assertIsNotNone(all_rows[0].consumed_at)
            self.assertIsNone(all_rows[1].consumed_at)

    def test_expired_code_is_not_active(self) -> None:
        with database.session_scope() as db:
            auth_repo.issue_code(db, email=EMAIL, code_hash=CODE_HASH_OK, ttl_seconds=60, now_epoch=NOW)
            self.assertIsNotNone(auth_repo.get_active_code(db, EMAIL, NOW + 59))
            self.assertIsNone(auth_repo.get_active_code(db, EMAIL, NOW + 60))

    def test_consume_code_is_idempotent(self) -> None:
        with database.session_scope() as db:
            auth_repo.issue_code(db, email=EMAIL, code_hash=CODE_HASH_OK, ttl_seconds=300, now_epoch=NOW)
            row = auth_repo.get_active_code(db, EMAIL, NOW)
            assert row is not None

            auth_repo.consume_code(db, row, NOW + 1)
            self.assertEqual(row.consumed_at, NOW + 1)
            self.assertIsNone(auth_repo.get_active_code(db, EMAIL, NOW + 1))

            auth_repo.consume_code(db, row, NOW + 2)
            self.assertEqual(row.consumed_at, NOW + 1)

    def test_issue_code_rejects_empty_email(self) -> None:
        with database.session_scope() as db:
            with self.assertRaises(AppError) as ctx:
                auth_repo.issue_code(db, email="  ", code_hash=CODE_HASH_OK, ttl_seconds=60, now_epoch=NOW)
            self.assertEqual(ctx.exception.code, ErrorCode.INVALID_REQUEST)

    # ------------------------------------------------------------------ #
    # 错误尝试上限
    # ------------------------------------------------------------------ #
    def test_record_attempt_increments(self) -> None:
        with database.session_scope() as db:
            auth_repo.issue_code(db, email=EMAIL, code_hash=CODE_HASH_OK, ttl_seconds=300, now_epoch=NOW)
            row = auth_repo.get_active_code(db, EMAIL, NOW)
            assert row is not None

            self.assertEqual(auth_repo.record_attempt(db, row, NOW + 1), 1)
            self.assertEqual(auth_repo.record_attempt(db, row, NOW + 2), 2)
            self.assertEqual(row.attempts, 2)

    def test_verify_code_rejects_after_five_wrong_attempts(self) -> None:
        with database.session_scope() as db:
            auth_repo.issue_code(db, email=EMAIL, code_hash=CODE_HASH_OK, ttl_seconds=300, now_epoch=NOW)

            for _ in range(auth_repo.MAX_CODE_ATTEMPTS):
                self.assertFalse(
                    auth_repo.verify_code(db, email=EMAIL, code_hash=CODE_HASH_BAD, now_epoch=NOW + 1)
                )

            row = auth_repo.get_active_code(db, EMAIL, NOW + 2)
            assert row is not None
            self.assertEqual(row.attempts, auth_repo.MAX_CODE_ATTEMPTS)

            # 超过上限后即使提供正确码也拒绝，并失效该码。
            self.assertFalse(
                auth_repo.verify_code(db, email=EMAIL, code_hash=CODE_HASH_OK, now_epoch=NOW + 3)
            )
            self.assertIsNone(auth_repo.get_active_code(db, EMAIL, NOW + 4))

    def test_verify_code_consumes_on_success(self) -> None:
        with database.session_scope() as db:
            auth_repo.issue_code(db, email=EMAIL, code_hash=CODE_HASH_OK, ttl_seconds=300, now_epoch=NOW)
            self.assertTrue(
                auth_repo.verify_code(db, email=EMAIL, code_hash=CODE_HASH_OK, now_epoch=NOW + 1)
            )
            self.assertIsNone(auth_repo.get_active_code(db, EMAIL, NOW + 2))

    def test_verify_code_without_code_returns_false(self) -> None:
        with database.session_scope() as db:
            self.assertFalse(
                auth_repo.verify_code(db, email=EMAIL, code_hash=CODE_HASH_OK, now_epoch=NOW)
            )

    # ------------------------------------------------------------------ #
    # 限流窗口
    # ------------------------------------------------------------------ #
    def test_rate_limit_allows_up_to_limit_within_window(self) -> None:
        with database.session_scope() as db:
            for _ in range(3):
                self.assertTrue(
                    auth_repo.hit_rate_limit(
                        db,
                        scope="commands",
                        key=EMAIL,
                        limit=3,
                        window_seconds=60,
                        now_epoch=NOW + 5,
                    )
                )
            self.assertFalse(
                auth_repo.hit_rate_limit(
                    db,
                    scope="commands",
                    key=EMAIL,
                    limit=3,
                    window_seconds=60,
                    now_epoch=NOW + 9,
                )
            )

            row = db.execute(
                select(RateLimitRow).where(RateLimitRow.scope == "commands", RateLimitRow.key == EMAIL)
            ).scalar_one()
            self.assertEqual(row.window_start, NOW // 60 * 60)
            self.assertEqual(row.count, 3)

    def test_rate_limit_resets_in_next_window(self) -> None:
        with database.session_scope() as db:
            window_start = NOW // 60 * 60
            self.assertTrue(
                auth_repo.hit_rate_limit(
                    db, scope="commands", key=EMAIL, limit=1, window_seconds=60, now_epoch=NOW
                )
            )
            self.assertFalse(
                auth_repo.hit_rate_limit(
                    db, scope="commands", key=EMAIL, limit=1, window_seconds=60, now_epoch=NOW + 30
                )
            )
            # 下一个整窗口重新计数。
            self.assertTrue(
                auth_repo.hit_rate_limit(
                    db,
                    scope="commands",
                    key=EMAIL,
                    limit=1,
                    window_seconds=60,
                    now_epoch=window_start + 60,
                )
            )

    def test_rate_limit_isolated_by_scope_and_key(self) -> None:
        with database.session_scope() as db:
            self.assertTrue(
                auth_repo.hit_rate_limit(
                    db, scope="commands", key=EMAIL, limit=1, window_seconds=60, now_epoch=NOW
                )
            )
            self.assertTrue(
                auth_repo.hit_rate_limit(
                    db, scope="commands", key="other@example.com", limit=1, window_seconds=60, now_epoch=NOW
                )
            )
            self.assertTrue(
                auth_repo.hit_rate_limit(
                    db, scope="auth", key=EMAIL, limit=1, window_seconds=60, now_epoch=NOW
                )
            )
            self.assertFalse(
                auth_repo.hit_rate_limit(
                    db, scope="commands", key=EMAIL, limit=1, window_seconds=60, now_epoch=NOW
                )
            )

    def test_rate_limit_validates_arguments(self) -> None:
        with database.session_scope() as db:
            for kwargs in (
                {"scope": "", "key": EMAIL, "limit": 1, "window_seconds": 60, "now_epoch": NOW},
                {"scope": "commands", "key": "", "limit": 1, "window_seconds": 60, "now_epoch": NOW},
                {"scope": "commands", "key": EMAIL, "limit": 0, "window_seconds": 60, "now_epoch": NOW},
                {"scope": "commands", "key": EMAIL, "limit": 1, "window_seconds": 0, "now_epoch": NOW},
            ):
                with self.assertRaises(AppError) as ctx:
                    auth_repo.hit_rate_limit(db, **kwargs)  # type: ignore[arg-type]
                self.assertEqual(ctx.exception.code, ErrorCode.INVALID_REQUEST)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()