"""Pipeline 节点函数。

每个节点是一个 async generator，接收 state，实时 yield SSE 事件，并返回状态更新。
节点内部使用 llm_stream/llm_complete 调用 LLM。
"""
import json
from typing import AsyncGenerator, Dict, Any
from openai import APIError

from core.llm_client import llm_stream, llm_complete
from core.role_prompts import (
    build_pm_prompt,
    build_cot_prompt,
    build_system_prompt,
    COACH_PROMPT,
)


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _llm_config(state: Dict[str, Any]) -> dict:
    """从 state 提取 LLM 配置。"""
    return {
        "api_key": state.get("user_api_key", "") or None,
        "base_url": state.get("user_base_url", "") or None,
        "model": state.get("user_model", "") or None,
    }


async def _stream_role(
    role: str,
    role_name: str,
    messages: list,
    state: Dict[str, Any],
    color: str = None,
) -> AsyncGenerator[str, None]:
    """通用流式角色节点：发送 role_start → tokens → role_done，返回完整文本。"""
    yield _sse({"type": "node_start", "node": role, "role_name": role_name})

    full_response = ""
    total_tokens = 0
    cfg = _llm_config(state)
    try:
        async for token, token_count in llm_stream(messages, **cfg):
            if token:
                full_response += token
                yield _sse({"type": "token", "node": role, "role": role, "token": token})
            if token_count > 0:
                total_tokens = token_count
    except ValueError as e:
        yield _sse({"type": "error", "node": role, "message": str(e)})
        return
    except APIError as e:
        msg = e.message if hasattr(e, "message") else str(e)
        yield _sse({"type": "error", "node": role, "message": f"AI 服务请求失败：{msg}"})
        return
    except Exception as e:
        yield _sse({"type": "error", "node": role, "message": f"AI 服务异常：{str(e)}"})
        return

    yield _sse({
        "type": "node_done",
        "node": role,
        "role_name": role_name,
        "output": full_response,
        "tokens": total_tokens,
    })


async def node_pm_prd(state: Dict[str, Any]) -> AsyncGenerator[str, None]:
    """PM 撰写 PRD 文档。"""
    problem = state.get("problem_statement", "")
    system_prompt = build_pm_prompt(task="prd", context=problem)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"请基于以下产品想法撰写完整的 PRD 文档：\n\n{problem}"},
    ]
    async for event in _stream_role("pm_prd", "产品经理", messages, state):
        yield event


async def node_cot(state: Dict[str, Any]) -> AsyncGenerator[str, None]:
    """CoT 思维链分析。"""
    problem = state.get("problem_statement", "")
    prd = state.get("prd", "")
    context = f"PRD 文档：\n{prd}" if prd else ""
    system_prompt = build_cot_prompt(problem, context)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"请对以下产品问题进行四步思维链分析：\n\n{problem}"},
    ]
    async for event in _stream_role("cot", "思维链引擎", messages, state):
        yield event


async def node_coach(state: Dict[str, Any]) -> AsyncGenerator[str, None]:
    """产品教练建议。"""
    problem = state.get("problem_statement", "")
    cot = state.get("cot_analysis", "")
    from core.role_prompts import TONE_PREAMBLE, COACH_PROMPT
    system_prompt = TONE_PREAMBLE + "\n\n" + COACH_PROMPT
    user_content = f"产品想法：{problem}\n\nCoT 分析：\n{cot}\n\n请基于以上分析，给出关键建议。"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    async for event in _stream_role("coach", "产品教练", messages, state):
        yield event


async def node_cto(state: Dict[str, Any]) -> AsyncGenerator[str, None]:
    """CTO 技术方案。"""
    problem = state.get("problem_statement", "")
    prd = state.get("prd", "")
    cot = state.get("cot_analysis", "")
    system_prompt = build_system_prompt("cto")
    user_content = f"产品想法：{problem}\n\nPRD 摘要：\n{prd[:2000]}\n\nCoT 分析：\n{cot[:1500]}\n\n请从技术角度评估并给出方案。"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    async for event in _stream_role("cto", "技术负责人", messages, state):
        yield event


async def node_designer(state: Dict[str, Any]) -> AsyncGenerator[str, None]:
    """设计师方案。"""
    problem = state.get("problem_statement", "")
    prd = state.get("prd", "")
    cto_solution = state.get("cto_solution", "")
    system_prompt = build_system_prompt("designer")
    user_content = f"产品想法：{problem}\n\nPRD 摘要：\n{prd[:2000]}\n\nCTO 方案：\n{cto_solution[:1500]}\n\n请从设计角度评估并给出方案。"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    async for event in _stream_role("designer", "设计师", messages, state):
        yield event


async def node_ops(state: Dict[str, Any]) -> AsyncGenerator[str, None]:
    """运营方案。"""
    problem = state.get("problem_statement", "")
    prd = state.get("prd", "")
    cto_solution = state.get("cto_solution", "")
    designer_solution = state.get("designer_solution", "")
    system_prompt = build_system_prompt("ops")
    user_content = f"产品想法：{problem}\n\nPRD 摘要：\n{prd[:2000]}\n\nCTO 方案：\n{cto_solution[:1000]}\n\n设计师方案：\n{designer_solution[:1000]}\n\n请从运营/市场角度评估并给出方案。"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    async for event in _stream_role("ops", "运营负责人", messages, state):
        yield event


async def node_user_feedback(state: Dict[str, Any]) -> AsyncGenerator[str, None]:
    """用户 Agent 反馈。"""
    problem = state.get("problem_statement", "")
    prd = state.get("prd", "")
    system_prompt = build_system_prompt("user")
    user_content = f"产品想法：{problem}\n\nPRD 摘要：\n{prd[:2000]}\n\n作为目标用户，你会用这个产品吗？说出你的真实想法。"
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    async for event in _stream_role("user_feedback", "目标用户", messages, state):
        yield event


async def node_canvas_synthesis(state: Dict[str, Any]) -> AsyncGenerator[str, None]:
    """综合画布：汇总各角色产出，构建结构化画布。"""
    yield _sse({"type": "node_start", "node": "canvas_synthesis", "role_name": "画布综合"})

    all_outputs = {
        "prd": state.get("prd", "")[:1500],
        "cot": state.get("cot_analysis", "")[:1000],
        "coach": state.get("coach_advice", "")[:1000],
        "cto": state.get("cto_solution", "")[:1000],
        "designer": state.get("designer_solution", "")[:1000],
        "ops": state.get("ops_solution", "")[:1000],
        "user": state.get("user_feedback", "")[:800],
    }

    system_prompt = """你是画布综合引擎。将各角色产出综合为结构化画布 JSON。
输出严格 JSON 格式：
{
  "consensus": ["各角色达成共识的点1", "共识点2"],
  "disagreements": [{"topic": "争议话题", "positions": {"cto": "观点", "designer": "观点"}}],
  "summary": "2-3句话总结",
  "key_risks": ["风险1", "风险2"],
  "next_steps": ["下一步1", "下一步2"]
}"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"各角色产出：\n{json.dumps(all_outputs, ensure_ascii=False, indent=2)}"},
    ]

    cfg = _llm_config(state)
    try:
        content, total_tokens = await llm_complete(messages, temperature=0.3, **cfg)
    except Exception as e:
        yield _sse({"type": "error", "node": "canvas_synthesis", "message": f"画布综合失败：{str(e)}"})
        return

    # 尝试解析 JSON
    canvas_tree = {"summary": content, "consensus": [], "disagreements": [], "key_risks": [], "next_steps": []}
    try:
        # 提取 JSON 块
        json_start = content.find("{")
        json_end = content.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            canvas_tree = json.loads(content[json_start:json_end])
    except (json.JSONDecodeError, ValueError):
        pass

    yield _sse({
        "type": "node_done",
        "node": "canvas_synthesis",
        "role_name": "画布综合",
        "output": content,
        "canvas_tree": canvas_tree,
        "tokens": total_tokens,
    })


async def node_portrait(state: Dict[str, Any]) -> AsyncGenerator[str, None]:
    """产品画像生成。"""
    yield _sse({"type": "node_start", "node": "portrait", "role_name": "产品画像"})

    prd = state.get("prd", "")
    canvas = state.get("canvas_tree", {})

    system_prompt = """你是产品画像生成引擎。基于 PRD 和画布，生成产品画像 JSON。
输出严格 JSON 格式：
{
  "name": "产品名称",
  "tagline": "一句话标语",
  "target_users": ["目标用户1", "目标用户2"],
  "core_features": {"must_have": ["功能1"], "nice_to_have": ["功能2"]},
  "style_keywords": ["风格词1", "风格词2"],
  "color_scheme": {"primary": "#hex", "secondary": "#hex"},
  "interaction_style": "交互风格描述"
}"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"PRD：\n{prd[:2000]}\n\n画布：\n{json.dumps(canvas, ensure_ascii=False)[:1000]}"},
    ]

    cfg = _llm_config(state)
    try:
        content, total_tokens = await llm_complete(messages, temperature=0.4, **cfg)
    except Exception as e:
        yield _sse({"type": "error", "node": "portrait", "message": f"画像生成失败：{str(e)}"})
        return

    portrait = {"name": "未命名产品", "tagline": "", "core_features": {}}
    try:
        json_start = content.find("{")
        json_end = content.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            portrait = json.loads(content[json_start:json_end])
    except (json.JSONDecodeError, ValueError):
        pass

    yield _sse({
        "type": "node_done",
        "node": "portrait",
        "role_name": "产品画像",
        "output": content,
        "portrait": portrait,
        "tokens": total_tokens,
    })


async def node_pm_acceptance(state: Dict[str, Any]) -> AsyncGenerator[str, None]:
    """PM 验收：对照 PRD 验收各角色产出。"""
    yield _sse({"type": "node_start", "node": "pm_acceptance", "role_name": "PM验收"})

    prd = state.get("prd", "")
    outputs = {
        "cot": state.get("cot_analysis", "")[:800],
        "coach": state.get("coach_advice", "")[:800],
        "cto": state.get("cto_solution", "")[:800],
        "designer": state.get("designer_solution", "")[:800],
        "ops": state.get("ops_solution", "")[:800],
        "user": state.get("user_feedback", "")[:800],
    }

    system_prompt = build_pm_prompt(task="acceptance", context={"prd": prd, "outputs": json.dumps(outputs, ensure_ascii=False)})
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "请对照 PRD 验收以上各角色产出，输出严格 JSON 格式的验收结果。"},
    ]

    cfg = _llm_config(state)
    try:
        content, total_tokens = await llm_complete(messages, temperature=0.2, **cfg)
    except Exception as e:
        yield _sse({"type": "error", "node": "pm_acceptance", "message": f"验收失败：{str(e)}"})
        return

    # 解析验收结果
    acceptance_result = {"passed": True, "gaps": [], "suggestions": [], "summary": content[:200]}
    try:
        json_start = content.find("{")
        json_end = content.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            acceptance_result = json.loads(content[json_start:json_end])
    except (json.JSONDecodeError, ValueError):
        pass

    yield _sse({
        "type": "node_done",
        "node": "pm_acceptance",
        "role_name": "PM验收",
        "output": content,
        "acceptance_result": acceptance_result,
        "tokens": total_tokens,
    })
