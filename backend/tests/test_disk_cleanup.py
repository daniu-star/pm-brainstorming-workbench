"""scripts/disk_cleanup.py 的自检（只测编排逻辑，不动真实临时目录）。"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import tempfile
import time
import unittest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
REPO = BACKEND.parent
SCRIPT = REPO / "scripts" / "disk_cleanup.py"


def load_module():
    spec = importlib.util.spec_from_file_location("disk_cleanup", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class DiskCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = load_module()

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="disk-cleanup-test-")
        self.root = pathlib.Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_path_stats_counts_files_and_bytes(self) -> None:
        target = self.root / "dir"
        (target / "nested").mkdir(parents=True)
        (target / "a.txt").write_bytes(b"12345")
        (target / "nested" / "b.bin").write_bytes(b"123")
        self.assertEqual(self.mod._path_stats(target), (2, 8))
        self.assertEqual(self.mod._path_stats(self.root / "missing"), (0, 0))

    def test_plan_backup_rotation_keeps_newest(self) -> None:
        root = self.root / "backups"
        root.mkdir()
        names = ["20260101-000000", "20260102-000000", "20260103-000000"]
        for index, name in enumerate(names):
            backup = root / name
            backup.mkdir()
            (backup / "manifest.json").write_text("{}", encoding="utf-8")
            stamp = time.time() + index
            import os

            os.utime(backup, (stamp, stamp))
        (root / "not-a-backup").mkdir()

        stale = self.mod.plan_backup_rotation(root, keep=2)
        self.assertEqual([p.name for p in stale], ["20260101-000000"])
        self.assertEqual(self.mod.plan_backup_rotation(root, keep=5), [])
        self.assertEqual(len(self.mod.plan_backup_rotation(root, keep=0)), 3)

    def test_select_categories_filters_and_validates(self) -> None:
        picked, error = self.mod.select_categories(None, "")
        self.assertIsNone(error)
        self.assertEqual(picked, list(self.mod.DEFAULT_APPLY))

        picked, error = self.mod.select_categories("npm-cache,next-cache", "")
        self.assertIsNone(error)
        self.assertEqual(picked, ["next-cache", "npm-cache"])

        picked, error = self.mod.select_categories(None, "backups")
        self.assertIsNone(error)
        self.assertNotIn("backups", picked)

        picked, error = self.mod.select_categories("nope", "")
        self.assertEqual(picked, [])
        self.assertIn("nope", error or "")

        picked, error = self.mod.select_categories(None, "nope")
        self.assertIn("nope", error or "")

    def test_collect_finds_temp_tests_and_tmp_files(self) -> None:
        # 用打补丁的方式把仓库路径指到临时目录，避免碰真实产物
        tmp_tests = self.root / "backend" / ".tmp_tests"
        tmp_tests.mkdir(parents=True)
        (tmp_tests / "x.db").write_bytes(b"0" * 10)
        (self.root / "backend" / ".tmp_report.py").write_text("print(1)", encoding="utf-8")

        original_backend = self.mod.BACKEND
        self.mod.BACKEND = self.root / "backend"
        try:
            targets = self.mod.collect(include_next=False, include_npm=False, keep_backups=2)
        finally:
            self.mod.BACKEND = original_backend

        categories = {target.category for target in targets}
        self.assertIn("temp-tests", categories)
        self.assertIn("tmp-files", categories)
        temp_target = next(t for t in targets if t.category == "temp-tests")
        self.assertEqual((temp_target.files, temp_target.bytes), (1, 10))

    def test_json_report_shape(self) -> None:
        code = self.mod.main(["--json", "--only", "temp-tests"])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()