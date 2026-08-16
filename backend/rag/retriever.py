"""Keyword-based document retriever for RAG. No embedding service required."""
import json
import logging
import os
import re
import threading
from pathlib import Path

# 索引路径相对 backend 根目录解析，不依赖启动 cwd（B004）。
INDEX_PATH = Path(__file__).resolve().parents[1] / "data" / "knowledge_base.json"

logger = logging.getLogger(__name__)

# 匹配纯中文（中日韩统一表意文字）片段。
_CJK_RE = re.compile(r"[一-鿿]+")


class RAGRetriever:
    def __init__(self, index_path: str | Path | None = None):
        self._index_path = Path(index_path) if index_path else INDEX_PATH
        self._documents: list[str] = []
        self._metadatas: list[dict] = []
        self._loaded = False
        self._load_lock = threading.Lock()

    def _load(self):
        """线程安全地惰性加载索引（B067）。"""
        if self._loaded:
            return
        with self._load_lock:
            if self._loaded:
                return
            if os.path.exists(self._index_path):
                try:
                    with open(self._index_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        self._documents = data.get("documents", [])
                        self._metadatas = data.get("metadatas", [])
                except (json.JSONDecodeError, OSError):
                    logger.warning("RAG 索引加载失败：%s", self._index_path)
                    self._documents = []
                    self._metadatas = []
            self._loaded = True

    async def search(self, query: str, n_results: int = 5) -> list[str]:
        """Keyword-based search: tokenize query and score documents by keyword overlap."""
        self._load()
        if not self._documents:
            return []

        query_keywords = set(self._tokenize(query))
        if not query_keywords:
            return []

        scored = []
        for i, doc in enumerate(self._documents):
            doc_lower = doc.lower()
            score = sum(1 for kw in query_keywords if kw in doc_lower)
            # Bonus for category match in metadata
            meta = self._metadatas[i] if i < len(self._metadatas) else {}
            cat = meta.get("category", "")
            heading = meta.get("heading", "").lower()
            for kw in query_keywords:
                if kw in cat.lower():
                    score += 2
                if kw in heading:
                    score += 3
            if score > 0:
                scored.append((score, doc))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:n_results]
        return [doc for _, doc in top if _ > 0]

    def is_empty(self) -> bool:
        self._load()
        return len(self._documents) == 0

    def add(self, documents: list[str], metadatas: list[dict]):
        self._load()
        self._documents.extend(documents)
        self._metadatas.extend(metadatas)
        self._save()

    def count(self) -> int:
        self._load()
        return len(self._documents)

    def _save(self):
        os.makedirs(self._index_path.parent, exist_ok=True)
        with open(self._index_path, "w", encoding="utf-8") as f:
            json.dump({
                "documents": self._documents,
                "metadatas": self._metadatas,
            }, f, ensure_ascii=False)

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """分词：中文按 bigram 切分提升召回（B066），英文/数字保留 len>=2。

        单字中文 token 全部过滤（bigram 天然为双字）。
        """
        tokens: list[str] = []
        for raw in re.findall(r"[a-zA-Z一-鿿\d]+", text.lower()):
            for part in re.findall(r"[一-鿿]+|[a-z\d]+", raw):
                if _CJK_RE.fullmatch(part):
                    if len(part) == 1:
                        continue
                    tokens.extend(part[i : i + 2] for i in range(len(part) - 1))
                elif len(part) >= 2 and part not in STOP_WORDS:
                    tokens.append(part)
        return tokens


STOP_WORDS = {
    "the", "is", "in", "of", "to", "and", "a", "an", "it", "for", "on", "with",
    "as", "at", "by", "or", "be", "this", "that", "from", "are", "we", "you",
    "的", "是", "在", "和", "了", "有", "我", "不", "人", "这", "中", "大",
    "就", "也", "都", "要", "会", "可以", "一个", "没有", "他们", "我们",
    "什么", "自己", "怎么", "如果", "因为", "所以", "但是", "然后", "这个",
}


rag_retriever = RAGRetriever()
