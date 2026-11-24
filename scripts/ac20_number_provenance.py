"""AC-20 端到端：模型编造数字的标注与验证建议（真模型，只跑一次）。

流程（除最后一次四角色脑暴外全程不耗模型额度）：
1. 复制线上库到临时文件（不污染 ``backend/data/app.db``），配置 ``DATABASE_URL``；
2. 播种会话 + 一条含数字的用户证据（访谈 12 家、每周 6 小时）；
3. ``skip_clarification`` 进入 brainstorming（同步命令，不调模型）；
4. 提交 ``brainstorm{target: all}`` → 真 ``LLMClient`` 跑四角色（约 2.5 万 tokens）；
5. 逐条论断做数字归属校验，输出 JSON 与 ``docs/ac20-number-provenance.md``（含人工标注列）。

用法::

    cd backend && ../.venv/Scripts/python.exe ../scripts/ac20_number_provenance.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
sys.path.insert(0, str(BACKEND))

from sqlalchemy import text  # noqa: E402

from domain import contracts as C  # noqa: E402
from repositories import database, run_repo, session_repo  # noqa: E402
from services import command_service, number_guard  # noqa: E402
from services.canvas_projector import project_canvas  # noqa: E402
from workflows import runner  # noqa: E402

OWNER = "ac20-number-provenance@example.com"
WORKER_ID = "ac20-e2e-worker"
PROBLEM = (
    "为中小企业财务团队做一个对账助手：他们每周要花大量时间核对银行流水与发票，"
    "希望系统能自动发现差异并给出处理建议。"
)
EVIDENCE_TITLE = "财务团队访谈记录"
EVIDENCE_EXCERPT = (
    "访谈记录：我们访谈了 12 家中小企业财务团队，"
    "平均每周对账 6 小时，其中 4 家希望先做银行流水与发票的自动比对。"
)
ANNOTATION_PATH = REPO / "docs" / "ac20-number-provenance.annotation.md"
REPORT_PATH = REPO / "docs" / "ac20-number-provenance.md"


def live_database_path() -> Path:
    """线上库文件路径（``DATABASE_URL`` 优先，否则 backend/data/app.db）。"""

    url = os.environ.get("DATABASE_URL") or database.default_database_url()
    for prefix in ("sqlite+pysqlite:///", "sqlite:///"):
        if url.startswith(prefix):
            raw = url[len(prefix) :]
            path = Path(raw)
            return path if path.is_absolute() else (BACKEND / path).resolve()
    raise SystemExit(f"不支持的数据库 URL：{url}")


def prepare_database(tmpdir: Path) -> str:
    """复制线上库到临时目录，避免污染生产数据。"""

    source = live_database_path()
    target = tmpdir / "ac20.db"
    if source.exists():
        shutil.copyfile(source, target)
    url = "sqlite:///" + target.as_posix()
    database.configure(url, force=True)
    database.initialize_database()
    return url


def seed_session() -> tuple[str, str, str]:
    """建会话 + 用户证据；返回 (session_id, message_id, evidence_id)。"""

    with database.session_scope() as db:
        session = session_repo.create_session(
            db, owner_email=OWNER, problem_statement=PROBLEM
        )
        session_id = session.id
        message = session_repo.list_messages(db, session_id, limit=1)[0]
        evidence = session_repo.add_evidence(
            db,
            session,
            kind=C.EvidenceKind.user_statement,
            title=EVIDENCE_TITLE,
            excerpt=EVIDENCE_EXCERPT,
            created_by=OWNER,
        )
        return session_id, message.id, evidence.id


def skip_clarification(session_id: str) -> None:
    with database.session_scope() as db:
        session = session_repo.get_session(db, session_id)
        assert session is not None
        command = C.CommandRequest(
            request_id=C.new_id(),
            action=C.Action.skip_clarification,
            payload={"reason": "AC-20 端到端：直接进入脑暴"},
            expected_session_version=int(session.session_version),
        )
        command_service.submit_command(
            db, owner_email=OWNER, session_id=session_id, command=command
        )


def submit_brainstorm(session_id: str) -> str:
    with database.session_scope() as db:
        session = session_repo.get_session(db, session_id)
        assert session is not None
        command = C.CommandRequest(
            request_id=C.new_id(),
            action=C.Action.brainstorm,
            payload={
                "content": (
                    "请四位专家针对对账助手给出评审意见与关键事实。"
                    "本轮要求：每位专家至少给出 1 个量化的市场/效果预估"
                    "（如自动匹配率百分比、每周节省工时、目标客户数量、付费转化率），"
                    "并在论断里写明这个数字是你基于行业经验的估算（无来源），"
                    "只有访谈证据里的数字才标注为有来源。"
                ),
                "target": "all",
            },
            expected_session_version=int(session.session_version),
        )
        receipt = command_service.submit_command(
            db, owner_email=OWNER, session_id=session_id, command=command
        )
        return receipt.run_id


def extend_deadline(run_id: str, seconds: int = 1800) -> None:
    """四角色真模型串行执行会超过默认 300s 运行超时，这里把截止时间推后。"""

    now = int(time.time())
    with database.session_scope() as db:
        db.execute(
            text(
                "update runs set deadline_at = :deadline, queue_deadline_at = :queue "
                "where id = :run_id"
            ),
            {"deadline": now + seconds, "queue": now + seconds, "run_id": run_id},
        )


def execute(run_id: str) -> None:
    """真 Worker 语义：先领取再执行（真实 LLMClient，不注入假客户端）。"""

    with database.session_scope() as db:
        claimed = run_repo.claim_next_run(
            db, worker_id=WORKER_ID, now_epoch=int(time.time())
        )
    if claimed is None or claimed.id != run_id:
        raise SystemExit(f"领取 Run 失败：{getattr(claimed, 'id', None)} != {run_id}")
    runner.execute_run(run_id, worker_id=WORKER_ID, renew_thread=False)


def reference_texts(session_id: str) -> tuple[dict[str, str], dict[str, str]]:
    with database.session_scope() as db:
        return session_repo.load_reference_texts(db, session_id)


def collect(session_id: str) -> dict[str, Any]:
    with database.session_scope() as db:
        session = session_repo.get_session(db, session_id, include_deleted=True)
        state = session_repo.get_product_state(db, session_id)
        runs = session_repo.list_messages(db, session_id, limit=1)  # 触发连接
        assert session is not None and state is not None
        del runs
        session_version = int(session.session_version)

    message_texts, evidence_texts = reference_texts(session_id)
    claims: list[dict[str, Any]] = []
    for claim in state.claims:
        parts = [message_texts.get(ref) for ref in claim.source_message_ids]
        parts.extend(evidence_texts.get(ref) for ref in claim.evidence_refs)
        source_text = "\n".join(part for part in parts if part)
        numbers = number_guard.extract_numbers(claim.text)
        unverified = [
            value
            for value in numbers
            if not number_guard.number_is_traceable(value, source_text)
        ]
        claims.append(
            {
                "claim_id": claim.id,
                "role": claim.role_code.value if claim.role_code else None,
                "kind": claim.kind.value,
                "text": claim.text,
                "numbers": numbers,
                "traceable": [value for value in numbers if value not in unverified],
                "unverified": unverified,
                "source_message_ids": list(claim.source_message_ids),
                "evidence_refs": list(claim.evidence_refs),
                "stored_unverified": list(claim.unverified_numbers),
            }
        )

    projection = project_canvas(state)
    nodes = [
        {
            "node_id": node.id,
            "branch": branch.title,
            "title": node.title,
            "detail_last_line": node.detail.splitlines()[-1]
            if node.detail
            else "",
            "unverified_numbers": list(node.unverified_numbers),
            "verification_suggestion": node.verification_suggestion,
        }
        for branch in projection.branches
        for node in branch.nodes
        if node.unverified_numbers
    ]
    return {
        "session_id": session_id,
        "session_version": session_version,
        "product_version": int(state.version),
        "claim_count": len(state.claims),
        "claims": claims,
        "unverified_nodes": nodes,
    }


def render_report(report: dict[str, Any], usage: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# AC-20 数字可追溯性端到端记录（真模型）")
    lines.append("")
    lines.append(
        "> 自动生成：`cd backend && ../.venv/Scripts/python.exe ../scripts/ac20_number_provenance.py`"
    )
    lines.append("")
    lines.append(f"- 会话：`{report['session_id']}`")
    lines.append(f"- 产品版本：v{report['product_version']}")
    lines.append(f"- 论断数：{report['claim_count']}")
    lines.append(
        f"- 模型用量：calls={usage.get('calls')} / prompt={usage.get('prompt_tokens')} "
        f"/ completion={usage.get('completion_tokens')} / 来源={usage.get('usage_source')}"
    )
    lines.append(
        f"- 未验证数字节点：{len(report['unverified_nodes'])} 个"
    )
    lines.append("")
    lines.append("## 论断级标注")
    lines.append("")
    lines.append("| # | 角色 | 论断 | 数字 | 可追溯 | 未验证 | 人工判定 |")
    lines.append("| - | ---- | ---- | ---- | ------ | ------ | -------- |")
    for index, claim in enumerate(report["claims"], start=1):
        text = claim["text"].replace("|", "\\|")
        lines.append(
            "| {index} | {role} | {text} | {numbers} | {traceable} | {unverified} |  |".format(
                index=index,
                role=claim["role"] or "-",
                text=text,
                numbers="、".join(claim["numbers"]) or "-",
                traceable="、".join(claim["traceable"]) or "-",
                unverified="、".join(claim["unverified"]) or "-",
            )
        )
    lines.append("")
    lines.append("## 画布未验证徽标")
    lines.append("")
    for node in report["unverified_nodes"]:
        lines.append(f"- **{node['branch']} / {node['title']}**")
        lines.append(f"  - 未验证数字：{'、'.join(node['unverified_numbers'])}")
        lines.append(f"  - 详情末行：{node['detail_last_line']}")
        suggestion = (node["verification_suggestion"] or "").replace("\n", " ")
        lines.append(f"  - 验证建议：{suggestion}")
    if not report["unverified_nodes"]:
        lines.append("- 本次四角色未产生无来源数字（模型未编造数字）。")
    lines.append("")
    lines.append("## 人工标注结论")
    lines.append("")
    lines.append("- [ ] 所有未验证数字已确认「来源里确实没有」→ AC-20 通过")
    lines.append("- 备注：")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    tmpdir = Path(tempfile.mkdtemp(prefix="ac20-", dir=str(BACKEND / ".tmp_tests")))
    try:
        url = prepare_database(tmpdir)
        session_id, message_id, evidence_id = seed_session()
        skip_clarification(session_id)
        run_id = submit_brainstorm(session_id)
        extend_deadline(run_id)
        execute(run_id)

        with database.session_scope() as db:
            run = run_repo.get_run(db, run_id)
            assert run is not None
            status = run.status
            error_code = run.error_code
            usage = json.loads(run.usage_json or "{}")
            steps = [
                {
                    "logical_key": step.logical_key,
                    "status": step.status,
                    "error_code": step.error_code,
                    "message": (step.error_message or "")[:160],
                }
                for step in run_repo.list_steps(db, run_id)
            ]

        report = collect(session_id)
        report.update(
            {
                "database_url": url,
                "run_id": run_id,
                "run_status": status,
                "run_error_code": error_code,
                "usage": usage,
                "seed": {
                    "message_id": message_id,
                    "evidence_id": evidence_id,
                    "evidence_excerpt": EVIDENCE_EXCERPT,
                },
            }
        )
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        rendered = render_report(report, usage)
        if ANNOTATION_PATH.exists():
            # 人工标注结论独立成文件：重跑脚本不会覆盖已完成的标注。
            marker = (
                "## 人工标注结论" + chr(10) + chr(10)
                + "- [ ] 所有未验证数字已确认「来源里确实没有」→ AC-20 通过" + chr(10)
                + "- 备注："
            )
            rendered = rendered.replace(
                marker,
                "## 人工标注结论" + chr(10) + chr(10)
                + ANNOTATION_PATH.read_text(encoding="utf-8"),
            )
        REPORT_PATH.write_text(rendered, encoding="utf-8")
        print(
            json.dumps(
                {
                    "run_status": status,
                    "run_error_code": error_code,
                    "product_version": report["product_version"],
                    "claim_count": report["claim_count"],
                    "steps": steps,
                    "unverified_nodes": len(report["unverified_nodes"]),
                    "usage": usage,
                    "report": str(REPORT_PATH),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if status == C.RunStatus.succeeded.value else 1
    finally:
        try:
            database.dispose_engine()
        finally:
            if os.environ.get("AC20_KEEP"):
                print(f"[keep] 临时库保留在 {tmpdir}")
            else:
                shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
