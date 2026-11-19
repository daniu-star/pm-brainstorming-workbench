"""旧库自动补列（additive schema upgrade）回归测试。

真实事故：``backend/data/app.db`` 是在 ``steps.created_at`` 加入 ORM 之前创建的，
``create_all`` 不会给已存在的表加列，于是每次 ``ensure_step`` / ``get_step`` 都抛
``OperationalError: no such column: steps.created_at``，用户侧表现就是「发消息一直失败」。
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import unittest

from repositories import database as db


def _legacy_database(path: str) -> None:
    """造一个「缺 steps.created_at」的旧库（其余表交给 create_all 建）。"""

    con = sqlite3.connect(path)
    con.execute(
        """
        CREATE TABLE steps (
            id VARCHAR(64) PRIMARY KEY,
            run_id VARCHAR(64) NOT NULL,
            logical_key VARCHAR(128) NOT NULL,
            kind VARCHAR(32) NOT NULL,
            role_code VARCHAR(32),
            status VARCHAR(32) NOT NULL DEFAULT 'pending',
            attempt_count INTEGER NOT NULL DEFAULT 0,
            input_hash VARCHAR(64) NOT NULL DEFAULT '',
            input_snapshot_json TEXT NOT NULL DEFAULT '{}',
            output_json TEXT,
            error_code VARCHAR(64),
            error_message TEXT,
            previous_step_id VARCHAR(64),
            started_at VARCHAR(32),
            finished_at VARCHAR(32)
        )
        """
    )
    con.execute(
        "INSERT INTO steps (id, run_id, logical_key, kind, started_at) "
        "VALUES ('step-old', 'run-old', 'clarify.generate', 'coach_turn', '2026-11-01T00:00:00Z')"
    )
    con.execute(
        "INSERT INTO steps (id, run_id, logical_key, kind) "
        "VALUES ('step-blank', 'run-old', 'clarify.followup', 'coach_turn')"
    )
    con.commit()
    con.close()


class SchemaUpgradeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="schema_upgrade_")
        self.path = os.path.join(self.tmp, "legacy.db")
        _legacy_database(self.path)
        self.url = f"sqlite+pysqlite:///{self.path}"
        # 必须先切到本用例的库：否则 db.get_engine() 会回落 default_database_url()
        # （真实 backend/data/app.db），前置断言就会读到已升级的库，出现假失败。
        db.configure(self.url, force=True)

    def tearDown(self) -> None:
        db.dispose_engine()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _initialize(self) -> None:
        engine = db.configure(self.url, force=True)
        db.initialize_database(engine)

    def _step_columns(self) -> set[str]:
        engine = db.get_engine()
        with engine.connect() as conn:
            return {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(steps)")}

    def _step_rows(self) -> dict[str, str]:
        engine = db.get_engine()
        with engine.connect() as conn:
            rows = conn.exec_driver_sql("SELECT id, created_at FROM steps").fetchall()
        return {row[0]: row[1] for row in rows}

    def test_legacy_steps_table_gets_created_at_column(self) -> None:
        self.assertNotIn("created_at", self._step_columns())

        self._initialize()

        self.assertIn("created_at", self._step_columns())

    def test_backfill_uses_started_at_or_finished_at(self) -> None:
        self._initialize()

        rows = self._step_rows()
        self.assertEqual(rows["step-old"], "2026-11-01T00:00:00Z")
        self.assertTrue(rows["step-blank"])  # NOT NULL 违约的回填兜底值

    def test_upgrade_is_idempotent(self) -> None:
        self._initialize()
        first = self._step_columns()

        # 第二次启动（真实场景：每次进程启动都会跑一遍）不应报错、也不应重复加列
        self._initialize()

        self.assertEqual(self._step_columns(), first)
        self.assertEqual(self._step_rows()["step-old"], "2026-11-01T00:00:00Z")

    def test_fresh_database_has_created_at(self) -> None:
        engine = db.configure(f"sqlite+pysqlite:///{os.path.join(self.tmp, 'fresh.db')}", force=True)
        db.initialize_database(engine)

        self.assertIn("created_at", self._step_columns())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
