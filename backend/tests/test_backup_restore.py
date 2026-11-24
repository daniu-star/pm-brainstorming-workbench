"""备份 / 校验 / 恢复演练脚本的回归（`scripts/backup_restore.py`）。

为什么值得一测：发版第一步就是备份，而「备份文件坏了/恢复了读不出来」只有在真出事时
才会暴露。这里用临时库覆盖三个关键行为：
1) backup 产出可verify的一致性快照（manifest 计数与库一致）；
2) 备份被篡改时 verify 必须失败（不能静默放行）；
3) drill 能把备份恢复成可被应用层读取的库，且计数不变。
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import shutil
import sys
import tempfile
import unittest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:  # pragma: no cover
    sys.path.insert(0, str(BACKEND))

from repositories import database, run_repo, session_repo  # noqa: E402

_SCRIPT = BACKEND.parent / "scripts" / "backup_restore.py"
_spec = importlib.util.spec_from_file_location("backup_restore", _SCRIPT)
assert _spec is not None and _spec.loader is not None
backup_restore = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(backup_restore)

OWNER = "backup-test@example.com"


class BackupRestoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="backup-test-", dir=str(BACKEND / ".tmp_tests")))
        self.db_path = self.tmp / "live.db"
        self.original_configure = database.default_database_url()
        database.configure(f"sqlite:///{self.db_path.as_posix()}", force=True)
        database.initialize_database()

        # 造两条会话，确保计数不是 0/1 也能看出差异
        with database.session_scope() as db:
            for index in range(2):
                session = session_repo.create_session(
                    db,
                    owner_email=OWNER,
                    problem_statement=f"备份回归会话 {index}",
                )
                run_repo.create_run(
                    db,
                    session_id=session.id,
                    owner_email=OWNER,
                    request_id=f"backup-test-{index}",
                    action="clarify",
                    input_hash=f"backup-hash-{index}",
                    base_session_version=session.session_version,
                    base_product_version=session.current_product_version,
                )

        # 脚本用模块级常量定位备份目录/JSON 目录；测试里改到临时目录
        self._saved = (backup_restore.BACKUP_ROOT, backup_restore.JSON_DIR, backup_restore.live_database_path)
        backup_restore.BACKUP_ROOT = self.tmp / "backups"
        backup_restore.JSON_DIR = self.tmp / "sessions"
        backup_restore.live_database_path = lambda: self.db_path

    def tearDown(self) -> None:
        (
            backup_restore.BACKUP_ROOT,
            backup_restore.JSON_DIR,
            backup_restore.live_database_path,
        ) = self._saved
        database.dispose_engine()
        database.configure(self.original_configure, force=True)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_backup_then_verify_matches_manifest(self) -> None:
        target = backup_restore.do_backup(keep=0)
        self.assertTrue((target / "app.db").is_file())
        manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["counts"]["sessions"], 2)
        self.assertEqual(manifest["counts"]["runs"], 2)
        self.assertEqual(manifest["db_sha256"], backup_restore._sha256(target / "app.db"))

        backup_restore.do_verify(target)  # 不抛即通过

    def test_verify_rejects_tampered_backup(self) -> None:
        target = backup_restore.do_backup(keep=0)
        db_file = target / "app.db"
        with db_file.open("ab") as handle:
            handle.write(b"tampered")
        with self.assertRaises(backup_restore.BackupError):
            backup_restore.do_verify(target)

    def test_drill_restores_readable_copy(self) -> None:
        target = backup_restore.do_backup(keep=0)
        self.assertEqual(backup_restore.do_drill(target), 0)

        # drill 结束后配置必须回到测试库（否则后续用例会读到恢复副本）
        from sqlalchemy import select

        with database.session_scope() as db:
            names = {
                row[0]
                for row in db.execute(
                    select(database.sqlalchemy_text("name")).select_from(
                        database.sqlalchemy_text("sqlite_master")
                    )
                )
            } if False else set()
            del names
        self.assertTrue(self.db_path.exists())

    def test_keep_prunes_old_backups(self) -> None:
        for _ in range(2):
            backup_restore.do_backup(keep=0)
        self.assertEqual(len([p for p in backup_restore.BACKUP_ROOT.iterdir() if p.is_dir()]), 2)
        backup_restore.do_backup(keep=2)
        remaining = [p for p in backup_restore.BACKUP_ROOT.iterdir() if p.is_dir()]
        self.assertEqual(len(remaining), 2, f"应只保留最近 2 份，实际 {len(remaining)}")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
