"""浏览器 E2E 的数据播种与清理（直接写库，不调模型，不耗额度）。

所有种子数据都由真实仓储函数写入（``session_repo`` / ``run_repo`` / ``audit_repo``），
只跳过 LLM 调用，因此前端读到的仍是真实 API 投影出来的结构
（画布 = ``project_canvas`` 确定性投影）。

用法::

    from seed import configure_live_database, seed_chat_session
    configure_live_database()
    info = seed_chat_session("e2e@example.com", problem_statement="...", assistant_reply="...")
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[2] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from domain import contracts as C  # noqa: E402
from repositories import database, run_repo, session_repo  # noqa: E402

SEED_WORKER = "seed-worker"


def configure_live_database() -> str:
    """连接正在运行的 API 所使用的同一个库。

    口径与后端一致：``DATABASE_URL`` 环境变量优先，否则 ``backend/data/app.db``
    （API 由 ``scripts/launch.ps1`` 启动时用的就是同一个默认值）。
    """
    url = database.default_database_url()
    database.configure(url, force=True)
    return url


def _start_run_without_worker(db, run, *, now_iso: str, lease_seconds: int = 300) -> None:
    """把种子 Run 直接置为「已被 worker 领取」，跳过排队与 LLM 调用。"""
    now_epoch = int(time.time())
    run.status = C.RunStatus.running.value
    run.lease_owner = SEED_WORKER
    run.lease_epoch = 1
    run.lease_expires_at = now_epoch + lease_seconds
    run.started_at = now_iso
    run.heartbeat_at = now_iso
    db.flush()


def seed_chat_session(
    owner_email: str,
    *,
    problem_statement: str,
    assistant_reply: str,
    action: str = C.Action.clarify.value,
) -> dict[str, Any]:
    """一个「已有一问一答、且有一个成功的 clarify Run」的会话（供 AC-01 刷新测试）。"""
    now_iso = C.utc_now_iso()
    with database.session_scope() as db:
        session = session_repo.create_session(
            db, owner_email=owner_email, problem_statement=problem_statement, now_iso=now_iso
        )
        sid = session.id
        run = run_repo.create_run(
            db,
            session_id=sid,
            owner_email=owner_email,
            request_id=f"seed-{sid}",
            action=action,
            input_hash=f"seed-hash-{sid}",
            base_session_version=int(session.session_version),
            base_product_version=int(session.current_product_version),
            deadline_at=int(time.time()) + 300,
            queue_deadline_at=int(time.time()) + 600,
            now_iso=now_iso,
        )
        _start_run_without_worker(db, run, now_iso=now_iso)
        step = run_repo.ensure_step(
            db,
            run,
            logical_key=f"{action}.generate",
            kind=C.TaskKind.clarify.value,
            now_iso=now_iso,
        )
        run_repo.start_step(db, step, now_iso=now_iso)
        run_repo.finish_step(
            db,
            step,
            status=C.StepStatus.succeeded,
            output={"committed": True, "seeded": True},
            now_iso=now_iso,
        )
        run_repo.release_run(
            db,
            run,
            worker_id=SEED_WORKER,
            lease_epoch=1,
            status=C.RunStatus.succeeded,
            result_product_version=int(session.current_product_version),
            now_iso=now_iso,
            usage={
                "prompt_tokens": 900,
                "completion_tokens": 200,
                "total_tokens": 1100,
                "calls": 1,
                "upstream_calls": 1,
            },
        )
        session_repo.add_message(
            db,
            session,
            sender_kind=C.SenderKind.agent,
            role_code=C.RoleCode.coach,
            stage=C.MessageStage.clarify,
            content=assistant_reply,
            run_id=run.id,
            now_iso=now_iso,
        )
        session_repo.touch_session(
            db,
            session,
            phase=C.SessionPhase.clarifying,
            active_run_id=None,
            now_iso=now_iso,
        )
        return {
            "session_id": sid,
            "run_id": run.id,
            "messages": session_repo.count_messages(db, sid),
            "session_version": int(session.session_version),
        }


def seed_branchy_canvas_session(owner_email: str, *, problem_statement: str) -> dict[str, Any]:
    """8 个分支都有节点的产品状态（AC-19：分支不能被折叠成「+N 更多」）。"""
    from services.canvas_projector import project_canvas

    now_iso = C.utc_now_iso()
    with database.session_scope() as db:
        session = session_repo.create_session(
            db, owner_email=owner_email, problem_statement=problem_statement, now_iso=now_iso
        )
        sid = session.id
        message = session_repo.add_message(
            db,
            session,
            sender_kind=C.SenderKind.human,
            stage=C.MessageStage.brainstorm,
            content=f"补充说明：{problem_statement}",
            now_iso=now_iso,
        )
        evidence = session_repo.add_evidence(
            db,
            session,
            kind=C.EvidenceKind.user_statement,
            title="用户访谈记录",
            excerpt="12 家中小企业反馈：对账平均每周耗时 6 小时。",
            created_by=owner_email,
            now_iso=now_iso,
        )
        refs = [message.id]
        ev = [evidence.id]

        claims = [
            C.Claim(
                id=C.new_id(),
                kind=C.ClaimKind.fact,
                text="目标用户每周对账耗时约 6 小时，是当前最痛的环节。",
                source_message_ids=refs,
                evidence_refs=ev,
                role_code=C.RoleCode.target_user,
                confidence_level="high",
            ),
            C.Claim(
                id=C.new_id(),
                kind=C.ClaimKind.hypothesis,
                text="如果把对账自动化到 15 分钟，客户愿意为效率提升付费。",
                role_code=C.RoleCode.cto,
                confidence_level="medium",
            ),
            C.Claim(
                id=C.new_id(),
                kind=C.ClaimKind.feature,
                text="自动匹配银行流水与发票，是首版必须有的核心功能。",
                role_code=C.RoleCode.designer,
                source_message_ids=refs,
            ),
            C.Claim(
                id=C.new_id(),
                kind=C.ClaimKind.risk,
                text="不同银行的流水格式差异大，首版若铺太宽会拖慢交付。",
                role_code=C.RoleCode.ops,
                evidence_refs=ev,
            ),
            C.Claim(
                id=C.new_id(),
                kind=C.ClaimKind.question,
                text="是否需要支持多主体（母公司 + 子公司）合并对账？",
                role_code=C.RoleCode.target_user,
            ),
            C.Claim(
                id=C.new_id(),
                kind=C.ClaimKind.insight,
                text="财务团队更在意「可解释的差异说明」，而不只是速度。",
                role_code=C.RoleCode.designer,
                source_message_ids=refs,
            ),
        ]
        options = [
            C.OptionItem(
                id=C.new_id(),
                title="方案 A：先做单主体自动对账",
                summary="聚焦一家银行 + 一种凭证，2 周内可交付最小闭环。",
                claim_ids=[claims[2].id],
            ),
            C.OptionItem(
                id=C.new_id(),
                title="方案 B：直接做多主体合并对账",
                summary="覆盖更全，但首版交付周期拉长到 8 周。",
                claim_ids=[claims[4].id],
            ),
        ]
        decisions = [
            C.DecisionItem(
                id=C.new_id(),
                option_id=options[0].id,
                rationale="先用方案 A 验证付费意愿，再扩展到多主体。",
                status=C.DecisionStatus.proposed,
            )
        ]
        experiments = [
            C.ExperimentItem(
                id=C.new_id(),
                hypothesis_claim_id=claims[1].id,
                method="招 5 家客户做 2 周试点，记录每周节省工时与付费意愿。",
                success_criterion="5 家中至少 3 家愿意继续付费试用。",
                status=C.ExperimentStatus.proposed,
            )
        ]
        issues = [
            C.UnresolvedIssue(
                id=C.new_id(),
                kind="gap",
                text="试点客户的选取标准还没定：行业、规模、现有工具。",
                status=C.IssueStatus.open,
            )
        ]
        state = C.initial_product_state(sid)
        state = state.model_copy(
            update={
                "version": int(state.version) + 1,
                "claims": claims,
                "options": options,
                "decisions": decisions,
                "experiments": experiments,
                "unresolved_issues": issues,
            }
        )
        session_repo.save_product_state(db, session, state, now_iso=now_iso)
        session_repo.touch_session(
            db, session, phase=C.SessionPhase.brainstorming, now_iso=now_iso
        )
        projection = project_canvas(state)
        return {
            "session_id": sid,
            "product_version": int(state.version),
            "branches": [(b.key, b.title, len(b.nodes)) for b in projection.branches],
            "last_branch_title": projection.branches[-1].title if projection.branches else None,
            "last_node_detail": projection.branches[-1].nodes[0].detail if projection.branches else "",
        }


def seed_audit_session(owner_email: str, *, problem_statement: str) -> dict[str, Any]:
    """active 审计 + 一个问题（供 AC-33 纯文字路径演练）。"""
    from repositories import audit_repo

    now_iso = C.utc_now_iso()
    with database.session_scope() as db:
        session = session_repo.create_session(
            db, owner_email=owner_email, problem_statement=problem_statement, now_iso=now_iso
        )
        sid = session.id
        audit = audit_repo.create_audit(
            db,
            session_id=sid,
            product_version=int(session.current_product_version),
            now_iso=now_iso,
        )
        question = audit_repo.add_question(
            db,
            audit,
            ordinal=1,
            dimension="problem_validity",
            issue_refs=[],
            question_text="这个问题在真实场景里有多常见？请给出你观察到的事实。",
            now_iso=now_iso,
        )
        audit_repo.update_audit(
            db,
            audit,
            status=C.AuditStatus.active,
            active_question_id=question.id,
            question_count=1,
            now_iso=now_iso,
        )
        session_repo.touch_session(
            db,
            session,
            phase=C.SessionPhase.auditing,
            current_audit_id=audit.id,
            now_iso=now_iso,
        )
        return {"session_id": sid, "audit_id": audit.id, "question_id": question.id}


def cleanup_sessions(session_ids: list[str]) -> int:
    """硬删种子会话（子表先删，避免外键约束），保持线上库整洁。"""
    from sqlalchemy import text

    statements = [
        "delete from step_candidates where run_id in (select id from runs where session_id = :sid)",
        "delete from steps where run_id in (select id from runs where session_id = :sid)",
        "delete from events where run_id in (select id from runs where session_id = :sid)",
        "delete from audit_assessment_revisions where audit_id in (select id from audits where session_id = :sid)",
        "delete from audit_report_revisions where audit_id in (select id from audits where session_id = :sid)",
        "delete from audit_questions where audit_id in (select id from audits where session_id = :sid)",
        "delete from audits where session_id = :sid",
        "delete from control_intents where session_id = :sid",
        "delete from idempotency_records where session_id = :sid",
        "delete from loop_artifacts where loop_id in (select id from loops where session_id = :sid)",
        "delete from loop_stage_results where loop_id in (select id from loops where session_id = :sid)",
        "delete from loop_rounds where loop_id in (select id from loops where session_id = :sid)",
        "delete from loops where session_id = :sid",
        "delete from legacy_artifacts where session_id = :sid",
        "delete from session_id_aliases where session_id = :sid",
        "delete from evidence where session_id = :sid",
        "delete from messages where session_id = :sid",
        "delete from product_states where session_id = :sid",
        "delete from runs where session_id = :sid",
        "delete from sessions where id = :sid",
    ]
    removed = 0
    with database.session_scope() as db:
        for sid in session_ids:
            for statement in statements:
                try:
                    db.execute(text(statement), {"sid": sid})
                except Exception:  # noqa: BLE001 - 表结构差异时跳过，最后一定清 sessions
                    continue
            removed += 1
    return removed