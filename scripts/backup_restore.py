"""数据库 + 旧 JSON 会话目录的备份 / 校验 / 恢复演练。

为什么需要它：
- 发版顺序里第一步是「备份 `backend/data/app.db` 与 `backend/data/sessions/`」（见
  `docs/v4-release-checklist.md`），但手工 `copy` 正在被写入的 SQLite 文件可能拿到
  不一致快照；
- 备份是否可用，必须**定期演练**，否则真出事才发现缺表/损坏。

做法：用 SQLite 官方的 online backup API（`sqlite3.Connection.backup`）在写入进行中
也能拿到一致性快照；同时复制 `data/sessions/`（旧 JSON 源文件，迁移脚本只读它），
并把计数与 sha256 写进 `manifest.json`。恢复演练在临时目录里跑，不碰生产库。

用法：
    python scripts/backup_restore.py backup              # 备份到 backend/data/backups/<时间戳>/
    python scripts/backup_restore.py backup --keep 10    # 备份并只保留最近 10 份
    python scripts/backup_restore.py verify <备份目录>    # 校验 manifest/哈希/完整性
    python scripts/backup_restore.py drill  <备份目录>    # 恢复到临时目录并比对计数
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
DATA_DIR = BACKEND / "data"
JSON_DIR = DATA_DIR / "sessions"
BACKUP_ROOT = DATA_DIR / "backups"

#: 与迁移演练/回滚演练保持同一套表，方便横向比对计数
TABLES = (
    "sessions",
    "messages",
    "product_states",
    "runs",
    "steps",
    "events",
    "audits",
    "audit_questions",
    "evidence",
    "control_intents",
    "idempotency_records",
    "legacy_artifacts",
    "migration_records",
    "session_id_aliases",
)


class BackupError(RuntimeError):
    pass


def live_database_path() -> Path:
    """按 repositories.database 的口径定位线上库（支持 DATABASE_URL 覆盖）。"""

    url = __import__("os").environ.get("DATABASE_URL") or ""
    if not url:
        sys.path.insert(0, str(BACKEND))
        from repositories.database import default_database_url

        url = default_database_url()
    for prefix in ("sqlite+pysqlite:///", "sqlite:///"):
        if url.startswith(prefix):
            raw = url[len(prefix) :]
            path = Path(raw)
            return path if path.is_absolute() else (BACKEND / raw).resolve()
    raise BackupError(f"不支持的数据库 URL：{url}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 256), b""):
            digest.update(chunk)
    return digest.hexdigest()


def table_counts(db_path: Path) -> dict[str, int]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        counts: dict[str, int] = {}
        for table in TABLES:
            try:
                counts[table] = int(conn.execute(f"select count(*) from {table}").fetchone()[0])
            except sqlite3.OperationalError:
                counts[table] = -1  # 老库可能还没有这张表
        return counts
    finally:
        conn.close()


def json_fingerprint() -> dict:
    if not JSON_DIR.exists():
        return {"dir": "absent", "files": 0, "sha256": ""}
    digest = hashlib.sha256()
    files = sorted(p for p in JSON_DIR.rglob("*") if p.is_file())
    for path in files:
        digest.update(path.relative_to(JSON_DIR).as_posix().encode("utf-8"))
        digest.update(_sha256(path).encode("ascii"))
    return {"dir": "present", "files": len(files), "sha256": digest.hexdigest()}


def do_backup(keep: int) -> Path:
    source = live_database_path()
    if not source.exists():
        raise BackupError(f"数据库不存在：{source}")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target_dir = BACKUP_ROOT / stamp
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    # 同一秒内连续备份（脚本化/演练）不能互相覆盖：加序号后缀
    suffix = 1
    while target_dir.exists():
        suffix += 1
        target_dir = BACKUP_ROOT / f"{stamp}-{suffix}"
    target_dir.mkdir(parents=True, exist_ok=False)
    target_db = target_dir / "app.db"

    # online backup：允许库正在被 Worker 写入
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    dst = sqlite3.connect(target_db)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()

    if JSON_DIR.exists():
        shutil.copytree(JSON_DIR, target_dir / "sessions")

    manifest = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": str(source),
        "db_sha256": _sha256(target_db),
        "db_bytes": target_db.stat().st_size,
        "counts": table_counts(target_db),
        "json": json_fingerprint(),
    }
    (target_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"BACKUP_OK {target_dir} bytes={manifest['db_bytes']} json_files={manifest['json']['files']}")

    if keep > 0:
        backups = sorted(p for p in BACKUP_ROOT.iterdir() if p.is_dir())
        for stale in backups[:-keep]:
            shutil.rmtree(stale, ignore_errors=True)
            print(f"  pruned {stale.name}")
    return target_dir


def load_manifest(backup_dir: Path) -> dict:
    path = backup_dir / "manifest.json"
    if not path.exists():
        raise BackupError(f"缺少 manifest.json：{backup_dir}")
    return json.loads(path.read_text(encoding="utf-8"))


def do_verify(backup_dir: Path) -> dict:
    manifest = load_manifest(backup_dir)
    db_path = backup_dir / "app.db"
    if not db_path.exists():
        raise BackupError(f"缺少 app.db：{backup_dir}")
    actual_sha = _sha256(db_path)
    if manifest.get("db_sha256") and actual_sha != manifest["db_sha256"]:
        raise BackupError("app.db 的 sha256 与 manifest 不一致（备份被改动或损坏）")

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        integrity = conn.execute("pragma integrity_check").fetchone()[0]
        fk = conn.execute("pragma foreign_key_check").fetchall()
    finally:
        conn.close()
    if integrity != "ok":
        raise BackupError(f"integrity_check 失败：{integrity}")
    if fk:
        raise BackupError(f"存在悬挂外键：{fk[:3]}")

    counts = table_counts(db_path)
    expected = manifest.get("counts") or {}
    drift = {k: (expected.get(k), v) for k, v in counts.items() if expected and expected.get(k) != v}
    if drift:
        raise BackupError(f"计数与 manifest 不一致：{drift}")

    print(
        "VERIFY_OK "
        f"sessions={counts.get('sessions')} messages={counts.get('messages')} "
        f"runs={counts.get('runs')} integrity=ok fk=0"
    )
    return {"counts": counts, "manifest": manifest}


def do_drill(backup_dir: Path) -> int:
    """把备份恢复到一个临时库并跑一遍应用层读路径（不碰生产库）。"""

    verified = do_verify(backup_dir)
    sys.path.insert(0, str(BACKEND))
    from repositories import database, run_repo, session_repo

    tmp_root = BACKEND / ".tmp_tests"
    tmp_root.mkdir(exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="restore-", dir=str(tmp_root)))
    restored = workdir / "restored.db"
    shutil.copyfile(backup_dir / "app.db", restored)
    previous = database.default_database_url()
    try:
        database.configure(f"sqlite:///{restored.as_posix()}", force=True)
        database.initialize_database()
        conn = sqlite3.connect(f"file:{restored}?mode=ro", uri=True)
        try:
            ids = [row[0] for row in conn.execute("select id from sessions limit 50")]
        finally:
            conn.close()
        with database.session_scope() as db:
            readable = 0
            for session_id in ids:
                session_repo.get_session(db, session_id, include_deleted=True)
                run_repo.list_session_runs(db, session_id, limit=5)
                readable += 1
        counts = table_counts(restored)
        print(
            "RESTORE_DRILL_OK "
            f"sessions={counts.get('sessions')} messages={counts.get('messages')} "
            f"readable_sessions={readable} integrity=ok"
        )
        return 0
    finally:
        database.configure(previous, force=True)
        shutil.rmtree(workdir, ignore_errors=True)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="备份 / 校验 / 恢复演练（SQLite + 旧 JSON 目录）")
    sub = parser.add_subparsers(dest="command", required=True)
    backup_cmd = sub.add_parser("backup", help="生成一致性备份")
    backup_cmd.add_argument("--keep", type=int, default=0, help="只保留最近 N 份（0 = 不清理）")
    for name, help_text in (("verify", "校验备份完整性"), ("drill", "恢复到临时目录并跑读路径")):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("directory", type=Path)
    args = parser.parse_args(argv)

    try:
        if args.command == "backup":
            do_backup(args.keep)
        elif args.command == "verify":
            do_verify(args.directory)
        else:
            return do_drill(args.directory)
    except (BackupError, sqlite3.Error) as exc:
        print(f"FAILED {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
