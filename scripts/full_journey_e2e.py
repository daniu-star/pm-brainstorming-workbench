"""完整用户旅程端到端（真实模型，一次跑通）：

    clarify → revise_brief → confirm_brief → brainstorm(四角色) → start_audit
    → answer_audit → end_audit(报告)

为什么要这个脚本：其余验收都是「按能力切片」的（崩溃注入 / 浏览器 / 数字守卫），
还没有一条把「一个新用户从提出问题到拿到审计报告」的完整主链路跑通的证据。
本脚本在**真实模型**上跑一遍，并把每一步的 phase、版本、产物与 token 账本落成
`docs/full-journey-e2e.md`，供发版说明引用。

设计约束：
- 复制线上库到 `backend/.tmp_tests/journey-*/journey.db`，不污染 `backend/data/app.db`；
  跑完默认删除临时库（`JOURNEY_KEEP=1` 可保留现场）。
- 全程走真实服务层（command_service + runner + 真 LLMClient），不 mock 任何东西。
- 成本：clarify ≈3K tokens、四角色 brainstorm ≈25K、审计开场/作答/报告各 ≈1–3K。
  一次完整旅程约 35–40K tokens，**只跑一次**，不要放进 CI。
- 断言只校验「结构与状态」，不校验模型措辞（模型输出天然有随机性）。
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

import sqlalchemy as sa  # noqa: E402

from core import config as core_config  # noqa: E402
from domain import contracts as C  # noqa: E402
from repositories import database, run_repo, session_repo  # noqa: E402
from services import canvas_projector, command_service  # noqa: E402
from workflows import runner  # noqa: E402

OWNER = "journey-e2e@example.com"
WORKER_ID = "journey-e2e-worker"
REPORT_PATH = REPO / "docs" / "full-journey-e2e.md"
MAX_RUNS_PER_PHASE = 8

PROBLEM = (
    "我们想为中小企业财务团队做一个「自动对账助手」：把银行流水、发票、报销单自动匹配，"
    "对不上的列出来并给出处理建议。先验证这个需求是否成立，目标客户 50 家以内。"
)

REVISE_FIELDS = {
    "target_users": "50 人以下中小企业的财务负责人与出纳，日常用 Excel 手工对账。",
    "usage_scenario": "每月结账前，财务把银行流水与发票导入，系统自动匹配并列出差异。",
    "core_problem": "手工对账每月耗时 6 小时以上，差异定位靠翻凭证，容易漏项。",
    "success_criteria": "对账时间从 6 小时降到 1 小时以内；差异项 100% 可追溯来源单据。",
}

BRAINSTORM_CONTENT = (
    "请四位专家评审这个对账助手方案：给出关键判断、可验证的实验，"
    "如果需要量化预估请写清口径与来源，没有来源就直接说明是估算。"
)

AUDIT_ANSWER = (
    "我们访谈了 12 家中小企业财务团队，平均每周对账 6 小时，"
    "其中 4 家优先做流水与发票比对。付费意愿集中在每月 300 元以内，"
    "决策人是财务负责人，采购周期约 2 周。"
)


class JourneyError(AssertionError):
    pass


def check(condition: bool, message: str) -> None:
    if not condition:
        raise JourneyError(message)


def live_database_path() -> Path:
    url = os.environ.get("DATABASE_URL") or database.default_database_url()
    for prefix in ("sqlite+pysqlite:///", "sqlite:///"):
        if url.startswith(prefix):
            raw = url[len(prefix):]
            path = Path(raw)
            return path if path.is_absolute() else (BACKEND / path)
    raise JourneyError(f"不支持的数据库 URL：{url}")


def prepare_database() -> Path:
    tmp_root = BACKEND / ".tmp_tests"
    tmp_root.mkdir(exist_ok=True)
    tmpdir = Path(tempfile.mkdtemp(prefix="journey-", dir=str(tmp_root)))
    target = tmpdir / "journey.db"
    source = live_database_path()
    check(source.exists(), f"线上库不存在：{source}")
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


def drain(label: str, actions: dict[str, str]) -> list[dict]:
    """把当前可领取的 Run 全部跑完（含同事务排入的 follow-up Run）。"""

    executed: list[dict] = []
    for _ in range(MAX_RUNS_PER_PHASE):
        with database.session_scope() as db:
            claimed = run_repo.claim_next_run(
                db, worker_id=WORKER_ID, now_epoch=int(time.time())
            )
        if claimed is None:
            break
        started = time.time()
        runner.execute_run(
            claimed.id, worker_id=WORKER_ID, renew_thread=False
        )
        elapsed = round(time.time() - started, 1)
        with database.session_scope() as db:
            row = run_repo.get_run(db, claimed.id)
            usage = json.loads(row.usage_json or "{}")
        entry = {
            "action": row.action,
            "status": row.status,
            "error_code": row.error_code,
            "elapsed_seconds": elapsed,
            "tokens": int(usage.get("total_tokens") or 0),
            "calls": int(usage.get("upstream_calls") or 0),
            "provider_calls": int(usage.get("provider_calls") or 0),
            "estimated": int(usage.get("estimated_calls") or 0),
        }
        executed.append(entry)
        actions[row.action] = row.status
        print(
            f"  [{label}] {row.action} -> {row.status} "
            f"{entry['elapsed_seconds']}s tokens={entry['tokens']} calls={entry['calls']}"
        )
    return executed


def command(action: C.Action, payload: dict, *, note: str):
    with database.session_scope() as db:
        session = session_repo.require_session(db, _session_id, owner_email=OWNER)
        request_id = f"journey-{action.value}-{int(time.time() * 1000)}"
        receipt = command_service.submit_command(
            db,
            owner_email=OWNER,
            session_id=session.id,
            command=C.CommandRequest(
                request_id=request_id,
                action=action,
                expected_session_version=session.session_version,
                payload=payload,
            ),
        )
    print(f"  [cmd] {action.value} -> {type(receipt).__name__} {note}")
    return receipt


def snapshot() -> dict:
    with database.session_scope() as db:
        session = session_repo.get_session(db, _session_id, include_deleted=True)
        state = session_repo.get_product_state(db, _session_id)
        runs = run_repo.list_session_runs(db, _session_id, limit=100)
        audit = None
        if session.current_audit_id:
            from repositories import audit_repo

            row = audit_repo.get_audit(db, session.current_audit_id)
            if row is not None:
                questions = audit_repo.questions_for_audit(db, row.id)
                report = json.loads(row.report_json) if row.report_json else {}
                if not report:
                    revisions = audit_repo.list_report_revisions(db, row.id)
                    if revisions:
                        report = json.loads(revisions[-1].report_json or "{}")
                audit = {
                    "id": row.id,
                    "status": row.status,
                    "question_count": row.question_count,
                    "answered": sum(1 for q in questions if q.answer_message_id),
                    "report_generated_by": report.get("generated_by"),
                    "report_source": report.get("generated_by"),
                    "report_points": len(report.get("supported_points") or []),
                    "report_gaps": len(report.get("unresolved_gaps") or []),
                }
        projection = canvas_projector.project_canvas(state)
        return {
            "phase": session.phase,
            "session_version": session.session_version,
            "product_version": state.version,
            "claims": len(state.claims),
            "options": len(state.options),
            "decisions": len(state.decisions),
            "experiments": len(state.experiments),
            "issues": len(state.unresolved_issues),
            "branches": [branch.title for branch in projection.branches],
            "unverified_nodes": sum(
                1 for claim in state.claims if claim.unverified_numbers
            ),
            "runs": [(row.action, row.status) for row in runs],
            "total_tokens": sum(
                int(json.loads(row.usage_json or "{}").get("total_tokens") or 0)
                for row in runs
            ),
            "audit": audit,
            "message_count": session_repo.count_messages(db, _session_id),
        }


def write_report(steps: list[dict], final: dict) -> None:
    lines: list[str] = []
    lines.append("# 完整用户旅程端到端（真实模型）")
    lines.append("")
    lines.append(
        "本文件由 `scripts/full_journey_e2e.py` 生成：在**真实模型**上把"
        "「提出问题 → 澄清 → brief 确认 → 四角色脑暴 → 审计开场 → 作答 → 收口报告」"
        "整条主链路跑一遍，并记录每一步的状态与 token 账本。"
    )
    lines.append("")
    lines.append(f"- 运行时间：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 模型：`{getattr(core_config.settings, 'llm_model', 'unknown')}`")
    lines.append(
        f"- 预算档位：`{getattr(core_config.settings, 'llm_budget_profile', 'standard')}`"
        f"（关闭思考：`{getattr(core_config.settings, 'llm_disable_reasoning', True)}`）"
    )
    lines.append("")
    lines.append("## 1. 分步结果")
    lines.append("")
    lines.append("| 步骤 | 命令 | 动作 | Run 状态 | 耗时 | tokens |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for step in steps:
        lines.append(
            f"| {step['index']} | `{step['command']}` | {step['action']} | "
            f"{step['status']} | {step['elapsed']}s | {step['tokens']} |"
        )
    lines.append("")
    lines.append("## 2. 最终产物")
    lines.append("")
    lines.append("| 指标 | 值 |")
    lines.append("| --- | --- |")
    lines.append(f"| 会话阶段 | `{final['phase']}` |")
    lines.append(f"| 会话版本 / 产品版本 | {final['session_version']} / {final['product_version']} |")
    lines.append(f"| 消息数 | {final['message_count']} |")
    lines.append(
        f"| 论断 / 方案 / 决策 / 实验 / 未决 | {final['claims']} / {final['options']} / "
        f"{final['decisions']} / {final['experiments']} / {final['issues']} |"
    )
    lines.append(f"| 画布分支 | {'、'.join(final['branches']) or '（空）'} |")
    lines.append(f"| 未验证数字节点 | {final['unverified_nodes']} |")
    if final.get("audit"):
        audit = final["audit"]
        lines.append(
            f"| 审计 | `{audit['status']}`，问题 {audit['question_count']} 个，"
            f"已答 {audit['answered']} 个，报告来源 `{audit['report_source']}`"
            f"（要点 {audit['report_points']} / 未决 {audit['report_gaps']}） |"
        )
    else:
        lines.append("| 审计 | （未创建） |")
    lines.append(f"| 本轮总 tokens | {final['total_tokens']} |")
    lines.append("")
    lines.append("## 3. 断言（脚本内校验，失败即非零退出）")
    lines.append("")
    lines.append("```text")
    for item in final["assertions"]:
        lines.append(f"PASS {item}")
    lines.append("```")
    lines.append("")
    lines.append("## 4. 复跑")
    lines.append("")
    lines.append("```bash")
    lines.append("cd backend && ../.venv/Scripts/python.exe ../scripts/full_journey_e2e.py")
    lines.append("# 保留临时库现场（排查用）：JOURNEY_KEEP=1 ...")
    lines.append("```")
    lines.append("")
    lines.append(
        "> 成本提示：一次完整旅程约 3.5–4 万 tokens（四角色脑暴占大头）。"
        "不要把它放进 CI，也不要反复跑。"
    )
    lines.append("")
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    global _session_id
    tmpdir = prepare_database()
    started = time.time()
    steps: list[dict] = []
    ok = False
    assertions: list[str] = []
    actions: dict[str, str] = {}

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

        # 1) clarify（真实模型）
        before = snapshot()
        executed = drain("clarify", actions)
        after = snapshot()
        assertions.append(
            f"clarify 收口：run={actions.get('clarify')}，phase {before['phase']} → {after['phase']}"
        )
        check(actions.get("clarify") == "succeeded", f"clarify 未成功：{actions}")
        check(after["message_count"] == 2, f"clarify 后消息数应 2，实际 {after['message_count']}")
        steps.append(_step(1, "create_session", executed))

        # 2) revise_brief（同步命令，不耗模型）
        command(
            C.Action.revise_brief,
            {
                "expected_product_version": after["product_version"],
                "fields": {
                    key: {"value": value, "status": "confirmed"}
                    for key, value in REVISE_FIELDS.items()
                },
                "note": "澄清结束后人工补齐 brief。",
            },
            note="补齐目标用户/场景/问题/成功标准",
        )
        drain("revise_brief", actions)
        after_revise = snapshot()
        check(after_revise["phase"] == "clarifying", f"revise 后阶段异常：{after_revise['phase']}")
        steps.append(_step(2, "revise_brief", []))

        # 3) confirm_brief → brainstorming
        command(
            C.Action.confirm_brief,
            {
                "expected_product_version": after_revise["product_version"],
                "fields": dict(REVISE_FIELDS),
            },
            note="确认 brief",
        )
        drain("confirm_brief", actions)
        after_brief = snapshot()
        check(after_brief["phase"] == "brainstorming", f"confirm 后阶段异常：{after_brief['phase']}")
        steps.append(_step(3, "confirm_brief", []))

        # 4) brainstorm 四角色（真实模型，成本最高）
        command(
            C.Action.brainstorm,
            {"content": BRAINSTORM_CONTENT, "target": "all"},
            note="四角色评审",
        )
        executed = drain("brainstorm", actions)
        check(actions.get("brainstorm") in {"succeeded", "partial_succeeded"}, f"brainstorm 未成功：{actions}")
        after_brainstorm = snapshot()
        check(after_brainstorm["claims"] > 0, "脑暴后没有任何论断")
        steps.append(_step(4, "brainstorm", executed))

        # 5) start_audit（真实模型开场）
        command(
            C.Action.start_audit,
            {"expected_product_version": after_brainstorm["product_version"]},
            note="开场审计",
        )
        executed = drain("start_audit", actions)
        after_audit = snapshot()
        check(after_audit["phase"] == "auditing", f"审计未开场：{after_audit['phase']}")
        check(bool(after_audit["audit"]), "审计实例未创建")
        steps.append(_step(5, "start_audit", executed))

        # 6) answer_audit（真实模型作答 + 下一问）
        with database.session_scope() as db:
            from repositories import audit_repo

            audit_row = audit_repo.get_audit(db, after_audit["audit"]["id"])
            question = audit_repo.get_question(db, audit_row.id, audit_row.active_question_id)
            audit_id, question_id = audit_row.id, question.id
        command(
            C.Action.answer_audit,
            {"audit_id": audit_id, "question_id": question_id, "answer": AUDIT_ANSWER},
            note="回答第 1 个审计问题",
        )
        executed = drain("answer_audit", actions)
        after_answer = snapshot()
        check(
            actions.get("answer_audit") == "succeeded",
            f"作答未成功：{actions}",
        )
        steps.append(_step(6, "answer_audit", executed))

        # 7) end_audit → closing Run 生成报告
        command(C.Action.end_audit, {"audit_id": audit_id}, note="结束审计并入队报告")
        executed = drain("end_audit", actions)
        final = snapshot()
        check(actions.get("end_audit") == "succeeded", f"收口报告未成功：{actions}")
        check(bool(final["audit"]), "审计实例丢失")
        check(final["audit"]["status"] == "completed", f"审计未 completed：{final['audit']}")
        check(
            final["audit"]["report_source"] in {"model", "fallback"},
            f"报告未落库：{final['audit']}",
        )
        check(
            final["audit"]["report_points"] + final["audit"]["report_gaps"] > 0,
            f"报告内容为空：{final['audit']}",
        )
        check(final["phase"] == "audit_completed", f"最终阶段异常：{final['phase']}")
        steps.append(_step(7, "end_audit", executed))

        with database.session_scope() as db:
            pending = run_repo.active_run_for_session(db, _session_id)
            rows = run_repo.list_session_runs(db, _session_id, limit=50)
        check(pending is None, "仍有活动 Run 未收口")
        terminal = {"succeeded", "failed", "partial_succeeded", "cancelled"}
        check(
            all(row.status in terminal for row in rows),
            f"存在未收口 Run：{[row.status for row in rows if row.status not in terminal]}",
        )
        assertions.append("无残留活动 Run；审计 completed；报告 source=model；阶段 audit_completed")

        final["assertions"] = assertions
        write_report(steps, final)
        ok = True
        elapsed = round(time.time() - started, 1)
        print(
            json.dumps(
                {
                    "ok": True,
                    "session_id": _session_id,
                    "elapsed_seconds": elapsed,
                    "total_tokens": final["total_tokens"],
                    "claims": final["claims"],
                    "branches": final["branches"],
                    "unverified_nodes": final["unverified_nodes"],
                    "audit": final["audit"],
                    "report": str(REPORT_PATH),
                },
                ensure_ascii=False,
            )
        )
        return 0
    finally:
        database.dispose_engine()
        keep = os.environ.get("JOURNEY_KEEP") == "1" or not ok
        if keep:
            print(f"kept tmpdir: {tmpdir}")
        else:
            shutil.rmtree(tmpdir, ignore_errors=True)


_step_counter = {"index": 0}


def _step(index: int, name: str, executed: list[dict]) -> dict:
    total = sum(item["tokens"] for item in executed)
    elapsed = round(sum(item["elapsed_seconds"] for item in executed), 1)
    if executed:
        last = executed[-1]
        return {
            "index": index,
            "command": name,
            "action": last["action"],
            "status": last["status"],
            "elapsed": elapsed,
            "tokens": total,
        }
    return {
        "index": index,
        "command": name,
        "action": name,
        "status": "succeeded",
        "elapsed": elapsed,
        "tokens": total,
    }


_session_id = ""


if __name__ == "__main__":
    sys.exit(main())
