"""审计报告真实模型路径验收（第 17 批修复的回归证据）。

背景：完整用户旅程（`scripts/full_journey_e2e.py`）第一次真跑时，``end_audit``
的 closing Run 三次调用后把报告落成了 ``generated_by="fallback"`` —— 模型在 JSON
里带出了多余字段（``type``）并把 ``limitations`` 写成了数组，严格校验直接判失败。
修复（``ReportDraft`` 有界归一 + 指令里写明字段类型）之后，本脚本用**真实模型**
跑一遍「澄清 → brief → 审计开场 → 作答 → 结束出报告」，断言报告来自模型而不是兜底。

成本：一次约 1.2–1.5 万 tokens（clarify ≈2K、开场 ≈3K、作答 ≈3.3K、报告 ≈5K）。
不要放进 CI，也不要反复跑。
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from core import config as core_config  # noqa: E402
from domain import contracts as C  # noqa: E402
from repositories import audit_repo, database, run_repo, session_repo  # noqa: E402
from services import command_service  # noqa: E402
from workflows import runner  # noqa: E402

OWNER = "audit-report-e2e@example.com"
WORKER_ID = "audit-report-e2e-worker"
REPORT_PATH = REPO / "docs" / "audit-report-e2e.md"

PROBLEM = "为中小企业财务团队做自动对账助手，先确认需求是否成立。"
REVISE_FIELDS = {
    "target_users": "50 人以下中小企业的财务负责人与出纳。",
    "usage_scenario": "每月结账前导入流水与发票，自动匹配并列出差异。",
    "core_problem": "手工对账每月超 6 小时，差异定位靠翻凭证。",
    "success_criteria": "对账时间降到 1 小时内，差异 100% 可追溯。",
}
ANSWER = (
    "我们访谈了 12 家中小企业财务团队，平均每周对账 6 小时，"
    "其中 4 家优先做流水与发票比对；付费意愿集中在每月 300 元以内。"
)


class E2EError(AssertionError):
    pass


def check(condition: bool, message: str) -> None:
    if not condition:
        raise E2EError(message)


def live_database_path() -> Path:
    url = os.environ.get("DATABASE_URL") or database.default_database_url()
    for prefix in ("sqlite+pysqlite:///", "sqlite:///"):
        if url.startswith(prefix):
            path = Path(url[len(prefix):])
            return path if path.is_absolute() else (BACKEND / path)
    raise E2EError(f"不支持的数据库 URL：{url}")


def prepare_database() -> Path:
    tmp_root = BACKEND / ".tmp_tests"
    tmp_root.mkdir(exist_ok=True)
    tmpdir = Path(tempfile.mkdtemp(prefix="audit-report-", dir=str(tmp_root)))
    target = tmpdir / "report.db"
    source = live_database_path()
    src = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(target.as_posix())
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    database.configure(f"sqlite:///{target.as_posix()}", force=True)
    database.initialize_database()
    return tmpdir


def drain(label: str) -> list[dict]:
    executed: list[dict] = []
    for _ in range(6):
        with database.session_scope() as db:
            claimed = run_repo.claim_next_run(
                db, worker_id=WORKER_ID, now_epoch=int(time.time())
            )
        if claimed is None:
            break
        started = time.time()
        runner.execute_run(claimed.id, worker_id=WORKER_ID, renew_thread=False)
        with database.session_scope() as db:
            row = run_repo.get_run(db, claimed.id)
            usage = json.loads(row.usage_json or "{}")
        entry = {
            "action": row.action,
            "status": row.status,
            "elapsed": round(time.time() - started, 1),
            "tokens": int(usage.get("total_tokens") or 0),
            "calls": int(usage.get("upstream_calls") or 0),
        }
        executed.append(entry)
        print(
            f"  [{label}] {entry['action']} -> {entry['status']} "
            f"{entry['elapsed']}s tokens={entry['tokens']} calls={entry['calls']}"
        )
    return executed


def command(action: C.Action, payload: dict) -> object:
    with database.session_scope() as db:
        session = session_repo.require_session(db, _session_id, owner_email=OWNER)
        receipt = command_service.submit_command(
            db,
            owner_email=OWNER,
            session_id=session.id,
            command=C.CommandRequest(
                request_id=f"report-e2e-{action.value}-{int(time.time() * 1000)}",
                action=action,
                expected_session_version=session.session_version,
                payload=payload,
            ),
        )
    print(f"  [cmd] {action.value} -> {type(receipt).__name__}")
    return receipt


def audit_snapshot() -> dict:
    with database.session_scope() as db:
        session = session_repo.get_session(db, _session_id, include_deleted=True)
        check(session.current_audit_id is not None, "审计实例未创建")
        row = audit_repo.get_audit(db, session.current_audit_id)
        check(row is not None, "审计行缺失")
        report = json.loads(row.report_json) if row.report_json else {}
        revisions = audit_repo.list_report_revisions(db, row.id)
        return {
            "audit_id": row.id,
            "status": row.status,
            "question_count": row.question_count,
            "report": report,
            "revision_count": len(revisions),
            "revision_generated_by": revisions[-1].generated_by if revisions else None,
            "phase": session.phase,
        }


def write_report(rows: list[dict], snap: dict, elapsed: float) -> None:
    report = snap["report"]
    lines = [
        "# 审计报告真实模型路径验收（第 17 批）",
        "",
        "本文件由 `scripts/audit_report_e2e.py` 生成：在**真实模型**上跑"
        "「澄清 → brief 确认 → 审计开场 → 作答 → 结束出报告」，"
        "验证报告来自模型（`generated_by=model`）而不是确定性兜底。",
        "",
        f"- 运行时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 模型：`{getattr(core_config.settings, 'llm_model', 'unknown')}`",
        f"- 关闭思考：`{getattr(core_config.settings, 'llm_disable_reasoning', True)}`",
        f"- 总耗时：{elapsed}s",
        "",
        "## 1. 分步",
        "",
        "| Run | 状态 | 耗时 | tokens | 调用次数 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['action']} | {row['status']} | {row['elapsed']}s | "
            f"{row['tokens']} | {row['calls']} |"
        )
    lines += [
        "",
        "## 2. 报告",
        "",
        "| 指标 | 值 |",
        "| --- | --- |",
        f"| 审计状态 | `{snap['status']}` |",
        f"| 问题数 | {snap['question_count']} |",
        f"| `generated_by` | **`{report.get('generated_by')}`** |",
        f"| 报告修订数 | {snap['revision_count']}（最后一条 `{snap['revision_generated_by']}`） |",
        f"| supported_points | {len(report.get('supported_points') or [])} 条 |",
        f"| unresolved_gaps | {len(report.get('unresolved_gaps') or [])} 条 |",
        f"| recommended_experiments | {len(report.get('recommended_experiments') or [])} 条 |",
        f"| summary 字数 | {len(report.get('summary') or '')} |",
        f"| 最终阶段 | `{snap['phase']}` |",
        "",
        "## 3. 断言",
        "",
        "```text",
        "PASS 报告由模型生成（generated_by=model）",
        "PASS 报告至少有一条 supported_points 或 unresolved_gaps",
        "PASS 报告摘要非空",
        "PASS 审计 completed 且阶段 audit_completed",
        "PASS 报告修订表里有对应记录",
        "```",
        "",
        "## 4. 复跑",
        "",
        "```bash",
        "cd backend && ../.venv/Scripts/python.exe ../scripts/audit_report_e2e.py",
        "```",
        "",
        "> 成本：一次约 0.8–1.5 万 tokens，不要放进 CI。",
        "",
    ]
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    global _session_id
    tmpdir = prepare_database()
    started = time.time()
    ok = False
    rows: list[dict] = []
    try:
        with database.session_scope() as db:
            session = session_repo.create_session(
                db, owner_email=OWNER, problem_statement=PROBLEM
            )
            command_service.start_initial_run(
                db, session=session, owner_email=OWNER, now_iso=C.utc_now_iso()
            )
            _session_id = session.id
        print(f"session {_session_id}")

        rows += drain("clarify")
        with database.session_scope() as db:
            state = session_repo.get_product_state(db, _session_id)
        command(
            C.Action.revise_brief,
            {
                "expected_product_version": state.version,
                "fields": {
                    key: {"value": value, "status": "confirmed"}
                    for key, value in REVISE_FIELDS.items()
                },
            },
        )
        rows += drain("revise_brief")
        with database.session_scope() as db:
            state = session_repo.get_product_state(db, _session_id)
        command(
            C.Action.confirm_brief,
            {"expected_product_version": state.version, "fields": dict(REVISE_FIELDS)},
        )
        rows += drain("confirm_brief")

        with database.session_scope() as db:
            state = session_repo.get_product_state(db, _session_id)
        command(C.Action.start_audit, {"expected_product_version": state.version})
        rows += drain("start_audit")

        snap = audit_snapshot()
        check(snap["status"] == "active", f"审计未开场：{snap['status']}")
        with database.session_scope() as db:
            row = audit_repo.get_audit(db, snap["audit_id"])
            question = audit_repo.get_question(db, row.id, row.active_question_id)
            audit_id, question_id = row.id, question.id
        command(
            C.Action.answer_audit,
            {"audit_id": audit_id, "question_id": question_id, "answer": ANSWER},
        )
        rows += drain("answer_audit")

        command(C.Action.end_audit, {"audit_id": audit_id})
        rows += drain("end_audit")

        snap = audit_snapshot()
        report = snap["report"]
        check(snap["status"] == "completed", f"审计未完成：{snap['status']}")
        check(snap["phase"] == "audit_completed", f"阶段异常：{snap['phase']}")
        check(
            report.get("generated_by") == "model",
            f"报告仍走兜底：generated_by={report.get('generated_by')}",
        )
        points = len(report.get("supported_points") or [])
        gaps = len(report.get("unresolved_gaps") or [])
        check(points + gaps > 0, "报告内容为空")
        check(
            str(report.get("summary") or "").strip() != "",
            "报告摘要为空（第 18 批要求摘要必填）",
        )
        check(snap["revision_count"] > 0, "报告修订表为空")
        check(
            snap["revision_generated_by"] == "model",
            f"修订表来源异常：{snap['revision_generated_by']}",
        )
        elapsed = round(time.time() - started, 1)
        write_report(rows, snap, elapsed)
        ok = True
        print(
            json.dumps(
                {
                    "ok": True,
                    "elapsed_seconds": elapsed,
                    "generated_by": report.get("generated_by"),
                    "supported_points": points,
                    "unresolved_gaps": gaps,
                    "summary_chars": len(report.get("summary") or ""),
                    "total_tokens": sum(row["tokens"] for row in rows),
                    "report": str(REPORT_PATH),
                },
                ensure_ascii=False,
            )
        )
        return 0
    finally:
        database.dispose_engine()
        if os.environ.get("JOURNEY_KEEP") == "1" or not ok:
            print(f"kept tmpdir: {tmpdir}")
        else:
            shutil.rmtree(tmpdir, ignore_errors=True)


_session_id = ""


if __name__ == "__main__":
    sys.exit(main())
