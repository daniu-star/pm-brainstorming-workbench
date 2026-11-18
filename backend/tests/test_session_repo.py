"""仓储层集成测试：会话/消息/产品状态/证据（真实 SQLite 临时库）。

对应验收：AC-04（1000 条消息、分页、seq 单调）、AC-38（会话创建幂等语义）、
AC-27（owner 隔离）、AC-02（产品状态版本只增）。
"""
import tempfile
import unittest
from pathlib import Path

from domain.contracts import (
    BriefField,
    BriefFieldStatus,
    BriefStatus,
    ProductState,
    SessionPhase,
    VerificationStatus,
    empty_brief,
    new_id,
)
from domain.errors import AppError, ErrorCode
from repositories import session_repo
from repositories.database import dispose_engine, reset_for_tests, session_scope


class RepoTestCase(unittest.TestCase):
    """每个用例独立临时库，避免跨用例串状态。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        db_path = Path(self._tmp.name, "test.db").as_posix()
        reset_for_tests(f"sqlite+pysqlite:///{db_path}")

    def tearDown(self):
        dispose_engine()
        self._tmp.cleanup()


class CreateSessionTests(RepoTestCase):
    def test_creates_session_with_clarifying_phase_and_first_message(self):
        with session_scope() as db:
            session = session_repo.create_session(
                db, owner_email="a@example.com", problem_statement="做一个自动写周报的工具"
            )
            self.assertEqual(session.phase, SessionPhase.clarifying.value)
            self.assertEqual(session.session_version, 1)
            self.assertEqual(session.current_product_version, 1)
            self.assertEqual(session.brief_status, BriefStatus.draft.value)
            self.assertEqual(len(session.id), 32)

            messages = session_repo.list_messages(db, session.id)
            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0].seq, 1)
            self.assertEqual(messages[0].sender_kind, "human")
            self.assertEqual(messages[0].content, "做一个自动写周报的工具")

            state = session_repo.get_product_state(db, session.id)
            self.assertEqual(state.version, 1)
            self.assertEqual(state.brief.status, BriefStatus.draft)
            self.assertEqual(state.claims, [])

    def test_next_message_seq_continues_after_first(self):
        with session_scope() as db:
            session = session_repo.create_session(
                db, owner_email="a@example.com", problem_statement="p"
            )
            self.assertEqual(session.next_message_seq, 2)
            message = session_repo.add_message(
                db, session, sender_kind="agent", stage="clarify", content="谁是目标用户？"
            )
            self.assertEqual(message.seq, 2)
            self.assertEqual(session.next_message_seq, 3)

    def test_custom_session_id_used(self):
        fixed = new_id()
        with session_scope() as db:
            session = session_repo.create_session(
                db, session_id=fixed, owner_email="a@example.com", problem_statement="p"
            )
            self.assertEqual(session.id, fixed)


class OwnerIsolationTests(RepoTestCase):
    def test_require_session_rejects_other_owner(self):
        with session_scope() as db:
            session = session_repo.create_session(
                db, owner_email="a@example.com", problem_statement="p"
            )
            with self.assertRaises(AppError) as ctx:
                session_repo.require_session(db, session.id, owner_email="b@example.com")
            self.assertEqual(ctx.exception.code, ErrorCode.FORBIDDEN)

    def test_require_session_missing_raises_not_found(self):
        with session_scope() as db:
            with self.assertRaises(AppError) as ctx:
                session_repo.require_session(db, new_id(), owner_email="a@example.com")
            self.assertEqual(ctx.exception.code, ErrorCode.NOT_FOUND)

    def test_soft_deleted_session_not_readable(self):
        with session_scope() as db:
            session = session_repo.create_session(
                db, owner_email="a@example.com", problem_statement="p"
            )
            session_repo.soft_delete_session(db, session)
            self.assertIsNone(session_repo.get_session(db, session.id))
            with self.assertRaises(AppError):
                session_repo.require_session(db, session.id, owner_email="a@example.com")
            # include_deleted 仍可拿到（迁移/审计需要）
            self.assertIsNotNone(session_repo.get_session(db, session.id, include_deleted=True))

    def test_list_sessions_only_returns_own(self):
        with session_scope() as db:
            session_repo.create_session(db, owner_email="a@example.com", problem_statement="p1")
            session_repo.create_session(db, owner_email="b@example.com", problem_statement="p2")
            rows, cursor = session_repo.list_sessions(db, "a@example.com")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].owner_email, "a@example.com")
            self.assertIsNone(cursor)


class MessagePagingTests(RepoTestCase):
    def _seed(self, db, session, count):
        for index in range(count):
            session_repo.add_message(
                db,
                session,
                sender_kind="agent",
                stage="brainstorm",
                content=f"消息 {index + 2}",
            )

    def test_1000_messages_paging_keeps_history(self):
        with session_scope() as db:
            session = session_repo.create_session(
                db, owner_email="a@example.com", problem_statement="p"
            )
            self._seed(db, session, 999)  # 加上首条 = 1000
            self.assertEqual(session_repo.count_messages(db, session.id), 1000)

            page1 = session_repo.list_messages(db, session.id, limit=50)
            self.assertEqual(len(page1), 50)
            self.assertEqual(page1[-1].seq, 1000)
            page2 = session_repo.list_messages(db, session.id, limit=50, before_seq=page1[0].seq)
            self.assertEqual(len(page2), 50)
            self.assertLess(page2[-1].seq, page1[0].seq)

            # 追加 5 条后历史仍在，seq 继续递增
            for index in range(5):
                session_repo.add_message(
                    db, session, sender_kind="agent", stage="brainstorm", content=f"新 {index}"
                )
            self.assertEqual(session_repo.count_messages(db, session.id), 1005)
            latest = session_repo.list_messages(db, session.id, limit=5)
            self.assertEqual([m.seq for m in latest], [1001, 1002, 1003, 1004, 1005])

    def test_limit_is_clamped(self):
        with session_scope() as db:
            session = session_repo.create_session(
                db, owner_email="a@example.com", problem_statement="p"
            )
            self._seed(db, session, 150)
            rows = session_repo.list_messages(db, session.id, limit=10000)
            self.assertEqual(len(rows), 100)

    def test_stage_filter(self):
        with session_scope() as db:
            session = session_repo.create_session(
                db, owner_email="a@example.com", problem_statement="p"
            )
            session_repo.add_message(
                db, session, sender_kind="agent", stage="brainstorm", content="b"
            )
            rows = session_repo.list_messages(db, session.id, stage="brainstorm")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].content, "b")


class ProductStateVersionTests(RepoTestCase):
    def test_versions_only_increase(self):
        with session_scope() as db:
            session = session_repo.create_session(
                db, owner_email="a@example.com", problem_statement="p"
            )
            state = session_repo.get_product_state(db, session.id)
            brief = empty_brief()
            brief.target_users = BriefField(value="企业财务", status=BriefFieldStatus.confirmed)
            brief.status = BriefStatus.ready
            next_state = ProductState(
                session_id=session.id,
                version=2,
                brief=brief,
                source_run_id=new_id(),
            )
            session_repo.save_product_state(db, session, next_state)
            self.assertEqual(session.current_product_version, 2)
            restored = session_repo.get_product_state(db, session.id)
            self.assertEqual(restored.version, 2)
            self.assertEqual(restored.brief.target_users.value, "企业财务")

            # 旧版本仍可读取（不原地覆盖）
            old = session_repo.get_product_state(db, session.id, version=1)
            self.assertEqual(old.version, 1)
            self.assertEqual(old.brief.status, BriefStatus.draft)

    def test_saving_same_or_lower_version_rejected(self):
        with session_scope() as db:
            session = session_repo.create_session(
                db, owner_email="a@example.com", problem_statement="p"
            )
            state = session_repo.get_product_state(db, session.id)
            with self.assertRaises(AppError) as ctx:
                session_repo.save_product_state(db, session, state)
            self.assertEqual(ctx.exception.code, ErrorCode.INVALID_REQUEST)


class EvidenceTests(RepoTestCase):
    def test_add_evidence_defaults_to_unverified(self):
        with session_scope() as db:
            session = session_repo.create_session(
                db, owner_email="a@example.com", problem_statement="p"
            )
            row = session_repo.add_evidence(
                db,
                session,
                kind="market_source",
                title="市场规模",
                excerpt="2025 年中国企业服务市场规模为 1200 亿元",
                source_uri="https://example.com/report",
                source_date="2025-06-01",
            )
            self.assertEqual(row.verification_status, VerificationStatus.unverified.value)
            self.assertEqual(len(row.content_hash), 64)
            model = session_repo.evidence_to_model(row)
            self.assertEqual(model.verification_status, VerificationStatus.unverified)
            self.assertEqual(session_repo.get_evidence(db, row.id).title, "市场规模")

    def test_public_knowledge_domain_has_no_session(self):
        with session_scope() as db:
            row = session_repo.add_evidence(
                db, None, kind="methodology", title="方法", excerpt="双钻模型"
            )
            self.assertIsNone(row.session_id)


class TouchSessionTests(RepoTestCase):
    def test_unset_keeps_value_and_none_clears(self):
        with session_scope() as db:
            session = session_repo.create_session(
                db, owner_email="a@example.com", problem_statement="p"
            )
            session_repo.touch_session(db, session, active_run_id="run-1")
            self.assertEqual(session.active_run_id, "run-1")
            session_repo.touch_session(db, session, phase=SessionPhase.brainstorming)
            self.assertEqual(session.phase, SessionPhase.brainstorming.value)
            self.assertEqual(session.active_run_id, "run-1")
            session_repo.touch_session(db, session, active_run_id=None)
            self.assertIsNone(session.active_run_id)

    def test_bump_session_version_increments(self):
        with session_scope() as db:
            session = session_repo.create_session(
                db, owner_email="a@example.com", problem_statement="p"
            )
            self.assertEqual(session_repo.bump_session_version(db, session), 2)
            self.assertEqual(session_repo.bump_session_version(db, session), 3)


if __name__ == "__main__":
    unittest.main()
