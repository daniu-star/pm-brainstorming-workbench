import json
import uuid
from typing import List, Optional, Tuple
from core.llm_client import llm_complete

CANVAS_PARSE_PROMPT = """你是结构化数据提取器。给定一段产品头脑风暴对话，提取其中的共识、分歧和阶段性总结。

输出格式（严格 JSON，不要 markdown，不要解释）：
{
  "topic": "问题陈述一句话",
  "timeline": [
    {
      "id": "c1",
      "type": "consensus",
      "content": "一句话概述共识内容",
      "roles": ["cto", "designer"],
      "source_refs": ["消息ID"],
      "status": "draft"
    },
    {
      "id": "d1",
      "type": "disagreement",
      "content": "一句话概述分歧内容",
      "roles": ["cto", "designer"],
      "positions": [
        {"role": "cto", "stance": "立场简述"},
        {"role": "designer", "stance": "立场简述"}
      ]
    },
    {
      "id": "s1",
      "type": "summary",
      "content": "一句话概述阶段性成果",
      "roles": ["cto", "designer"]
    }
  ]
}

规则：
- consensus：记录用户与Agent达成共识的观点，用一句话概述
- disagreement：记录用户与Agent存在分歧的观点，包含各方立场
- summary：每一轮对话结束后的阶段性成果，用一句话概述
- positions 字段仅 disagreement 类型需要
- 不要记录所有话语，只提取关键共识、分歧和阶段性总结
- 最多 15 个节点
- content 和 stance 必须使用中文
- source_refs 必须引用输入中真实存在的消息 ID，禁止编造来源
- 新提取节点一律为 draft，只有用户操作才能变为 confirmed
- 严格 JSON 输出，不要 markdown，不要解释"""


def _enrich_decision_graph(map_data: dict, messages: List[dict]) -> dict:
    valid_sources = []
    for index, message in enumerate(messages):
        message_id = message.get("id") or f"legacy_{index}"
        valid_sources.append(
            {
                "id": message_id,
                "kind": "message",
                "role": message.get("role_name") or message.get("role", "unknown"),
                "excerpt": message.get("content", "")[:180],
                "timestamp": message.get("timestamp"),
                "round_id": message.get("round_id"),
            }
        )
    valid_ids = {source["id"] for source in valid_sources}
    timeline = map_data.get("timeline", [])
    for index, node in enumerate(timeline):
        node["id"] = node.get("id") or f"node_{uuid.uuid4().hex[:8]}"
        node["status"] = node.get("status") if node.get("status") in ("draft", "confirmed") else "draft"
        refs = [ref for ref in node.get("source_refs", []) if ref in valid_ids]
        if not refs and valid_sources:
            refs = [source["id"] for source in valid_sources[-2:]]
        node["source_refs"] = refs

    edges = []
    for index in range(1, len(timeline)):
        previous = timeline[index - 1]
        current = timeline[index]
        relation = "contradicts" if current.get("type") == "disagreement" else "refines"
        edges.append(
            {
                "id": f"edge_{index}",
                "from": previous["id"],
                "to": current["id"],
                "relation": relation,
            }
        )
    map_data["sources"] = valid_sources
    map_data["edges"] = edges
    return map_data


async def parse_conversation_to_map(messages: List[dict], api_key: str = "", base_url: str = "", model: str = "") -> Tuple[dict, int]:
    conversation_text = _format_messages(messages)

    user_prompt = f"以下是头脑风暴对话：\n\n{conversation_text}\n\n提取共识、分歧和阶段性总结。"

    messages_for_llm = [
        {"role": "system", "content": CANVAS_PARSE_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    result, tokens = await llm_complete(messages_for_llm, temperature=0.2, api_key=api_key or None, base_url=base_url or None, model=model or None)

    try:
        if "```json" in result:
            result = result.split("```json")[1].split("```")[0].strip()
        elif "```" in result:
            result = result.split("```")[1].split("```")[0].strip()

        map_data = json.loads(result)
        return _enrich_decision_graph(map_data, messages), tokens
    except (json.JSONDecodeError, IndexError):
        return {
            "topic": "解析错误 — 请重新生成",
            "timeline": [],
        }, tokens


async def parse_incremental_map(messages: List[dict], existing_map: Optional[dict], api_key: str = "", base_url: str = "", model: str = "") -> Tuple[Optional[dict], int]:
    if existing_map is None:
        map_data, tokens = await parse_conversation_to_map(messages, api_key=api_key, base_url=base_url, model=model)
        return map_data, tokens

    recent = messages[-4:] if len(messages) > 4 else messages
    conversation_text = _format_messages(recent)
    existing_json = json.dumps(existing_map, ensure_ascii=False)

    user_prompt = (
        f"已有共识/分歧/总结时间线：\n{existing_json}\n\n"
        f"需要合并的新对话消息：\n{conversation_text}\n\n"
        f"将新的共识、分歧和阶段性总结合并到已有时间线中，保持 timeline 顺序。"
        f"按相同 JSON 格式返回完整的更新后数据。"
    )

    messages_for_llm = [
        {"role": "system", "content": CANVAS_PARSE_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    result, tokens = await llm_complete(messages_for_llm, temperature=0.2, api_key=api_key or None, base_url=base_url or None, model=model or None)

    try:
        if "```json" in result:
            result = result.split("```json")[1].split("```")[0].strip()
        elif "```" in result:
            result = result.split("```")[1].split("```")[0].strip()

        map_data = json.loads(result)
        return _enrich_decision_graph(map_data, messages), tokens
    except (json.JSONDecodeError, IndexError):
        return None, tokens


def _format_messages(messages: List[dict]) -> str:
    lines = []
    for m in messages:
        role = m.get("role_name", m.get("role", "unknown"))
        message_id = m.get("id", "legacy")
        content = m.get("content", "")
        lines.append(f"[消息ID={message_id}][{role}]: {content}")
    return "\n".join(lines)
