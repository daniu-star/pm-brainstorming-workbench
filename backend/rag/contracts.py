"""RAG 检索契约（PRD FR-04A）。

检索结果是结构化命中而不是裸文本：每条命中携带可解析来源、语义片段、
稳定证据 ID 与内容哈希，供 ContextBuilder 注入、画布引用与报告溯源复用。
本模块只定义数据模型，不做任何 IO。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

#: 语义片段上限（字符）。检索返回完整语义切片，不固定截取前 300 字。
MAX_EXCERPT_CHARS = 1200

#: 证据 ID：稳定、可复现的 32 位小写十六进制。
EVIDENCE_ID_PATTERN = r"^[0-9a-f]{32}$"

#: 文档内容哈希：sha256 十六进制。
CONTENT_HASH_PATTERN = r"^[0-9a-f]{64}$"

#: UTC ISO-8601 秒级时间戳（``2026-11-18T03:00:00Z``）。
UTC_ISO_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"


class RetrievalHit(BaseModel):
    """单条结构化检索命中（FR-04A）。

    字段约束：
    * ``evidence_id`` 由 ``source_uri + heading`` 确定性派生，可跨进程复现；
    * ``excerpt`` 不超过 :data:`MAX_EXCERPT_CHARS` 字符；
    * ``score`` 归一化到 ``[0, 1]``，同一次结果内按降序排列；
    * ``content_hash`` 为文档内容 sha256，用于检测索引/来源漂移；
    * ``retrieved_at`` 为检索时刻的 UTC ISO 时间戳。

    模型禁止额外字段，避免上游把检索结果当自由字典使用。
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str = Field(min_length=32, max_length=32, pattern=EVIDENCE_ID_PATTERN)
    source_uri: str = Field(min_length=1, max_length=1024)
    title: str = Field(default="", max_length=300)
    heading: str = Field(default="", max_length=300)
    excerpt: str = Field(min_length=1, max_length=MAX_EXCERPT_CHARS)
    score: float = Field(ge=0.0, le=1.0)
    category: str = Field(default="", max_length=64)
    source_date: str | None = Field(default=None, max_length=64)
    content_hash: str = Field(min_length=64, max_length=64, pattern=CONTENT_HASH_PATTERN)
    retrieved_at: str = Field(min_length=20, max_length=20, pattern=UTC_ISO_PATTERN)


__all__ = [
    "CONTENT_HASH_PATTERN",
    "EVIDENCE_ID_PATTERN",
    "MAX_EXCERPT_CHARS",
    "RetrievalHit",
    "UTC_ISO_PATTERN",
]
