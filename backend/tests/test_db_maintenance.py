"""`scripts/db_maintenance.py` 的回归（第 26 批）。

覆盖：报告能列出表名/字节/行数；VACUUM 能回收空闲页并保持库可读；``--json`` 输出可解析。
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "db_maintenance.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("db_maintenance_under_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # dataclass/反射场景需要模块先登记到 sys.modules，否则注解解析会拿到 None。
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class DbMaintenanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _load_module()

    def setUp(self) -> None:
        root = REPO / "backend" / ".tmp_tests"
        root.mkdir(exist_ok=True)
        self.tmpdir = Path(tempfile.mkdtemp(prefix="db_maint_", dir=str(root)))
        self.db_path = self.tmpdir / "maint.db"
        con = sqlite3.connect(self.db_path)
        try:
            con.execute("create table payloads (id integer primary key, body text not null)")
            con.executemany(
                "insert into payloads (body) values (?)",
                [("x" * 400,) for _ in range(50)],
            )
            con.commit()
            con.execute("delete from payloads where id > 10")
            con.commit()
        finally:
            con.close()

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_report_lists_tables_rows_and_bytes(self) -> None:
        report = self.mod.build_report(self.db_path)
        self.assertEqual(report["path"], str(self.db_path))
        self.assertGreater(report["file_bytes"], 0)
        self.assertIn("payloads", report["biggest"][0]["name"])
        self.assertGreater(report["biggest"][0]["bytes"], 0)
        # 表在 REPORT_TABLES 里没登记时不进「业务表」段，但仍会出现在 biggest 排行里。
        self.assertIn("freelist_bytes", report)
        text = self.mod.format_report(report)
        self.assertIn("数据库：", text)
        self.assertIn("最占空间：", text)

    def test_vacuum_reclaims_free_pages_and_keeps_db_readable(self) -> None:
        before = self.mod.build_report(self.db_path)
        self.assertGreater(before["freelist_pages"], 0, "删除行后应留下空闲页")
        result = self.mod.run_vacuum(self.db_path)
        self.assertGreaterEqual(result["saved_bytes"], 0)
        after = self.mod.build_report(self.db_path)
        self.assertEqual(after["freelist_pages"], 0)
        con = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(con.execute("select count(*) from payloads").fetchone()[0], 10)
            self.assertEqual(con.execute("pragma integrity_check").fetchone()[0], "ok")
        finally:
            con.close()

    def test_report_json_is_serializable(self) -> None:
        report = self.mod.build_report(self.db_path)
        payload = json.loads(json.dumps(report, ensure_ascii=False))
        self.assertEqual(payload["path"], str(self.db_path))
        self.assertIsInstance(payload["tables"], dict)
        self.assertTrue(all(isinstance(item["bytes"], int) for item in payload["biggest"]))


if __name__ == "__main__":
    unittest.main()
