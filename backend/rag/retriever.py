"""本地关键词知识检索（PRD FR-04A）。

V1 只返回裸文本，来源信息在 API 层丢失（ARC-11）。V4 升级为结构化命中：

* ``search(query, *, role_code=None, product_context=None, limit=4)``
  返回 :class:`~rag.contracts.RetrievalHit` 列表；
* 中文按双字（bigram）切分，英文/数字保留长度 >= 2 的词项，中英文均过滤停用词；
* 正文词频（次线性）、标题、小节标题、类别分别加权；
* ``role_code`` 给出类别偏好加权（如 cto 偏 architecture/security），
  ``product_context`` 参与打分但不会逐字回显到命中字段；
* 同一 ``source_uri`` 只保留得分最高的一条；零相关返回空列表；
* ``evidence_id`` 由 ``sha256(source_uri + heading)`` 稳定派生，
  ``content_hash`` 为文档内容 sha256，``retrieved_at`` 取 UTC ISO 时间。

检索是纯本地、只读、无模型调用的：不写库、不改索引文件、不访问网络。
旧调用形态保留为 :meth:`RAGRetriever.search_legacy`（async，返回文档文本），
仍可被历史代码 ``await``；``rag/index_builder.py`` 只依赖 ``INDEX_PATH``，不受影响。
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from domain.contracts import utc_now_iso
from rag.contracts import MAX_EXCERPT_CHARS, RetrievalHit

# 索引路径相对 backend 根目录解析，不依赖启动 cwd（B004）。
INDEX_PATH = Path(__file__).resolve().parents[1] / "data" / "knowledge_base.json"

#: 索引文件格式版本；索引可显式写入 ``index_version``，缺省视为 1。
INDEX_SCHEMA_VERSION = 1

#: 查询/上下文参与打分词项数量上限，避免超长输入放大计算量。
MAX_QUERY_TERMS = 32
MAX_CONTEXT_TERMS = 64

#: 各维度权重；标题/类别单独加权是 FR-04A 的明确要求。
WEIGHT_BODY = 1.0
WEIGHT_HEADING = 4.0
WEIGHT_TITLE = 3.0
WEIGHT_CATEGORY = 2.0
WEIGHT_CONTEXT_BODY = 0.5
WEIGHT_CONTEXT_HEADING = 1.0

#: 角色类别偏好命中时的乘数。
ROLE_CATEGORY_BOOST = 1.35

#: 分数饱和归一化常数：``score = raw / (raw + SATURATION)``，恒在 (0, 1)。
SCORE_SATURATION = 2.0

#: 角色 -> 偏好类别（小写）。未知角色不产生偏好加权。
ROLE_CATEGORY_PREFERENCE: dict[str, frozenset[str]] = {
    "cto": frozenset({"architecture", "security", "frameworks"}),
    "designer": frozenset({"ux", "process", "user-research"}),
    "ops": frozenset({"business", "pricing", "benchmarks"}),
    "target_user": frozenset({"user-research", "ux", "case_studies"}),
    "coach": frozenset({"methodologies", "frameworks", "benchmarks"}),
    "moderator": frozenset({"methodologies", "case_studies", "benchmarks"}),
    "interviewer": frozenset({"user-research", "ux"}),
}

logger = logging.getLogger(__name__)

# 匹配纯中文（中日韩统一表意文字）片段。
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
# 提取中英文/数字片段，供进一步切分。
_WORD_RE = re.compile(r"[a-zA-Z\u4e00-\u9fff\d]+")
# 中英文词项内部的切分：连续中文 / 连续拉丁数字。
_PART_RE = re.compile(r"[\u4e00-\u9fff]+|[a-z\d]+")
# 合法 sha256 十六进制。
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class _Candidate:
    """打分阶段的内部候选（尚未去重/截断）。"""

    raw: float
    doc_index: int
    doc: str
    source_uri: str
    title: str
    heading: str
    category: str
    content_hash: str
    source_date: str | None


class RAGRetriever:
    """基于本地关键词的知识检索器。

    索引为 JSON：``{"documents": [...], "metadatas": [...]}``，可选
    ``index_version`` 声明格式版本。文档与 metadata 按下标一一对应；
    metadata 缺字段时按来源派生，不会抛错。
    """

    def __init__(self, index_path: str | Path | None = None) -> None:
        self._index_path = Path(index_path) if index_path else INDEX_PATH
        self._documents: list[str] = []
        self._metadatas: list[dict[str, Any]] = []
        self._index_version: int = INDEX_SCHEMA_VERSION
        self._loaded = False
        self._load_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # 索引加载
    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        """线程安全地惰性加载索引（B067）。"""
        if self._loaded:
            return
        with self._load_lock:
            if self._loaded:
                return
            if os.path.exists(self._index_path):
                try:
                    with open(self._index_path, "r", encoding="utf-8") as handle:
                        data = json.load(handle)
                    documents = data.get("documents", [])
                    metadatas = data.get("metadatas", [])
                    if not isinstance(documents, list) or not isinstance(metadatas, list):
                        raise ValueError("index documents/metadatas 必须是数组")
                    self._documents = [doc for doc in documents if isinstance(doc, str)]
                    self._metadatas = [meta for meta in metadatas if isinstance(meta, dict)]
                    self._index_version = _coerce_index_version(data)
                except (json.JSONDecodeError, OSError, ValueError):
                    logger.warning("RAG 索引加载失败：%s", self._index_path)
                    self._documents = []
                    self._metadatas = []
                    self._index_version = INDEX_SCHEMA_VERSION
            self._loaded = True

    def is_empty(self) -> bool:
        """索引为空或不可读时返回 True。"""
        self._load()
        return len(self._documents) == 0

    def count(self) -> int:
        """索引文档切片数量。"""
        self._load()
        return len(self._documents)

    @property
    def index_version(self) -> int:
        """索引文件声明的格式版本（缺省为 1）。"""
        self._load()
        return self._index_version

    def add(self, documents: list[str], metadatas: list[dict[str, Any]]) -> None:
        """向索引追加文档并落盘（保留现有行为，供离线扩充使用）。"""
        self._load()
        self._documents.extend(documents)
        self._metadatas.extend(metadatas)
        self._save()

    def _save(self) -> None:
        os.makedirs(self._index_path.parent, exist_ok=True)
        with open(self._index_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "index_version": self._index_version,
                    "documents": self._documents,
                    "metadatas": self._metadatas,
                },
                handle,
                ensure_ascii=False,
            )

    # ------------------------------------------------------------------ #
    # 检索（FR-04A）
    # ------------------------------------------------------------------ #
    def search(
        self,
        query: str,
        *,
        role_code: str | None = None,
        product_context: str | None = None,
        limit: int = 4,
    ) -> list[RetrievalHit]:
        """按关键词相关度返回结构化命中，按 score 降序且同源去重。

        Args:
            query: 用户查询文本；空串、纯停用词或纯单字中文返回空列表。
            role_code: 角色代码（``RoleCode`` 或其字符串值），用于类别偏好加权。
            product_context: 产品上下文文本，只参与打分，不会回显到命中字段。
            limit: 返回条数上限；``<= 0`` 返回空列表。

        Returns:
            去重、截断后的 :class:`~rag.contracts.RetrievalHit` 列表；
            零相关时为空列表（不伪造命中）。
        """
        if limit <= 0:
            return []
        candidates = self._rank(query, role_code=role_code, product_context=product_context)
        if not candidates:
            return []

        now_iso = utc_now_iso()
        hits: list[RetrievalHit] = []
        seen_sources: set[str] = set()
        for candidate in candidates:
            if candidate.source_uri in seen_sources:
                continue
            seen_sources.add(candidate.source_uri)
            hits.append(self._to_hit(candidate, now_iso=now_iso))
            if len(hits) >= int(limit):
                break
        return hits

    async def search_legacy(self, query: str, n_results: int = 5) -> list[str]:
        """兼容旧 ``await retriever.search(query, n_results)`` 的裸文本形态。

        仅用于历史调用方过渡；新代码请使用 :meth:`search` 以获得结构化命中。
        """
        if n_results <= 0:
            return []
        candidates = self._rank(query, role_code=None, product_context=None)
        return [candidate.doc for candidate in candidates[: int(n_results)]]

    def _rank(
        self,
        query: str,
        *,
        role_code: str | None,
        product_context: str | None,
    ) -> list[_Candidate]:
        """打分并排序候选；同分按来源/标题稳定排序，保证结果可复现。"""
        self._load()
        if not self._documents:
            return []
        query_terms = _unique_terms(query, MAX_QUERY_TERMS)
        if not query_terms:
            return []
        context_terms = _unique_terms(product_context, MAX_CONTEXT_TERMS)
        preferences = ROLE_CATEGORY_PREFERENCE.get(_normalize_role(role_code), frozenset())

        candidates: list[_Candidate] = []
        for index, doc in enumerate(self._documents):
            if not doc or not doc.strip():
                continue
            meta = self._metadata_at(index)
            heading = _as_text(meta.get("heading"))
            title = _as_text(meta.get("title")) or _title_from_source(meta)
            category = _as_text(meta.get("category")).lower()
            source_uri = _source_uri(meta, index)
            query_raw, context_raw = _score_document(
                doc_lower=doc.lower(),
                heading_lower=heading.lower(),
                title_lower=title.lower(),
                category_lower=category,
                query_terms=query_terms,
                context_terms=context_terms,
            )
            # 上下文只用于在“查询相关”的候选内部重排，不能凭空制造相关性。
            if query_raw <= 0.0:
                continue
            raw = query_raw + context_raw
            if preferences and category in preferences:
                raw *= ROLE_CATEGORY_BOOST
            candidates.append(
                _Candidate(
                    raw=raw,
                    doc_index=index,
                    doc=doc.strip(),
                    source_uri=source_uri,
                    title=title,
                    heading=heading or title,
                    category=category,
                    content_hash=_content_hash(doc, meta),
                    source_date=_as_optional_text(meta.get("source_date")),
                )
            )

        candidates.sort(key=lambda item: (-item.raw, item.source_uri, item.heading, item.doc_index))
        return candidates

    def _metadata_at(self, index: int) -> dict[str, Any]:
        if index < len(self._metadatas):
            return self._metadatas[index]
        return {}

    @staticmethod
    def _to_hit(candidate: _Candidate, *, now_iso: str) -> RetrievalHit:
        """把候选转成契约模型（分数归一化、片段截断、证据 ID 稳定派生）。"""
        excerpt = candidate.doc[:MAX_EXCERPT_CHARS]
        return RetrievalHit(
            evidence_id=_evidence_id(candidate.source_uri, candidate.heading),
            source_uri=candidate.source_uri,
            title=candidate.title,
            heading=candidate.heading,
            excerpt=excerpt,
            score=round(candidate.raw / (candidate.raw + SCORE_SATURATION), 6),
            category=candidate.category,
            source_date=candidate.source_date,
            content_hash=candidate.content_hash,
            retrieved_at=now_iso,
        )

    # ------------------------------------------------------------------ #
    # 分词
    # ------------------------------------------------------------------ #
    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """分词：中文按 bigram 切分提升召回（B066），英文/数字保留 len>=2。

        中文单字与停用词（中英文均含）被过滤，避免虚词主导排序。
        """
        if not isinstance(text, str) or not text:
            return []
        tokens: list[str] = []
        for raw in _WORD_RE.findall(text.lower()):
            for part in _PART_RE.findall(raw):
                if _CJK_RE.fullmatch(part):
                    if len(part) == 1:
                        continue
                    for index in range(len(part) - 1):
                        token = part[index : index + 2]
                        if token not in STOP_WORDS:
                            tokens.append(token)
                elif len(part) >= 2 and part not in STOP_WORDS:
                    tokens.append(part)
        return tokens


def _unique_terms(text: str | None, limit: int) -> list[str]:
    """去重并截断词项，保持出现顺序以保证打分可复现。"""
    return list(dict.fromkeys(RAGRetriever._tokenize(text or "")))[:limit]


def _normalize_role(role_code: str | None) -> str:
    """兼容枚举/字符串两种角色表示。"""
    value = getattr(role_code, "value", role_code)
    return str(value or "").strip().lower()


def _as_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _as_optional_text(value: Any) -> str | None:
    text = _as_text(value)
    return text or None


def _title_from_source(meta: dict[str, Any]) -> str:
    """metadata 缺 title 时用来源文件名（去扩展名）兜底。"""
    source = _as_text(meta.get("source")) or _as_text(meta.get("path"))
    if not source:
        return ""
    name = source.rsplit("/", 1)[-1]
    stem = Path(name).stem
    return stem[:300]


def _source_uri(meta: dict[str, Any], index: int) -> str:
    """解析可追溯来源：显式 URI 优先，否则拼 ``kb://<category>/<file>``。"""
    explicit = _as_text(meta.get("source_uri"))
    if explicit:
        return explicit[:1024]
    source = _as_text(meta.get("source")) or _as_text(meta.get("path"))
    if not source:
        return f"kb://unknown/{index}"
    if "://" in source:
        return source[:1024]
    source = source.lstrip("./")
    category = _as_text(meta.get("category"))
    if category:
        return f"kb://{category}/{source}"[:1024]
    return f"kb://{source}"[:1024]


def _content_hash(doc: str, meta: dict[str, Any]) -> str:
    """优先采用索引声明的 sha256，否则对文档内容现算。"""
    declared = _as_text(meta.get("content_hash"))
    if declared and _SHA256_RE.fullmatch(declared):
        return declared
    return hashlib.sha256(doc.encode("utf-8")).hexdigest()


def _evidence_id(source_uri: str, heading: str) -> str:
    """稳定证据 ID：``sha256(source_uri + \\0 + heading)[:32]``。"""
    digest = hashlib.sha256(f"{source_uri}\x00{heading}".encode("utf-8")).hexdigest()
    return digest[:32]


def _score_document(
    *,
    doc_lower: str,
    heading_lower: str,
    title_lower: str,
    category_lower: str,
    query_terms: list[str],
    context_terms: list[str],
) -> tuple[float, float]:
    """关键词加权打分，返回 ``(查询相关分, 上下文弱相关分)``。

    查询相关分 = 正文词频（次线性）+ 标题/小节标题/类别命中；
    上下文分只在候选已与查询相关时计入，避免产品上下文单独制造命中。
    """
    query_raw = 0.0
    for term in query_terms:
        count = doc_lower.count(term)
        if count:
            query_raw += WEIGHT_BODY * (1.0 + math.log(count))
        if term in heading_lower:
            query_raw += WEIGHT_HEADING
        if term in title_lower:
            query_raw += WEIGHT_TITLE
        if term in category_lower:
            query_raw += WEIGHT_CATEGORY

    context_raw = 0.0
    for term in context_terms:
        count = doc_lower.count(term)
        if count:
            context_raw += WEIGHT_CONTEXT_BODY * (1.0 + math.log(count))
        elif term in heading_lower:
            context_raw += WEIGHT_CONTEXT_HEADING
    return query_raw, context_raw


def _coerce_index_version(data: dict[str, Any]) -> int:
    """读取索引版本，非法值回退到当前 schema 版本。"""
    for key in ("index_version", "version", "schema_version"):
        value = data.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return INDEX_SCHEMA_VERSION


# 中英文停用词；中文按 bigram 粒度过滤。
STOP_WORDS: set[str] = {
    "the", "is", "in", "of", "to", "and", "a", "an", "it", "for", "on", "with",
    "as", "at", "by", "or", "be", "this", "that", "from", "are", "we", "you",
    "的", "是", "在", "和", "了", "有", "我", "不", "人", "这", "中", "大",
    "就", "也", "都", "要", "会", "可以", "一个", "没有", "他们", "我们",
    "什么", "自己", "怎么", "如果", "因为", "所以", "但是", "然后", "这个",
    "那个", "以及", "或者", "并且", "对于", "关于", "由于", "虽然", "不过",
    "因此", "其实", "已经", "还是", "这些", "那些", "不是", "就是",
    "时候", "目前", "如何", "为什么",
}


rag_retriever = RAGRetriever()


def search(
    query: str,
    *,
    role_code: str | None = None,
    product_context: str | None = None,
    limit: int = 4,
) -> list[RetrievalHit]:
    """模块级便捷入口，等价于 ``rag_retriever.search(...)``。

    供 ContextBuilder / 工作流直接 ``from rag.retriever import search`` 调用，
    无需自行管理检索器实例；索引用进程内单例，线程安全惰性加载。
    """
    return rag_retriever.search(
        query,
        role_code=role_code,
        product_context=product_context,
        limit=limit,
    )


__all__ = [
    "INDEX_PATH",
    "INDEX_SCHEMA_VERSION",
    "RAGRetriever",
    "ROLE_CATEGORY_PREFERENCE",
    "RetrievalHit",
    "STOP_WORDS",
    "rag_retriever",
    "search",
]
