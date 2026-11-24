#!/usr/bin/env python
"""数据库增长报告与 VACUUM 维护（stdlib only）。

用途：回答「库为什么这么大、还能不能变小」。

* ``report``：按表统计占用字节（优先 ``dbstat``，不可用时用页数估算）+ 行数 +
  文本列总长度，输出最占空间的前 N 项。只读，不修改库。
* ``vacuum``：执行 ``VACUUM`` 回收已删除行占用的空闲页。SQLite 删除行后**不会**自动
  缩小文件，需要显式 VACUUM；VACUUM 期间需要约等于库体积的临时空间，并在结束时
  重写整库，因此不要在任务高峰执行。

用法::

    python scripts/db_maintenance.py report
    python scripts/db_maintenance.py report --json
    python scripts/db_maintenance.py vacuum

数据库路径与线上一致：``DATABASE_URL`` 优先，否则 ``backend/data/app.db``。
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"

#: 报告里逐个统计行数与文本列长度的业务表（按重要性排序，不是全部表）。
REPORT_TABLES = (
    "sessions",
    "messages",
    "product_states",
    "runs",
    "steps",
    "step_candidates",
    "events",
    "audits",
    "audit_questions",
    "audit_assessment_revisions",
    "audit_report_revisions",
    "evidence",
    "control_intents",
    "idempotency_records",
    "auth_codes",
    "loop_artifacts",
    "loop_stage_results",
    "legacy_artifacts",
)

#: 大字段：报告时统计它们的总字节数，便于判断「是正文还是中间载荷占空间」。
TEXT_COLUMNS = {
    "messages": ("content",),
    "product_states": ("payload_json",),
    "steps": ("input_snapshot_json", "output_json", "error_message"),
    "step_candidates": ("payload_json",),
    "events": ("payload_json",),
    "audit_questions": ("question_text", "rationale", "evidence_refs_json"),
    "audit_assessment_revisions": ("assessment_json",),
    "audit_report_revisions": ("report_json",),
    "evidence": ("title", "excerpt"),
    "loop_artifacts": ("payload_json",),
    "loop_stage_results": ("summary", "payload_json"),
    "legacy_artifacts": ("payload_json",),
}

#: sqlite_master 里的内部表不进报告（索引与自动索引另算）。
INTERNAL_PREFIXES = ("sqlite_",)


def live_database_path() -> Path:
    """解析当前数据库文件路径（支持 ``sqlite:///`` 与 ``sqlite+pysqlite:///``）。"""

    value = os.environ.get("DATABASE_URL") or ""
    if not value:
        sys.path.insert(0, str(BACKEND))
        from repositories import database  # noqa: PLC0415 - 延迟导入，避免影响 --help

        value = database.default_database_url()
    for prefix in ("sqlite+pysqlite:///", "sqlite:///"):
        if value.startswith(prefix):
            value = value[len(prefix) :]
            break
    path = Path(value)
    if not path.is_absolute():
        path = BACKEND / path
    return path


def _table_bytes(con: sqlite3.Connection) -> dict[str, int]:
    """每张表（含索引）占用的字节数；没有 dbstat 时退回页数估算。"""

    try:
        rows = con.execute(
            "select name, sum(pgsize) as bytes from dbstat group by name"
        ).fetchall()
    except sqlite3.Error:
        page_size = int(con.execute("pragma page_size").fetchone()[0])
        rows = []
        for name, in con.execute(
            "select name from sqlite_master where type in ('table','index')"
        ).fetchall():
            pages = int(con.execute(f"pragma page_count('{name}')").fetchone()[0])
            rows.append((name, pages * page_size))
    return {str(name): int(size or 0) for name, size in rows}


def build_report(db_path: Path, *, top: int = 12) -> dict:
    """返回报告字典：库体积、按表字节、行数、文本列字节、最大表排行。"""

    if not db_path.exists():
        raise SystemExit(f"数据库不存在：{db_path}")
    con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        page_size = int(con.execute("pragma page_size").fetchone()[0])
        page_count = int(con.execute("pragma page_count").fetchone()[0])
        freelist = int(con.execute("pragma freelist_count").fetchone()[0])
        bytes_by_name = _table_bytes(con)

        tables: dict[str, dict[str, int]] = {}
        for table in REPORT_TABLES:
            try:
                count = int(con.execute(f"select count(*) from {table}").fetchone()[0])
            except sqlite3.Error:
                continue
            text_bytes = 0
            for column in TEXT_COLUMNS.get(table, ()):
                try:
                    text_bytes += int(
                        con.execute(
                            f"select coalesce(sum(length({column})), 0) from {table}"
                        ).fetchone()[0]
                    )
                except sqlite3.Error:
                    continue
            tables[table] = {
                "rows": count,
                "bytes": bytes_by_name.get(table, 0),
                "text_bytes": text_bytes,
            }

        total_bytes = sum(bytes_by_name.values())
        biggest = sorted(
            ((name, size) for name, size in bytes_by_name.items()
             if not name.startswith(INTERNAL_PREFIXES)),
            key=lambda item: item[1],
            reverse=True,
        )[:top]
        return {
            "path": str(db_path),
            "file_bytes": db_path.stat().st_size,
            "page_size": page_size,
            "page_count": page_count,
            "freelist_pages": freelist,
            "freelist_bytes": freelist * page_size,
            "dbstat_total_bytes": total_bytes,
            "tables": tables,
            "biggest": [{"name": name, "bytes": size} for name, size in biggest],
        }
    finally:
        con.close()


def format_report(report: dict) -> str:
    lines = [
        f"数据库：{report['path']}",
        f"文件大小：{report['file_bytes'] / 1024:.1f}KB"
        f"（page_size={report['page_size']}，页数={report['page_count']}，"
        f"空闲页={report['freelist_pages']} / {report['freelist_bytes'] / 1024:.1f}KB）",
    ]
    if report["freelist_bytes"] > 0:
        lines.append("提示：存在空闲页，可用 `python scripts/db_maintenance.py vacuum` 回收。")
    lines.append("最占空间：")
    for item in report["biggest"]:
        lines.append(f"  {item['name']:28} {item['bytes'] / 1024:8.1f}KB")
    lines.append("业务表：")
    for table, stats in report["tables"].items():
        lines.append(
            f"  {table:28} 行 {stats['rows']:>7}  占用 {stats['bytes'] / 1024:8.1f}KB"
            f"  文本 {stats['text_bytes'] / 1024:8.1f}KB"
        )
    return "\n".join(lines)


def run_vacuum(db_path: Path) -> dict:
    """执行 VACUUM 并回报前后体积（需要约等于库体积的临时空间）。"""

    if not db_path.exists():
        raise SystemExit(f"数据库不存在：{db_path}")
    before = db_path.stat().st_size
    con = sqlite3.connect(db_path)
    try:
        con.execute("vacuum")
        con.commit()
        # VACUUM 结束时文件可能尚未截断到新页数，先关连接再取大小更准。
        after_pages = int(con.execute("pragma page_count").fetchone()[0])
        after_page_size = int(con.execute("pragma page_size").fetchone()[0])
    finally:
        con.close()
    after = min(db_path.stat().st_size, after_pages * after_page_size)
    return {"path": str(db_path), "before_bytes": before, "after_bytes": after,
            "saved_bytes": max(0, before - after)}


def main(argv: list[str]) -> int:
    command = argv[0] if argv else "report"
    json_mode = "--json" in argv
    args = [item for item in argv if not item.startswith("--")]
    if json_mode and args:
        args = [item for item in args if item not in ("report", "vacuum")] or args
    try:
        db_path = live_database_path()
    except Exception as exc:  # noqa: BLE001 - 解析失败时给出可操作提示
        print(f"无法解析数据库路径：{exc}")
        return 2

    if command == "report":
        report = build_report(db_path)
        print(json.dumps(report, ensure_ascii=False, indent=2) if json_mode else format_report(report))
        return 0
    if command == "vacuum":
        result = run_vacuum(db_path)
        if json_mode:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(
                f"VACUUM 完成：{result['before_bytes'] / 1024:.1f}KB → "
                f"{result['after_bytes'] / 1024:.1f}KB（回收 {result['saved_bytes'] / 1024:.1f}KB）"
            )
        return 0
    print(f"未知子命令：{command}（可用：report / vacuum）")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
