import json
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
      "roles": ["cto", "designer"]
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
- 严格 JSON 输出，不要 markdown，不要解释"""


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
        return map_data, tokens
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
        return map_data, tokens
    except (json.JSONDecodeError, IndexError):
        return None, tokens


def _format_messages(messages: List[dict]) -> str:
    lines = []
    for m in messages:
        role = m.get("role_name", m.get("role", "unknown"))
        content = m.get("content", "")
        lines.append(f"[{role}]: {content}")
    return "\n".join(lines)
