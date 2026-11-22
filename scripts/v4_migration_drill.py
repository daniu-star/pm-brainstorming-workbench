#!/usr/bin/env python
"""V4 迁移发布演练（WP-10）：dry-run 零写入 → apply → 幂等重放 → 回退重迁 → 源文件哈希不变。

用法（在 backend/ 目录下执行，使用本机 venv 解释器）::

    cd backend
    ../.venv/Scripts/python.exe ../scripts/v4_migration_drill.py
    ../.venv/Scripts/python.exe ../scripts/v4_migration_drill.py --source data/sessions/_archived

设计原则：
- 只在临时目录里复制源 JSON 并建临时库，**绝不触碰** `backend/data/app.db` 与真实会话目录；
- 回退演练 = 删除新库后从同一份源重新导入，校验各表计数与首次导入完全一致；
- 每个阶段都对比源文件 sha256，证明迁移器只读源、不修改/删除/覆盖源文件。

退出码：0 表示全部阶段通过，1 表示任一断言失败。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

TABLES = (
    "sessions",
    "messages",
    "product_states",
    "audits",
    "legacy_artifacts",
    "migration_records",
    "session_id_aliases",
)
REQUIRED_PLAN_KEYS = ("sessions", "messages", "product_states")


def _sha256_tree(root: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.glob("*.json"))}


def _counts(db: Path) -> dict[str, int | str]:
    if not db.exists():
        return {"db": "absent"}
    con = sqlite3.connect(db)
    try:
        names = {row[0] for row in con.execute("select name from sqlite_master where type='table'")}
        return {
            table: con.execute(f"select count(*) from {table}").fetchone()[0]
            for table in TABLES
            if table in names
        }
    finally:
        con.close()


def _run_cli(python_bin: str, args: list[str], env: dict[str, str]) -> dict:
    proc = subprocess.run(
        [python_bin, "-m", "migrations.import_json_sessions", *args],
        cwd=".",
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        print(f"[drill] 迁移命令失败：{args}\nstdout={proc.stdout[-2000:]}\nstderr={proc.stderr[-2000:]}")
        raise SystemExit(1)
    payload = proc.stdout.strip()
    try:
        return json.loads(payload) if payload else {}
    except ValueError:
        return {"_raw": payload}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):  # Windows 控制台默认 cp936，中文输出会乱码
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="V4 迁移发布演练")
    parser.add_argument(
        "--source",
        default="data/sessions/_archived",
        help="旧 JSON 会话目录（只会被复制，不会被修改）",
    )
    parser.add_argument(
        "--python",
        default=str((Path("..") / ".venv" / "Scripts" / "python.exe").resolve()),
        help="运行迁移器的解释器",
    )
    args = parser.parse_args(argv)

    src = Path(args.source).resolve()
    if not src.is_dir():
        print(f"[drill] 源目录不存在：{src}")
        return 1

    work = Path(tempfile.mkdtemp(prefix="drill_", dir=".")).resolve()
    copy = work / "src"
    shutil.copytree(src, copy)
    db = work / "drill.db"
    manifest = work / "manifest"
    env = dict(os.environ, PYTHONPATH=".", DATABASE_URL=f"sqlite+pysqlite:///{db.as_posix()}")

    ok = True
    try:
        before = _sha256_tree(copy)
        print(f"[drill] 源：{src}（{len(before)} 个 JSON，只读复制到 {copy}）")

        dry = _run_cli(args.python, ["--source", str(copy), "--dry-run", "--manifest-dir", str(manifest)], env)
        plan = dry.get("planned") or {}
        print(f"[drill] 1/5 dry-run：scanned={dry.get('scanned')} valid={dry.get('valid')} planned={plan}")
        if not all(plan.get(k) for k in REQUIRED_PLAN_KEYS):
            print("[drill] dry-run 计划缺少必需表计数")
            ok = False
        if _counts(db) != {"db": "absent"}:
            print("[drill] dry-run 竟然创建了数据库文件")
            ok = False

        applied = _run_cli(args.python, ["--source", str(copy), "--apply", "--manifest-dir", str(manifest)], env)
        first = _counts(db)
        print(f"[drill] 2/5 apply：{applied.get('status', 'ok')} counts={first}")
        if first.get("sessions") != dry.get("valid"):
            print("[drill] 导入会话数与 dry-run 的有效数不一致")
            ok = False

        again = _run_cli(args.python, ["--source", str(copy), "--apply", "--manifest-dir", str(manifest)], env)
        print(f"[drill] 3/5 幂等重放：skipped={again.get('skipped')} counts={_counts(db)}")
        if _counts(db) != first:
            print("[drill] 重复导入改变了表计数（不幂等）")
            ok = False

        db.unlink()  # 模拟回退：丢弃新库后从同一份源重新导入
        _run_cli(args.python, ["--source", str(copy), "--apply", "--manifest-dir", str(manifest)], env)
        restored = _counts(db)
        print(f"[drill] 4/5 回退重迁：counts={restored} identical={restored == first}")
        if restored != first:
            print("[drill] 回退重迁结果与首次导入不一致")
            ok = False

        after = _sha256_tree(copy)
        print(f"[drill] 5/5 源文件哈希不变：{after == before}（{len(after)} 个文件）")
        if after != before:
            print("[drill] 迁移器改动了源文件")
            ok = False
    finally:
        shutil.rmtree(work, ignore_errors=True)

    print("DRILL_OK" if ok else "DRILL_FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())