"""Canvas 功能树解析：从脑暴对话中提取结构化 JSON 树。"""
import json
import logging

from core.llm_client import llm_complete

logger = logging.getLogger(__name__)

CANVAS_PARSE_PROMPT = """你是结构化数据提取器。给定一段产品头脑风暴对话，用 JSON 格式将想法组织为层级化功能树。

输出格式（严格 JSON，不要 markdown，不要解释）：
{
  "root": "问题陈述总结（一句话）",
  "branches": [
    {
      "name": "功能领域 / 主题名称",
      "children": [
        {
          "name": "具体想法或洞察",
          "source_role": "cto" | "designer" | "ops" | "user",
          "type": "feature" | "risk" | "question" | "insight",
          "source_text": "对话中的原文简短引用"
        }
      ]
    }
  ]
}

规则：
- 将相关的想法归入同一分支下
- 每个叶子节点必须有 source_role 和 type 字段
- 同时包含功能建议和已识别的风险/问题
- 最多 6 个分支，每个分支最多 8 个子节点
- 命名分支和叶子时保持简洁
- source_text 应为简短引用（50 字以内）
- root 和 name 字段必须使用中文输出"""


def _extract_json(text: str) -> dict | None:
    """从 LLM 输出中提取 JSON 对象；提取或解析失败返回 None（B077）。"""
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()
    try:
        tree = json.loads(text)
    except (json.JSONDecodeError, IndexError):
        return None
    if not isinstance(tree, dict):
        return None
    return tree


async def parse_conversation_to_tree(messages: list[dict]) -> dict | None:
    """全量解析对话为功能树；解析失败返回 None（不再返回错误占位，B036）。"""
    conversation_text = _format_messages(messages)

    user_prompt = f"以下是头脑风暴对话：\n\n{conversation_text}\n\n提取结构化的功能树。"

    messages_for_llm = [
        {"role": "system", "content": CANVAS_PARSE_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    result = await llm_complete(messages_for_llm, temperature=0.2)
    tree = _extract_json(result)
    if tree is None:
        logger.warning("Canvas 全量解析失败：LLM 输出不是合法 JSON")
    return tree


async def parse_incremental(messages: list[dict], existing_tree: dict | None, last_idx: int = 0) -> dict | None:
    """增量解析：只把 last_idx 之后的新消息合并进已有功能树（B040）。

    无新消息时直接返回 existing_tree；解析失败返回 None。
    """
    if existing_tree is None:
        return await parse_conversation_to_tree(messages)

    new_messages = [m for m in messages[max(0, last_idx):]] if last_idx < len(messages) else []
    if not new_messages:
        return existing_tree

    conversation_text = _format_messages(new_messages)
    existing_json = json.dumps(existing_tree, ensure_ascii=False)

    user_prompt = (
        f"已有功能树：\n{existing_json}\n\n"
        f"需要合并的新对话消息：\n{conversation_text}\n\n"
        f"将新的想法合并到已有功能树中。根据需要添加新分支/子节点。"
        f"按相同 JSON 格式返回完整的更新后功能树。"
    )

    messages_for_llm = [
        {"role": "system", "content": CANVAS_PARSE_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    result = await llm_complete(messages_for_llm, temperature=0.2)
    tree = _extract_json(result)
    if tree is None:
        logger.warning("Canvas 增量解析失败：LLM 输出不是合法 JSON")
    return tree


def _format_messages(messages: list[dict]) -> str:
    lines = []
    for m in messages:
        role = m.get("role_name", m.get("role", "unknown"))
        content = m.get("content", "")
        lines.append(f"[{role}]: {content}")
    return "\n".join(lines)
