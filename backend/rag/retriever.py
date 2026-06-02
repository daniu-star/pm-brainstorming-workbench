"""Keyword-based document retriever for RAG. No embedding service required."""
import os
import json
import re
from typing import List, Optional

INDEX_PATH = "./data/knowledge_base.json"


class RAGRetriever:
    def __init__(self):
        self._documents: List[str] = []
        self._metadatas: List[dict] = []
        self._loaded = False

    def _load(self):
        if self._loaded:
            return
        if os.path.exists(INDEX_PATH):
            with open(INDEX_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                self._documents = data.get("documents", [])
                self._metadatas = data.get("metadatas", [])
        self._loaded = True

    async def search(self, query: str, n_results: int = 5) -> List[str]:
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

    def add(self, documents: List[str], metadatas: List[dict]):
        self._load()
        self._documents.extend(documents)
        self._metadatas.extend(metadatas)
        self._save()

    def count(self) -> int:
        self._load()
        return len(self._documents)

    def _save(self):
        os.makedirs(os.path.dirname(INDEX_PATH), exist_ok=True)
        with open(INDEX_PATH, "w", encoding="utf-8") as f:
            json.dump({
                "documents": self._documents,
                "metadatas": self._metadatas,
            }, f, ensure_ascii=False)

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        tokens = []
        for m in re.finditer(r"[a-zA-Z\d]+|[一-鿿]", text.lower()):
            t = m.group()
            if len(t) > 1 and t not in STOP_WORDS:
                tokens.append(t)
            elif re.match(r"[一-鿿]", t) and t not in STOP_WORDS:
                tokens.append(t)
        return tokens


STOP_WORDS = {
    "the", "is", "in", "of", "to", "and", "a", "an", "it", "for", "on", "with",
    "as", "at", "by", "or", "be", "this", "that", "from", "are", "we", "you",
    "的", "是", "在", "和", "了", "有", "我", "不", "人", "这", "中", "大",
    "就", "也", "都", "要", "会", "可以", "一个", "没有", "他们", "我们",
    "什么", "自己", "怎么", "如果", "因为", "所以", "但是", "然后", "这个",
}


rag_retriever = RAGRetriever()
