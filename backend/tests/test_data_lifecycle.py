"""数据生命周期（PRD AC-35 / AC-39）：软删清正文、事件压缩与游标水位。

覆盖：
* 软删会话必须清除在线正文（消息、证据、问题陈述、产品状态载荷、审计文本、闭环产物），
  同时保留骨架行与 tombstone，避免「删除后重新迁移旧源复活内容」；
* 事件压缩只处理已终结 Run，且保留每个 Run 的终结事件；
* 压缩后旧游标必须 410（EVENT_CURSOR_EXPIRED），否则客户端会静默丢事件。
"""
from __future__ import annotations

import json
import pathlib
import shutil
import sys
import tempfile
import time
import unittest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:  # pragma: no cover
    sys.path.insert(0, str(BACKEND))

from fastapi import Request  # noqa: E402
from sqlalchemy import select  # noqa: E402

from api import sse  # noqa: E402
from domain import contracts as C  # noqa: E402
from domain.errors import AppError, ErrorCode  # noqa: E402
from repositories import audit_repo, database, loop_repo, run_repo, session_repo  # noqa: E402
from repositories.database import (  # noqa: E402
    AuditQuestionRow,
    AuditRow,
    EvidenceRow,
    LoopArtifactRow,
    MessageRow,
    ProductStateRow,
)

OWNER = "lifecycle@example.com"


def _request(headers: dict[str, str] | None = None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": "GET", "headers": raw, "query_string": b""})


class DataLifecycleCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp_root = BACKEND / ".tmp_tests"
        tmp_root.mkdir(exist_ok=True)
        self.tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="lifecycle_", dir=str(tmp_root)))
        self.db_url = "sqlite:///" + (self.tmpdir / "lifecycle.db").as_posix()
        database.configure(self.db_url, force=True)
        database.initialize_database()
        self.session_id = C.new_id()
        self.now = C.utc_now_iso()
        with database.session_scope() as db:
            session_repo.create_session(
                db,
                session_id=self.session_id,
                owner_email=OWNER,
                problem_statement="生命周期：预算协同工具的敏感问题陈述",
                now_iso=self.now,
            )

    def tearDown(self) -> None:
        database.dispose_engine()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # -- 软删清正文 -------------------------------------------------------- #

    def test_soft_delete_purges_online_content_but_keeps_tombstone(self) -> None:
        with database.session_scope() as db:
            session = session_repo.require_session(db, self.session_id, owner_email=OWNER)
            session_repo.add_message(
                db,
                session,
                sender_kind=C.SenderKind.human,
                stage=C.MessageStage.clarify,
                content="用户的商业机密：下季度要裁掉整个渠道团队",
                now_iso=self.now,
            )
            evidence = session_repo.add_evidence(
                db,
                session,
                kind=C.EvidenceKind.user_statement,
                title="渠道访谈记录",
                excerpt="访谈原文：渠道返点比例 18%，竞争对手给到 22%",
                source_uri="https://intranet.example.com/interview/42",
                now_iso=self.now,
                created_by=OWNER,
            )
            state = session_repo.get_product_state(db, self.session_id)
            brief = state.brief.model_copy(
                update={
                    "core_problem": state.brief.core_problem.model_copy(
                        update={"value": "渠道返点失控", "status": C.BriefFieldStatus.confirmed}
                    )
                }
            )
            claim = C.Claim(
                id=C.new_id(),
                kind=C.ClaimKind.problem if hasattr(C.ClaimKind, "problem") else C.ClaimKind.risk,
                text="渠道返点比例过高导致毛利下滑",
                status=C.ClaimStatus.proposed,
                created_product_version=state.version,
            )
            new_state = state.model_copy(update={"version": state.version + 1, "brief": brief, "claims": [claim]})
            session_repo.save_product_state(db, session, new_state, now_iso=self.now)

            audit = audit_repo.create_audit(db, session_id=self.session_id, product_version=1, now_iso=self.now)
            question = audit_repo.add_question(
                db,
                audit,
                ordinal=1,
                dimension="problem_validity",
                issue_refs=[],
                question_text="你说的「渠道返点失控」有量化证据吗？",
                now_iso=self.now,
            )
            audit.status = C.AuditStatus.active.value
            audit.active_question_id = question.id
            audit.report_json = '{"summary": "报告正文包含敏感结论"}'
            audit.dimension_states_json = '{"problem_validity": "supported"}'

            loop = loop_repo.create_loop(
                db,
                session_id=self.session_id,
                owner_email=OWNER,
                scope=C.LoopScope.mvp,
                max_rounds=3,
                target_score=85,
                focus="渠道返点优化",
                now_iso=self.now,
            )
            loop_repo.ensure_round(db, loop_id=loop.id, ordinal=1, now_iso=self.now)
            loop_repo.add_artifact(
                db,
                loop_id=loop.id,
                ordinal=1,
                kind=C.LoopArtifactKind.mvp_spec,
                title="MVP 规格",
                payload={
                    "title": "渠道返点看板",
                    "summary": "敏感原型说明",
                    "features": [
                        {
                            "id": "f1",
                            "title": "返点对比",
                            "description": "对比自家与竞品返点",
                            "acceptance": "能看到差异",
                            "priority": "P0",
                            "effort": "M",
                        }
                    ],
                    "assumptions": [],
                    "not_in_scope": [],
                },
                now_iso=self.now,
            )

        with database.session_scope() as db:
            session = session_repo.require_session(db, self.session_id, owner_email=OWNER)
            counts = session_repo.soft_delete_session(db, session, now_iso=self.now)
            self.assertGreaterEqual(counts.get("messages", 0), 1)
            self.assertEqual(counts.get("evidence"), 1)
            self.assertEqual(counts.get("audit_questions"), 1)
            self.assertEqual(counts.get("loop_artifacts"), 1)

        with database.session_scope() as db:
            session_row = session_repo.get_session(db, self.session_id, include_deleted=True)
            self.assertIsNotNone(session_row)
            self.assertEqual(session_row.problem_statement, "", "问题陈述必须清空")
            self.assertIsNotNone(session_row.deleted_at, "必须保留 tombstone")

            messages = list(db.execute(select(MessageRow).where(MessageRow.session_id == self.session_id)).scalars())
            self.assertTrue(messages, "消息行保留用于审计追溯")
            self.assertTrue(all(m.content == "" for m in messages), "消息正文必须清空")

            evidence_row = db.execute(select(EvidenceRow).where(EvidenceRow.id == evidence.id)).scalar_one()
            self.assertEqual(evidence_row.excerpt, "")
            self.assertEqual(evidence_row.title, session_repo.PURGED_TITLE)
            self.assertIsNone(evidence_row.limitations)

            state_row = (
                db.execute(
                    select(ProductStateRow)
                    .where(ProductStateRow.session_id == self.session_id)
                    .order_by(ProductStateRow.version.desc())
                )
                .scalars()
                .first()
            )
            payload = json.loads(state_row.payload_json)
            self.assertEqual(payload["version"], 2, "版本链必须保留")
            self.assertEqual(payload["claims"], [])
            fields = [item for item in payload["brief"].values() if isinstance(item, dict)]
            self.assertTrue(fields, payload["brief"])
            self.assertTrue(all(item.get("value") in (None, "") for item in fields), payload["brief"])

            question_row = db.execute(select(AuditQuestionRow).where(AuditQuestionRow.id == question.id)).scalar_one()
            self.assertEqual(question_row.question_text, "")
            self.assertEqual(question_row.rationale, "")
            audit_row = db.execute(select(AuditRow).where(AuditRow.id == audit.id)).scalar_one()
            self.assertIsNone(audit_row.report_json, "报告正文必须清除")
            self.assertEqual(audit_row.dimension_states_json, "{}")

            artifact = db.execute(select(LoopArtifactRow).where(LoopArtifactRow.loop_id == loop.id)).scalar_one()
            self.assertEqual(json.loads(artifact.payload_json), {})

            with self.assertRaises(AppError) as ctx:
                session_repo.require_session(db, self.session_id, owner_email=OWNER)
            self.assertEqual(ctx.exception.code, ErrorCode.NOT_FOUND)

    def test_soft_delete_can_keep_content_for_debugging(self) -> None:
        with database.session_scope() as db:
            session = session_repo.require_session(db, self.session_id, owner_email=OWNER)
            session_repo.add_message(
                db,
                session,
                sender_kind=C.SenderKind.human,
                stage=C.MessageStage.clarify,
                content="保留正文用于调试",
                now_iso=self.now,
            )
            counts = session_repo.soft_delete_session(db, session, now_iso=self.now, purge_content=False)
            self.assertEqual(counts, {})

        with database.session_scope() as db:
            messages = list(db.execute(select(MessageRow).where(MessageRow.session_id == self.session_id)).scalars())
            self.assertIn("保留正文用于调试", [m.content for m in messages])

    # -- 事件压缩 ---------------------------------------------------------- #

    def _make_terminal_run(self) -> str:
        with database.session_scope() as db:
            run = run_repo.create_run(
                db,
                session_id=self.session_id,
                owner_email=OWNER,
                request_id=C.new_request_id(),
                action=C.Action.clarify.value,
                input_hash="0" * 64,
                base_session_version=1,
                base_product_version=1,
                now_iso=self.now,
            )
            for idx in range(4):
                run_repo.append_event(
                    db,
                    session_id=self.session_id,
                    run_id=run.id,
                    type=C.EventType.role_output_updated.value,
                    payload={"chunk": idx},
                    now_iso=self.now,
                )
        with database.session_scope() as db:
            claimed = run_repo.claim_next_run(db, worker_id="w-test", now_epoch=int(time.time()))
            self.assertIsNotNone(claimed)
            run_repo.release_run(
                db,
                claimed,
                worker_id="w-test",
                lease_epoch=int(claimed.lease_epoch or 0),
                status=C.RunStatus.succeeded.value,
                now_iso=self.now,
            )
            return claimed.id

    def test_compaction_keeps_terminal_event_and_skips_active_runs(self) -> None:
        terminal_run_id = self._make_terminal_run()
        with database.session_scope() as db:
            active = run_repo.create_run(
                db,
                session_id=self.session_id,
                owner_email=OWNER,
                request_id=C.new_request_id(),
                action=C.Action.clarify.value,
                input_hash="1" * 64,
                base_session_version=2,
                base_product_version=1,
                now_iso=self.now,
            )
            run_repo.append_event(
                db,
                session_id=self.session_id,
                run_id=active.id,
                type=C.EventType.run_queued.value,
                payload={},
                now_iso=self.now,
            )
            active_id = active.id

        future = "2999-01-01T00:00:00Z"
        with database.session_scope() as db:
            before = len(run_repo.events_after(db, terminal_run_id))
            removed = run_repo.compact_events(db, before_iso=future)
            self.assertEqual(removed, before - 1, "只保留终结事件")
            self.assertEqual(run_repo.compact_events(db, before_iso=future), 0, "压缩必须幂等")

        with database.session_scope() as db:
            terminal_events = run_repo.events_after(db, terminal_run_id)
            self.assertEqual(len(terminal_events), 1, "每个终结 Run 只保留最后一个事件")
            # release_run 只改 Run 状态；终结事件由命令层写，这里断言「保留的是最后一个事件」
            self.assertEqual(int(terminal_events[0].seq), int(run_repo.latest_event_seq(db, terminal_run_id)))
            self.assertEqual(len(run_repo.events_after(db, active_id)), 2, "活动 Run 的队列与自定义事件都保留")
            self.assertEqual(run_repo.oldest_event_seq(db, terminal_run_id), terminal_events[0].seq)

    def test_cursor_below_retained_watermark_is_expired(self) -> None:
        terminal_run_id = self._make_terminal_run()
        with database.session_scope() as db:
            removed = run_repo.compact_events(db, before_iso="2999-01-01T00:00:00Z")
            self.assertGreater(removed, 0)
            oldest = run_repo.oldest_event_seq(db, terminal_run_id)
            latest = run_repo.latest_event_seq(db, terminal_run_id)
        watermark = max(0, int(oldest) - 1)

        with self.assertRaises(AppError) as ctx:
            sse.resolve_start_cursor(_request(), "0", latest_event_seq=latest, retention_watermark=watermark)
        self.assertEqual(ctx.exception.code, ErrorCode.EVENT_CURSOR_EXPIRED)
        self.assertEqual(ctx.exception.http_status, 410)

        self.assertEqual(
            sse.resolve_start_cursor(
                _request(), str(watermark), latest_event_seq=latest, retention_watermark=watermark
            ),
            watermark,
        )
        with self.assertRaises(AppError) as too_far:
            sse.resolve_start_cursor(
                _request(), str(latest + 5), latest_event_seq=latest, retention_watermark=watermark
            )
        self.assertEqual(too_far.exception.code, ErrorCode.INVALID_EVENT_CURSOR)

    def test_last_event_id_is_also_subject_to_watermark(self) -> None:
        terminal_run_id = self._make_terminal_run()
        with database.session_scope() as db:
            run_repo.compact_events(db, before_iso="2999-01-01T00:00:00Z")
            oldest = run_repo.oldest_event_seq(db, terminal_run_id)
        watermark = max(0, int(oldest) - 1)
        with self.assertRaises(AppError) as ctx:
            sse.resolve_start_cursor(
                _request({"Last-Event-ID": "1"}),
                None,
                latest_event_seq=10,
                retention_watermark=watermark,
            )
        self.assertEqual(ctx.exception.code, ErrorCode.EVENT_CURSOR_EXPIRED)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()