"""retriever 单元测试：FR-04A 结构化检索（含人工相关性标签用例）。

覆盖：中文 bigram/停用词、标题与类别加权、角色偏好、同源去重、零相关、
limit 截断、score 单调与取值范围、结构化字段完整性、证据 ID 稳定性、
确定性（固定时钟逐字节一致）、索引版本与容错、旧形态兼容。

所有用例使用临时索引文件，不读取也不修改仓库内的真实知识库。
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from pydantic import ValidationError

from rag.contracts import MAX_EXCERPT_CHARS, RetrievalHit
from rag.retriever import RAGRetriever

FIXED_NOW = "2026-11-18T03:00:00Z"
UTC_ISO_RE = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"
EVIDENCE_ID_RE = r"^[0-9a-f]{32}$"
CONTENT_HASH_RE = r"^[0-9a-f]{64}$"

# 人工构造的知识库语料：类别覆盖 architecture/security/ux/process/business/
# pricing/user-research/benchmarks/case_studies/methodologies/frameworks。
CORPUS: list[dict[str, Any]] = [
    {
        "text": "服务采用无状态架构，通过水平扩展支撑高并发与容量规划。",
        "metadata": {"source": "scaling.md", "category": "architecture", "heading": "水平扩展"},
    },
    {
        "text": "认证授权使用短期令牌与最小权限，安全架构防止越权与令牌泄露。",
        "metadata": {"source": "auth.md", "category": "security", "heading": "认证与授权"},
    },
    {
        "text": "交互流程通过渐进式引导降低上手成本，用户体验测试发现体验断点。",
        "metadata": {"source": "onboarding.md", "category": "ux", "heading": "交互流程"},
    },
    {
        "text": "研发流程采用双周迭代与复盘会议，流程改进依赖交付周期数据。",
        "metadata": {"source": "retro.md", "category": "process", "heading": "迭代流程"},
    },
    {
        "text": "商业模式按订阅收费，付费转化与生命周期价值决定收入增长。",
        "metadata": {"source": "model.md", "category": "business", "heading": "商业模式"},
    },
    {
        "text": "定价策略按用量分层，价格锚点与试用转化影响客单价。",
        "metadata": {"source": "tiers.md", "category": "pricing", "heading": "定价分层"},
    },
    {
        "text": "用户研究通过半结构化访谈理解真实场景，访谈记录提炼需求与痛点。",
        "metadata": {"source": "interviews.md", "category": "user-research", "heading": "用户访谈"},
    },
    {
        "text": "SaaS 产品留存基准：次月留存 40%，流失率衡量健康度。",
        "metadata": {"source": "retention.md", "category": "benchmarks", "heading": "留存基准"},
    },
    {
        "text": "增长案例：邀请裂变把获客成本降低三成。",
        "metadata": {"source": "growth.md", "category": "case_studies", "heading": "增长案例"},
    },
    {
        "text": "RICE 是特性优先级评分模型，用触达、影响、信心与成本排序功能。",
        "metadata": {"source": "rice.md", "category": "methodologies", "heading": "RICE 评分"},
    },
    {
        "text": "增长循环依赖邀请裂变与复购，增长飞轮持续扩大用户规模。",
        "metadata": {"source": "growth_loops.md", "category": "methodologies", "heading": "增长循环"},
    },
    {
        "text": "留存循环通过习惯养成提升复购，留存与增长相互强化。",
        "metadata": {"source": "growth_loops.md", "category": "methodologies", "heading": "留存循环"},
    },
    {
        "text": "长文说明：" + "容量规划" * 700,
        "metadata": {"source": "longform.md", "category": "frameworks", "heading": "容量规划"},
    },
    {
        "text": "外部证据：留存基准来自公开报告。",
        "metadata": {
            "source": "external.md",
            "category": "benchmarks",
            "heading": "外部证据",
            "title": "公开报告",
            "source_uri": "https://example.com/report",
            "content_hash": "a" * 64,
            "source_date": "2025-01-02",
        },
    },
    {
        "text": "流程编排关注体验细节与交互一致性。",
        "metadata": {"source": "flow_ux.md", "category": "ux", "heading": "流程体验"},
    },
    {
        "text": "流程引擎承载任务编排与调度策略。",
        "metadata": {"source": "flow_arch.md", "category": "architecture", "heading": "流程引擎"},
    },
    {
        "text": "研究预算决定验证节奏与范围。",
        "metadata": {"source": "research_biz.md", "category": "business", "heading": "研究预算"},
    },
    {
        "text": "用户研究理解真实需求与场景。",
        "metadata": {"source": "research_user.md", "category": "user-research", "heading": "用户研究"},
    },
    {
        "text": "增长基准给出行业区间参考。",
        "metadata": {"source": "bench_growth.md", "category": "benchmarks", "heading": "增长基准"},
    },
    {
        "text": "增长定价按阶段动态调整。",
        "metadata": {"source": "price_growth.md", "category": "pricing", "heading": "增长定价"},
    },
    {
        "text": "增长飞轮手册介绍复利效应。",
        "metadata": {
            "source": "titled.md",
            "category": "frameworks",
            "heading": "正文",
            "title": "增长飞轮手册",
        },
    },
]


def _payload(index_version: int | None = 1) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "documents": [item["text"] for item in CORPUS],
        "metadatas": [dict(item["metadata"]) for item in CORPUS],
    }
    if index_version is not None:
        payload["index_version"] = index_version
    return payload


class TokenizeTests(unittest.TestCase):
    def test_chinese_bigram(self):
        self.assertEqual(RAGRetriever._tokenize("产品经理"), ["产品", "品经", "经理"])

    def test_two_char_chinese_word_is_single_bigram(self):
        self.assertEqual(RAGRetriever._tokenize("增长"), ["增长"])

    def test_single_chinese_char_filtered(self):
        self.assertEqual(RAGRetriever._tokenize("好"), [])

    def test_english_kept_when_len_ge_2(self):
        tokens = RAGRetriever._tokenize("RICE framework a")
        self.assertIn("rice", tokens)
        self.assertIn("framework", tokens)
        self.assertNotIn("a", tokens)

    def test_stop_words_filtered(self):
        tokens = RAGRetriever._tokenize("the user retention")
        self.assertNotIn("the", tokens)
        self.assertIn("retention", tokens)

    def test_mixed_content(self):
        tokens = RAGRetriever._tokenize("北极星指标 North Star Metric")
        self.assertIn("北极", tokens)
        self.assertIn("指标", tokens)
        self.assertIn("north", tokens)
        self.assertIn("star", tokens)

    def test_chinese_stopword_bigram_filtered(self):
        tokens = RAGRetriever._tokenize("这个 产品")
        self.assertNotIn("这个", tokens)
        self.assertIn("产品", tokens)


class RetrieverTestCase(unittest.TestCase):
    """带临时索引的测试基类；每个用例独立临时目录。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.index_path = Path(self._tmp.name) / "knowledge_base.json"

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, payload: dict[str, Any], name: str = "knowledge_base.json") -> Path:
        path = Path(self._tmp.name) / name
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def _write_default(self) -> Path:
        return self._write(_payload())

    def _retriever(self) -> RAGRetriever:
        self._write_default()
        return RAGRetriever(index_path=self.index_path)

    def _first_source(self, hits: list[RetrievalHit]) -> str:
        self.assertTrue(hits, "期望至少一条命中")
        return hits[0].source_uri


class IndexLoadTests(RetrieverTestCase):
    def test_empty_index_when_file_missing(self):
        retriever = RAGRetriever(index_path=self.index_path)
        self.assertTrue(retriever.is_empty())
        self.assertEqual(retriever.count(), 0)
        self.assertEqual(retriever.search("任何查询"), [])

    def test_empty_index_with_empty_file(self):
        self._write({"documents": [], "metadatas": []})
        retriever = RAGRetriever(index_path=self.index_path)
        self.assertTrue(retriever.is_empty())
        self.assertEqual(retriever.search("任何查询"), [])

    def test_broken_json_is_tolerated(self):
        self.index_path.write_text("{not valid json", encoding="utf-8")
        retriever = RAGRetriever(index_path=self.index_path)
        self.assertTrue(retriever.is_empty())
        self.assertEqual(retriever.search("架构"), [])

    def test_index_version_defaults_and_explicit(self):
        self._write({"documents": [], "metadatas": [], "index_version": 7})
        self.assertEqual(RAGRetriever(index_path=self.index_path).index_version, 7)
        other = self._write({"documents": [], "metadatas": []}, name="no_version.json")
        self.assertEqual(RAGRetriever(index_path=other).index_version, 1)

    def test_load_is_thread_safe(self):
        self._write({"documents": ["文档内容。" for _ in range(10)], "metadatas": [{} for _ in range(10)]})
        retriever = RAGRetriever(index_path=self.index_path)
        errors: list[BaseException] = []

        def hit() -> None:
            try:
                retriever._load()
            except BaseException as exc:  # pragma: no cover - 记录意外异常
                errors.append(exc)

        threads = [threading.Thread(target=hit) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(retriever.count(), 10)


class SearchRelevanceTests(RetrieverTestCase):
    """人工相关性标签用例：query -> 期望命中的来源（或期望为空）。"""

    def test_english_methodology_hit(self):
        hits = self._retriever().search("RICE 优先级")
        self.assertIn("rice.md", self._first_source(hits))
        self.assertIn("RICE", hits[0].excerpt)

    def test_chinese_user_retention_hit(self):
        hits = self._retriever().search("用户留存")
        self.assertTrue(self._first_source(hits).endswith("retention.md"))

    def test_heading_weight_beats_body_only(self):
        hits = self._retriever().search("迭代流程")
        self.assertEqual(hits[0].heading, "迭代流程")

    def test_title_weight_beats_heading(self):
        hits = self._retriever().search("增长飞轮")
        self.assertTrue(self._first_source(hits).endswith("titled.md"))
        self.assertEqual(hits[0].title, "增长飞轮手册")

    def test_role_designer_prefers_process(self):
        hits = self._retriever().search("流程", role_code="designer")
        self.assertTrue(self._first_source(hits).endswith("retro.md"))

    def test_role_cto_prefers_architecture(self):
        hits = self._retriever().search("流程", role_code="cto")
        self.assertTrue(self._first_source(hits).endswith("flow_arch.md"))

    def test_role_ops_prefers_business_categories(self):
        default_hits = self._retriever().search("增长")
        self.assertFalse(default_hits[0].source_uri.endswith("bench_growth.md"))
        hits = self._retriever().search("增长", role_code="ops")
        self.assertTrue(self._first_source(hits).endswith("bench_growth.md"))

    def test_role_target_user_prefers_user_research(self):
        default_hits = self._retriever().search("研究")
        self.assertTrue(self._first_source(default_hits).endswith("research_biz.md"))
        hits = self._retriever().search("研究", role_code="target_user")
        self.assertTrue(self._first_source(hits).endswith("research_user.md"))

    def test_unknown_role_matches_no_role(self):
        baseline = self._retriever().search("流程", limit=10)
        unknown = self._retriever().search("流程", role_code="intern", limit=10)
        self.assertEqual(
            [hit.source_uri for hit in baseline],
            [hit.source_uri for hit in unknown],
        )

    def test_same_source_is_deduplicated(self):
        hits = self._retriever().search("增长循环", limit=10)
        growth_loops = [hit for hit in hits if hit.source_uri.endswith("growth_loops.md")]
        self.assertEqual(len(growth_loops), 1)
        self.assertEqual(growth_loops[0].heading, "增长循环")

    def test_limit_truncates(self):
        retriever = self._retriever()
        self.assertEqual(len(retriever.search("增长", limit=2)), 2)
        self.assertEqual(len(retriever.search("增长", limit=1)), 1)

    def test_non_positive_limit_returns_empty(self):
        retriever = self._retriever()
        self.assertEqual(retriever.search("增长", limit=0), [])
        self.assertEqual(retriever.search("增长", limit=-3), [])

    def test_zero_relevance_returns_empty(self):
        self.assertEqual(self._retriever().search("量子纠缠芯片"), [])

    def test_product_context_cannot_manufacture_relevance(self):
        hits = self._retriever().search("量子纠缠芯片", product_context="增长 留存 架构")
        self.assertEqual(hits, [])

    def test_empty_query_variants_return_empty(self):
        retriever = self._retriever()
        self.assertEqual(retriever.search(""), [])
        self.assertEqual(retriever.search("   "), [])
        self.assertEqual(retriever.search("的 是 和 the of"), [])

    def test_single_chinese_char_query_returns_empty(self):
        self.assertEqual(self._retriever().search("好"), [])

    def test_score_monotonic_and_bounded(self):
        hits = self._retriever().search("流程", limit=10)
        self.assertGreaterEqual(len(hits), 2)
        scores = [hit.score for hit in hits]
        self.assertEqual(scores, sorted(scores, reverse=True))
        for score in scores:
            self.assertGreater(score, 0.0)
            self.assertLess(score, 1.0)

    def test_product_context_boosts_relevance(self):
        retriever = self._retriever()
        baseline = retriever.search("架构")
        boosted = retriever.search("架构", product_context="授权 令牌")
        self.assertTrue(self._first_source(baseline).endswith("scaling.md"))
        self.assertTrue(self._first_source(boosted).endswith("auth.md"))

    def test_product_context_not_echoed(self):
        hits = self._retriever().search("架构", product_context="量子纠缠机密上下文")
        self.assertTrue(hits)
        for hit in hits:
            haystack = f"{hit.title}{hit.heading}{hit.excerpt}{hit.source_uri}{hit.category}"
            self.assertNotIn("量子", haystack)
            self.assertNotIn("机密", haystack)

    def test_english_query_is_case_insensitive(self):
        lower = self._retriever().search("rice")
        upper = self._retriever().search("RICE")
        self.assertEqual(
            [hit.source_uri for hit in lower],
            [hit.source_uri for hit in upper],
        )


class HitContractTests(RetrieverTestCase):
    def test_hit_fields_complete(self):
        hits = self._retriever().search("RICE 优先级")
        hit = hits[0]
        self.assertRegex(hit.evidence_id, EVIDENCE_ID_RE)
        self.assertRegex(hit.content_hash, CONTENT_HASH_RE)
        self.assertRegex(hit.retrieved_at, UTC_ISO_RE)
        self.assertGreaterEqual(hit.score, 0.0)
        self.assertLessEqual(hit.score, 1.0)
        self.assertLessEqual(len(hit.excerpt), MAX_EXCERPT_CHARS)
        self.assertTrue(hit.source_uri)
        self.assertTrue(hit.title)
        self.assertTrue(hit.heading)
        self.assertEqual(RetrievalHit.model_validate(hit.model_dump()), hit)

    def test_evidence_id_is_stable_and_reproducible(self):
        hit = self._retriever().search("RICE 优先级")[0]
        document = CORPUS[9]["text"]
        expected_content = hashlib.sha256(document.encode("utf-8")).hexdigest()
        expected_evidence = hashlib.sha256(
            f"{hit.source_uri}\x00{hit.heading}".encode("utf-8")
        ).hexdigest()[:32]
        self.assertEqual(hit.content_hash, expected_content)
        self.assertEqual(hit.evidence_id, expected_evidence)

    def test_metadata_overrides_are_applied(self):
        hits = self._retriever().search("外部证据")
        self.assertEqual(hits[0].source_uri, "https://example.com/report")
        self.assertEqual(hits[0].content_hash, "a" * 64)
        self.assertEqual(hits[0].source_date, "2025-01-02")
        self.assertEqual(hits[0].title, "公开报告")

    def test_missing_metadata_derives_defaults(self):
        self._write(
            {"documents": ["孤立文档：量子计算与纠缠实验。"], "metadatas": []},
            name="orphan.json",
        )
        retriever = RAGRetriever(index_path=Path(self._tmp.name) / "orphan.json")
        hits = retriever.search("量子")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].source_uri, "kb://unknown/0")
        self.assertEqual(hits[0].title, "")
        self.assertEqual(hits[0].heading, "")
        self.assertTrue(hits[0].excerpt)

    def test_excerpt_is_truncated_to_limit(self):
        hits = self._retriever().search("容量规划")
        hit = hits[0]
        self.assertTrue(hit.source_uri.endswith("longform.md"))
        self.assertEqual(len(hit.excerpt), MAX_EXCERPT_CHARS)
        self.assertLess(len(hit.excerpt), len(CORPUS[12]["text"]))

    def test_deterministic_with_fixed_clock(self):
        retriever = self._retriever()
        with mock.patch("rag.retriever.utc_now_iso", return_value=FIXED_NOW):
            first = [hit.model_dump() for hit in retriever.search("流程", limit=10)]
            second = [hit.model_dump() for hit in retriever.search("流程", limit=10)]
        self.assertEqual(first, second)
        self.assertTrue(all(item["retrieved_at"] == FIXED_NOW for item in first))

    def test_search_does_not_write_index(self):
        retriever = self._retriever()
        before = self.index_path.read_bytes()
        retriever.search("增长", limit=4)
        self.assertEqual(self.index_path.read_bytes(), before)

    def test_count_and_persisted_add(self):
        retriever = self._retriever()
        self.assertEqual(retriever.count(), len(CORPUS))
        retriever.add(["新增文档：量子计算实验。"], [{"source": "new.md", "category": "benchmarks", "heading": "新增"}])
        self.assertEqual(retriever.count(), len(CORPUS) + 1)
        reloaded = RAGRetriever(index_path=self.index_path)
        self.assertEqual(reloaded.count(), len(CORPUS) + 1)
        self.assertIn("new.md", reloaded.search("新增")[0].source_uri)

    def test_module_level_search_delegates_to_singleton(self):
        from rag import retriever as retriever_module

        with mock.patch.object(retriever_module, "rag_retriever", self._retriever()):
            hits = retriever_module.search("RICE 优先级", role_code="coach", limit=1)
        self.assertEqual(len(hits), 1)
        self.assertIsInstance(hits[0], RetrievalHit)
        self.assertIn("rice.md", hits[0].source_uri)


class RetrievalHitModelTests(unittest.TestCase):
    def _valid_fields(self) -> dict[str, Any]:
        return {
            "evidence_id": "0" * 32,
            "source_uri": "kb://benchmarks/x.md",
            "title": "标题",
            "heading": "小节",
            "excerpt": "片段",
            "score": 0.5,
            "category": "benchmarks",
            "source_date": None,
            "content_hash": "0" * 64,
            "retrieved_at": FIXED_NOW,
        }

    def test_rejects_extra_fields(self):
        fields = self._valid_fields() | {"unexpected": "x"}
        with self.assertRaises(ValidationError):
            RetrievalHit(**fields)

    def test_rejects_oversized_excerpt(self):
        fields = self._valid_fields() | {"excerpt": "字" * (MAX_EXCERPT_CHARS + 1)}
        with self.assertRaises(ValidationError):
            RetrievalHit(**fields)

    def test_rejects_out_of_range_score(self):
        fields = self._valid_fields() | {"score": 1.5}
        with self.assertRaises(ValidationError):
            RetrievalHit(**fields)

    def test_rejects_bad_content_hash(self):
        fields = self._valid_fields() | {"content_hash": "not-a-sha256"}
        with self.assertRaises(ValidationError):
            RetrievalHit(**fields)


class LegacySearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_legacy_returns_documents(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "knowledge_base.json"
            path.write_text(json.dumps(_payload(), ensure_ascii=False), encoding="utf-8")
            retriever = RAGRetriever(index_path=path)
            documents = await retriever.search_legacy("RICE 优先级", n_results=1)
        self.assertEqual(len(documents), 1)
        self.assertIn("RICE", documents[0])

    async def test_search_legacy_honours_zero_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "knowledge_base.json"
            path.write_text(json.dumps(_payload(), ensure_ascii=False), encoding="utf-8")
            retriever = RAGRetriever(index_path=path)
            self.assertEqual(await retriever.search_legacy("增长", n_results=0), [])


if __name__ == "__main__":
    unittest.main()
