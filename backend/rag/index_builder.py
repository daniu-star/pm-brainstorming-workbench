"""RAG 知识库索引构建器。

递归扫描 backend/rag/knowledge/**/*.md，按二级标题（##）切片，
写入 backend/data/knowledge_base.json（B004）。
所有路径基于 __file__ 相对解析，不依赖启动 cwd。
"""
import json
import logging
import os
import re
import tempfile
from pathlib import Path

from rag.retriever import INDEX_PATH

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[1]
KNOWLEDGE_DIR = BACKEND_DIR / "rag" / "knowledge"

# 仅匹配二级标题（## xxx），不匹配三级及以上（###）。
_HEADING_RE = re.compile(r"^##(?!#)\s+(.+)$")


def _split_by_heading(text: str, fallback_heading: str) -> list[tuple[str, str]]:
    """按二级标题切片，返回 (heading, content) 列表。

    首个标题之前的内容归属 fallback_heading（通常为文件标题）。
    """
    sections: list[tuple[str, str]] = []
    current_heading = fallback_heading
    lines: list[str] = []
    for line in text.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            if lines:
                sections.append((current_heading, "\n".join(lines).strip()))
            current_heading = m.group(1).strip()
            lines = []
        else:
            lines.append(line)
    if lines:
        sections.append((current_heading, "\n".join(lines).strip()))
    return sections


def iter_knowledge_documents() -> tuple[list[str], list[dict]]:
    """扫描 knowledge 目录，返回 (documents, metadatas)。"""
    documents: list[str] = []
    metadatas: list[dict] = []
    if not KNOWLEDGE_DIR.exists():
        logger.warning("知识库目录不存在：%s", KNOWLEDGE_DIR)
        return documents, metadatas

    for md_path in sorted(KNOWLEDGE_DIR.rglob("*.md")):
        try:
            text = md_path.read_text(encoding="utf-8")
        except OSError:
            logger.warning("知识库文档读取失败，已跳过：%s", md_path.name)
            continue

        rel = md_path.relative_to(KNOWLEDGE_DIR)
        category = rel.parts[0] if len(rel.parts) > 1 else ""
        title_match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        title = title_match.group(1).strip() if title_match else md_path.stem

        for heading, content in _split_by_heading(text, title):
            if not content or len(content) < 20:
                continue
            documents.append(content)
            metadatas.append({
                "source": md_path.name,
                "heading": heading,
                "category": category,
            })
    return documents, metadatas


def build_index() -> int:
    """重建索引文件（原子写），返回切片数量。"""
    documents, metadatas = iter_knowledge_documents()
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(INDEX_PATH.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"documents": documents, "metadatas": metadatas}, f, ensure_ascii=False)
        os.replace(tmp_name, INDEX_PATH)
    except OSError:
        logger.exception("RAG 索引写入失败")
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise
    logger.info("RAG 知识库索引已构建：%d 个切片", len(documents))
    return len(documents)


def build_if_stale() -> int:
    """索引文件不存在或比 knowledge 目录旧时重建；返回本次新建的切片数（未重建返回 0）。"""
    if not INDEX_PATH.exists():
        return build_index()
    try:
        index_mtime = INDEX_PATH.stat().st_mtime
        newest = max((p.stat().st_mtime for p in KNOWLEDGE_DIR.rglob("*.md")), default=0.0)
    except OSError:
        logger.warning("RAG 索引状态检查失败，触发重建")
        return build_index()
    if newest > index_mtime:
        return build_index()
    return 0
