"""上下文构建接入本地检索（FR-04A / FR-02 / AC-20）。

对应验收：
- AC-20：知识库无相关内容时背景为空，不编造命中；
- 检索命中只作为“背景知识”，**不得**被当成可引用的 ``evidence_id``；
- 检索失败不得中断上下文构建（降级为无命中）。

用 monkeypatch 替换 ``rag.retriever.search``，不依赖真实索引文件。
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from domain.contracts import MessageStage, RoleCode, SenderKind, TaskKind
from rag.contracts import RetrievalHit
from repositories import session_repo
from repositories.database import dispose_engine, reset_for_tests, session_scope
from services.context_builder import build_context


def _hit(index: int = 0) -> RetrievalHit:
    return RetrievalHit(
        evidence_id=f"{index:032x}",
        source_uri=f"kb://benchmarks/metric_{index}.md",
        title=f"指标 {index}",
        heading="留存",
        excerpt=f"第 {index} 条留存基准数据，来源为行业报告。",
        score=0.9 - index * 0.1,
        category="benchmarks",
        source_date="2025-03-01",
        content_hash="c" * 64,
        retrieved_at="2026-11-19T00:00:00Z",
    )


def _all_text(bundle) -> str:
    return "\n".join(message.content for message in bundle.messages)


class ContextBuilderRagTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        db_path = Path(self._tmp.name, "rag_ctx.db").as_posix()
        reset_for_tests(f"sqlite+pysqlite:///{db_path}")

    def tearDown(self):
        dispose_engine()
        self._tmp.cleanup()

    def _seed(self, db):
        session = session_repo.create_session(
            db, owner_email="coach@example.com", problem_statement="做一个留存分析工具"
        )
        message = session_repo.add_message(
            db,
            session,
            sender_kind=SenderKind.human,
            stage=MessageStage.clarify,
            content="目标用户是中小 SaaS 团队，想提升首月留存",
        )
        return session, message

    def _build(self, db, session, message, **kwargs):
        return build_context(
            db,
            session_id=session.id,
            task_kind=kwargs.pop("task_kind", TaskKind.clarify),
            role_code=kwargs.pop("role_code", RoleCode.coach),
            product_version=kwargs.pop("product_version", 1),
            current_message_id=message.id,
            **kwargs,
        )

    # -- 命中 -------------------------------------------------------------- #

    def test_hits_are_injected_as_non_citable_background(self):
        with session_scope() as db:
            session, message = self._seed(db)
            with mock.patch("rag.retriever.search", return_value=[_hit(0), _hit(1)]) as patched:
                bundle = self._build(db, session, message)

        self.assertEqual(patched.call_count, 1)
        text = _all_text(bundle)
        self.assertIn("[背景知识·不可引用]", text)
        self.assertIn("kb://benchmarks/metric_0.md", text)
        self.assertIn("相关度 0.90", text)
        # 背景知识不得进入可引用白名单（AC-20 / FR-04C 引用校验）
        self.assertEqual(bundle.evidence_refs, [])

    def test_role_code_and_query_are_forwarded(self):
        with session_scope() as db:
            session, message = self._seed(db)
            with mock.patch("rag.retriever.search", return_value=[]) as patched:
                self._build(db, session, message, role_code=RoleCode.cto)

        args, kwargs = patched.call_args
        self.assertEqual(kwargs["role_code"], RoleCode.cto.value)
        self.assertEqual(kwargs["limit"], 4)
        query = args[0]
        self.assertIn("留存分析工具", query)
        self.assertIn("首月留存", query)

    # -- 无命中 / 失败 ----------------------------------------------------- #

    def test_no_hits_keeps_evidence_section_absent(self):
        with session_scope() as db:
            session, message = self._seed(db)
            with mock.patch("rag.retriever.search", return_value=[]):
                bundle = self._build(db, session, message)

        self.assertNotIn("[背景知识·不可引用]", _all_text(bundle))
        self.assertFalse(any("背景知识" in note for note in bundle.notes))

    def test_retrieval_failure_degrades_to_empty(self):
        with session_scope() as db:
            session, message = self._seed(db)
            with mock.patch("rag.retriever.search", side_effect=RuntimeError("index broken")):
                bundle = self._build(db, session, message)

        self.assertNotIn("[背景知识·不可引用]", _all_text(bundle))
        self.assertIn("目标用户是中小 SaaS 团队", _all_text(bundle))  # 主流程不受影响

    def test_existing_evidence_stays_citable_and_ranks_before_background(self):
        with session_scope() as db:
            session, message = self._seed(db)
            row = session_repo.add_evidence(
                db,
                session,
                kind="user_research",
                title="5 位财务访谈",
                excerpt="访谈显示对账耗时长",
                created_by="coach@example.com",
            )
            with mock.patch("rag.retriever.search", return_value=[_hit(0)]):
                bundle = self._build(db, session, message)

        self.assertEqual(len(bundle.evidence_refs), 1)
        self.assertEqual(bundle.evidence_refs[0], row.id)
        evidence_text = _all_text(bundle)
        self.assertLess(
            evidence_text.index("5 位财务访谈"), evidence_text.index("[背景知识·不可引用]")
        )


if __name__ == "__main__":
    unittest.main()
