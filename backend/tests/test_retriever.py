"""retriever 单元测试：中文 bigram 分词、空索引、检索命中、线程安全加载。"""
import json
import tempfile
import threading
import unittest
from pathlib import Path

from rag.retriever import RAGRetriever


class TokenizeTests(unittest.TestCase):
    def test_chinese_bigram(self):
        self.assertEqual(
            RAGRetriever._tokenize("产品经理"),
            ["产品", "品经", "经理"],
        )

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


class RetrieverTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.index_path = Path(self._tmp.name) / "knowledge_base.json"

    def tearDown(self):
        self._tmp.cleanup()

    def _write_index(self, documents, metadatas):
        self.index_path.write_text(
            json.dumps({"documents": documents, "metadatas": metadatas}, ensure_ascii=False),
            encoding="utf-8",
        )

    async def test_empty_index_when_file_missing(self):
        retriever = RAGRetriever(index_path=self.index_path)
        self.assertTrue(retriever.is_empty())
        self.assertEqual(retriever.count(), 0)
        self.assertEqual(await retriever.search("任何查询"), [])

    async def test_empty_index_with_empty_file(self):
        self._write_index([], [])
        retriever = RAGRetriever(index_path=self.index_path)
        self.assertTrue(retriever.is_empty())
        self.assertEqual(await retriever.search("任何查询"), [])

    async def test_search_returns_matching_document(self):
        self._write_index(
            ["RICE 是 Intercom 团队开发的特性优先级评分模型，用于客观排序产品功能。", "北极星指标是唯一的关键指标。"],
            [
                {"source": "rice.md", "heading": "概述", "category": "methodologies"},
                {"source": "star.md", "heading": "定义", "category": "methodologies"},
            ],
        )
        retriever = RAGRetriever(index_path=self.index_path)
        self.assertFalse(retriever.is_empty())
        results = await retriever.search("rice 优先级评分", n_results=1)
        self.assertEqual(len(results), 1)
        self.assertIn("RICE", results[0])

    async def test_search_no_match_returns_empty(self):
        self._write_index(["完全不相关的内容。"], [{"source": "x.md", "heading": "h", "category": "c"}])
        retriever = RAGRetriever(index_path=self.index_path)
        self.assertEqual(await retriever.search("zzzz qqqq"), [])

    def test_load_is_thread_safe(self):
        self._write_index(["文档内容。" for _ in range(10)], [{} for _ in range(10)])
        retriever = RAGRetriever(index_path=self.index_path)
        errors = []

        def hit():
            try:
                retriever._load()
            except Exception as exc:  # pragma: no cover - 记录意外异常
                errors.append(exc)

        threads = [threading.Thread(target=hit) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(retriever.count(), 10)


if __name__ == "__main__":
    unittest.main()
