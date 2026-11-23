"""AC-34 回滚演练：新构建失败后切回「上一数据库兼容构建」。

判据（PRD AC-34）：切换后的消息/任务不丢；数据库状态可读；无 JSON 双写。

演练全部发生在**生产库的临时副本**上（`sqlite3.backup()` 一致性快照），
不会写 `backend/data/app.db`，也不会碰 `backend/data/sessions/` 里的旧 JSON。

步骤：
1. 从生产库做一致性快照 → 临时库；
2. 记录基线（各表行数、会话/消息 ID 集合、排队中的 Run、产品状态版本）；
3. 模拟「新构建」的迁移：先做一次会失败的 DDL（NOT NULL 无默认值），
   再做一次「部分成功」的变更（加可空列 + 建新表 + 写 marker）后启动自检失败；
4. 新构建被判定失败 → 回滚：删新表、DROP 新列，再跑幂等 `initialize_database()`；
5. 断言消息/任务一条不少、所有会话可读、排队任务仍可被 Worker 领取、
   `PRAGMA integrity_check`/`foreign_key_check` 干净、旧 JSON 目录哈希不变。

用法：
    cd backend && ../.venv/Scripts/python.exe ../scripts/ac34_rollback_drill.py
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import sqlite3
import sys
import tempfile
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402

from repositories import database, run_repo, session_repo  # noqa: E402

TABLES = (
    "sessions",
    "messages",
    "product_states",
    "runs",
    "steps",
    "step_candidates",
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
JSON_DIR = BACKEND / "data" / "sessions"
ROLLOUT_TABLE = "rollout_journal"
ROLLOUT_COLUMN = "rollout_flag"


class DrillError(AssertionError):
    pass


def check(condition: bool, message: str) -> None:
    if not condition:
        raise DrillError(message)


def live_database_path() -> pathlib.Path:
    url = os.environ.get("DATABASE_URL") or database.default_database_url()
    for prefix in ("sqlite+pysqlite:///", "sqlite:///"):
        if url.startswith(prefix):
            raw = pathlib.Path(url[len(prefix):])
            return (raw if raw.is_absolute() else (BACKEND / raw)).resolve()
    raise DrillError(f"本演练只支持 SQLite 生产库，当前：{url}")


def snapshot(source: pathlib.Path, target: pathlib.Path) -> None:
    """用 sqlite3 在线备份 API 做一致性快照（不阻塞生产库写入）。"""

    source_conn = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    try:
        target_conn = sqlite3.connect(target.as_posix())
        try:
            source_conn.backup(target_conn)
        finally:
            target_conn.close()
    finally:
        source_conn.close()


def table_counts(db_url: str) -> dict[str, int]:
    database.configure(db_url, force=True)
    engine = database.get_engine()
    counts: dict[str, int] = {}
    with engine.connect() as conn:
        for table in TABLES:
            try:
                counts[table] = int(conn.execute(text(f"select count(*) from {table}")).scalar_one())
            except OperationalError:
                counts[table] = -1
    return counts


def collect_state(db_url: str) -> dict[str, object]:
    database.configure(db_url, force=True)
    with database.session_scope() as db:
        session_ids = [row[0] for row in db.execute(text("select id from sessions order by id")).all()]
        message_ids = [row[0] for row in db.execute(text("select id from messages order by id")).all()]
        run_ids = [row[0] for row in db.execute(text("select id from runs order by id")).all()]
        queued = [
            row[0]
            for row in db.execute(text("select id from runs where status = 'queued' order by created_at")).all()
        ]
        versions = {
            row[0]: int(row[1])
            for row in db.execute(
                text(
                    "select session_id, max(version) from product_states group by session_id"
                )
            ).all()
        }
        integrity = db.execute(text("pragma integrity_check")).scalar_one()
        fk_problems = db.execute(text("pragma foreign_key_check")).all()
        sqlite_version = db.execute(text("select sqlite_version()")).scalar_one()
    return {
        "counts": table_counts(db_url),
        "sessions": session_ids,
        "messages": message_ids,
        "runs": run_ids,
        "queued": queued,
        "product_versions": versions,
        "integrity": integrity,
        "foreign_key_problems": len(fk_problems),
        "sqlite_version": sqlite_version,
    }


def json_fingerprint() -> str:
    if not JSON_DIR.exists():
        return "absent"
    digest = hashlib.sha256()
    for path in sorted(JSON_DIR.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(JSON_DIR).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def simulate_failed_build(db_url: str) -> dict[str, str]:
    """模拟新构建：第一步 DDL 失败；第二步部分成功后自检失败。"""

    database.configure(db_url, force=True)
    engine = database.get_engine()
    report: dict[str, str] = {}

    with engine.connect() as conn:
        try:
            conn.execute(text(f"alter table sessions add column {ROLLOUT_COLUMN} text not null"))
            conn.commit()
            raise DrillError("新构建的非法 DDL 竟然成功了（NOT NULL 无默认值应被 SQLite 拒绝）")
        except OperationalError as exc:
            report["failed_ddl"] = str(exc.orig).splitlines()[0][:160]

    with engine.connect() as conn:
        conn.execute(text(f"alter table sessions add column {ROLLOUT_COLUMN} text"))
        conn.execute(
            text(
                f"create table if not exists {ROLLOUT_TABLE}"
                " (id integer primary key autoincrement, note text not null, created_at text not null)"
            )
        )
        conn.execute(
            text(f"insert into {ROLLOUT_TABLE} (note, created_at) values (:note, :now)"),
            {"note": "新构建迁移到一半", "now": str(int(time.time()))},
        )
        conn.commit()
        report["partial_change"] = "加列 + 建表成功，随后启动自检失败"
    return report


def rollback_build(db_url: str) -> dict[str, str]:
    """切回上一兼容构建：清掉新构建的半成品，再跑幂等建库。"""

    database.configure(db_url, force=True)
    engine = database.get_engine()
    report: dict[str, str] = {}
    with engine.connect() as conn:
        sqlite_version = conn.execute(text("select sqlite_version()")).scalar_one()
        conn.execute(text(f"drop table if exists {ROLLOUT_TABLE}"))
        report["drop_table"] = ROLLOUT_TABLE
        try:
            conn.execute(text(f"alter table sessions drop column {ROLLOUT_COLUMN}"))
            report["drop_column"] = f"{ROLLOUT_COLUMN}（sqlite {sqlite_version}）"
        except OperationalError as exc:  # SQLite < 3.35
            report["drop_column"] = (
                f"跳过（sqlite {sqlite_version} 不支持 DROP COLUMN）："
                f"{str(exc.orig).splitlines()[0][:100]}"
            )
        conn.commit()
    database.initialize_database()
    report["initialize_database"] = "幂等执行完成"
    return report


def seed_inflight_run(db_url: str) -> dict[str, object]:
    """在副本里造一个「切换时正在排队」的任务，用来验证回滚后任务不丢、仍可被领取。"""

    database.configure(db_url, force=True)
    with database.session_scope() as db:
        row = db.execute(
            text(
                "select id, owner_email, session_version, current_product_version from sessions"
                " where deleted_at is null order by updated_at desc limit 1"
            )
        ).first()
        if row is None:
            return {"run_id": None, "note": "库里没有可用会话"}
        session_id, owner_email, session_version, product_version = row
        run = run_repo.create_run(
            db,
            session_id=session_id,
            owner_email=owner_email,
            request_id=f"ac34-inflight-{int(time.time())}",
            action="clarify",
            input_hash="ac34-inflight-hash",
            base_session_version=int(session_version),
            base_product_version=int(product_version),
            deadline_at=int(time.time()) + 600,
            queue_deadline_at=int(time.time()) + 900,
        )
        return {"run_id": run.id, "session_id": session_id}


def claim_check(db_url: str) -> dict[str, object]:
    """在副本上验证：排队中的任务切回后仍可被 Worker 领取（任务不丢）。"""

    database.configure(db_url, force=True)
    with database.session_scope() as db:
        claimed = run_repo.claim_next_run(db, worker_id="rollback-drill", now_epoch=int(time.time()))
        if claimed is None:
            return {"claimed": None, "note": "当前没有可领取任务（也属正常）"}
        return {
            "claimed": claimed.id,
            "action": claimed.action,
            "status": claimed.status,
        }


def main() -> int:
    live = live_database_path()
    if not live.exists():
        raise SystemExit(f"生产库不存在：{live}")
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="ac34-", dir=str(BACKEND / ".tmp_tests")))
    snapshot_db = workdir / "rollback.db"
    claim_db = workdir / "claim.db"
    report: dict[str, object] = {
        "live_database": str(live),
        "working_copy": str(snapshot_db),
        "json_dir_fingerprint_before": None,
        "json_dir_fingerprint_after": None,
    }
    try:
        json_before = json_fingerprint()
        report["json_dir_fingerprint_before"] = json_before
        snapshot(live, snapshot_db)
        db_url = f"sqlite:///{snapshot_db.as_posix()}"

        before = collect_state(db_url)
        report["baseline_counts"] = before["counts"]
        print(f"[1/5] 快照完成：{live.name} → {snapshot_db.name}；基线 {before['counts']}")

        inflight = seed_inflight_run(db_url)
        report["inflight"] = inflight
        before = collect_state(db_url)
        check(inflight["run_id"] in before["queued"], "造出的排队任务没有进入 queued 集合")
        print(f"[1/5] 已注入一个「切换时排队中」的任务：{inflight}")

        report["failed_build"] = simulate_failed_build(db_url)
        after_bad_build = table_counts(db_url)
        check(
            after_bad_build["sessions"] == before["counts"]["sessions"],
            "新构建失败后会话行数发生变化",
        )
        print(f"[2/5] 新构建模拟：{report['failed_build']}")

        report["rollback"] = rollback_build(db_url)
        print(f"[3/5] 回滚完成：{report['rollback']}")

        after = collect_state(db_url)
        report["after"] = {k: v for k, v in after.items() if k not in {"messages", "sessions", "runs"}}
        check(after["counts"] == before["counts"], f"回滚后行数不一致：{before['counts']} != {after['counts']}")
        check(after["sessions"] == before["sessions"], "回滚后会话 ID 集合丢失")
        check(after["messages"] == before["messages"], "回滚后消息丢失")
        check(after["runs"] == before["runs"], "回滚后任务丢失")
        check(
            after["product_versions"] == before["product_versions"],
            "回滚后产品状态版本不一致",
        )
        check(after["integrity"] == "ok", f"integrity_check 失败：{after['integrity']}")
        check(after["foreign_key_problems"] == 0, "回滚后存在外键悬挂行")

        # 每个会话都要能被「上一构建」的 ORM 读出来（不是只有计数对）
        database.configure(db_url, force=True)
        readable = 0
        with database.session_scope() as db:
            for session_id in after["sessions"]:
                row = session_repo.get_session(db, session_id, include_deleted=True)
                check(row is not None, f"回滚后会话不可读：{session_id}")
                run_repo.list_session_runs(db, session_id, limit=50)
                readable += 1
        report["readable_sessions"] = readable
        check(readable == len(before["sessions"]), "回滚后可读会话数不对")
        print(f"[4/5] 数据校验通过：{readable} 个会话可读、消息 {len(after['messages'])} 条、任务 {len(after['runs'])} 个")

        # 任务可继续：在第二份副本上验证排队任务仍能被领取（不污染校验副本）
        shutil.copyfile(snapshot_db, claim_db)
        report["claim_check"] = claim_check(f"sqlite:///{claim_db.as_posix()}")
        claimed = report["claim_check"].get("claimed")
        check(
            inflight["run_id"] is not None and claimed == inflight["run_id"],
            f"回滚后排队的任务未被领取：{report['claim_check']}（期望 {inflight['run_id']}）",
        )

        json_after = json_fingerprint()
        report["json_dir_fingerprint_after"] = json_after
        check(json_before == json_after, f"旧 JSON 目录被改动（存在 JSON 双写风险）：{json_before} != {json_after}")
        print(f"[5/5] 任务可领取：{report['claim_check']}；JSON 目录未变：{json_after}")

        report["ok"] = True
        print("ROLLBACK_DRILL_OK")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
