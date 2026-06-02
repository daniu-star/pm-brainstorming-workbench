import json
from typing import List, Tuple
from core.llm_client import llm_complete
from db.session_store import session_store

PRODUCT_PORTRAIT_PROMPT = """你是资深产品经理，擅长从头脑风暴讨论中提炼产品画像。给定一段产品头脑风暴对话，生成一份结构化的产品画像。

输出格式（严格 JSON，不要 markdown，不要解释）：
{
  "product_name": "产品名称",
  "tagline": "一句话描述产品核心价值",
  "target_users": "目标用户群体描述",
  "core_features": [
    {
      "name": "功能名称",
      "description": "功能描述",
      "priority": "must-have"
    }
  ],
  "style_keywords": ["极简", "年轻化"],
  "color_scheme": {
    "primary": "#FF6B35",
    "secondary": "#F7C59F",
    "accent": "#2EC4B6",
    "background": "#FFFFFF"
  },
  "interaction_style": "卡片式浏览",
  "wireframe_description": "页面布局描述：\\n[header] 顶部导航栏，左侧Logo，右侧用户头像\\n[nav] 水平标签导航：首页 | 发现 | 我的\\n[content] 主内容区：卡片网格布局，每行2-3张卡片\\n[sidebar] 右侧边栏：筛选器和推荐\\n[footer] 底部：版权信息"
}

规则：
- product_name：简洁有力，2-6个字
- tagline：突出核心价值主张，不超过20字
- target_users：明确用户画像，包含场景
- core_features：3-6个核心功能，priority 只能是 "must-have" 或 "nice-to-have"
- style_keywords：2-5个风格关键词
- color_scheme：4个十六进制颜色值，primary 为主色，secondary 为辅色，accent 为强调色，background 为背景色
- interaction_style：描述主要交互方式
- wireframe_description：用 [header] [nav] [content] [sidebar] [footer] 标记各区域，每区域后描述其内容和布局
- 所有文本内容必须使用中文
- 严格 JSON 输出，不要 markdown，不要解释"""


def _format_messages(messages: List[dict]) -> str:
    lines = []
    for m in messages:
        role = m.get("role_name", m.get("role", "unknown"))
        content = m.get("content", "")
        lines.append(f"[{role}]: {content}")
    return "\n".join(lines)


async def generate_product_portrait(session_id: str, api_key: str = "", base_url: str = "", model: str = "") -> Tuple[dict, int]:
    session = session_store.get(session_id)
    if session is None:
        raise ValueError(f"会话 {session_id} 未找到")

    messages = session.get("messages", [])
    if not messages:
        raise ValueError("没有消息可供生成产品画像")

    conversation_text = _format_messages(messages)

    user_prompt = f"以下是产品头脑风暴对话：\n\n{conversation_text}\n\n请根据以上讨论，生成产品画像。"

    messages_for_llm = [
        {"role": "system", "content": PRODUCT_PORTRAIT_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    result, tokens = await llm_complete(
        messages_for_llm,
        temperature=0.3,
        api_key=api_key or None,
        base_url=base_url or None,
        model=model or None,
    )

    try:
        if "```json" in result:
            result = result.split("```json")[1].split("```")[0].strip()
        elif "```" in result:
            result = result.split("```")[1].split("```")[0].strip()

        portrait = json.loads(result)
        return portrait, tokens
    except (json.JSONDecodeError, IndexError):
        return {
            "product_name": "解析错误",
            "tagline": "请重新生成",
            "target_users": "",
            "core_features": [],
            "style_keywords": [],
            "color_scheme": {"primary": "#FF6B35", "secondary": "#F7C59F", "accent": "#2EC4B6", "background": "#FFFFFF"},
            "interaction_style": "",
            "wireframe_description": "",
        }, tokens
