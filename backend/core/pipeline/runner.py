"""Pipeline 执行器。

手动按拓扑顺序执行节点，实现 token 级流式 SSE 输出。
支持 PM 验收闭环：验收不通过且修订次数 < 2 时，从 cot 节点重新执行。
"""
import json
from typing import AsyncGenerator, Dict, Any

from core.pipeline.nodes import (
    node_pm_prd,
    node_cot,
    node_coach,
    node_cto,
    node_designer,
    node_ops,
    node_user_feedback,
    node_canvas_synthesis,
    node_portrait,
    node_pm_acceptance,
)
from core.pipeline.graph import PIPELINE_NODES, REVISION_ENTRY, MAX_REVISIONS

# 节点函数映射
NODE_FUNCTIONS = {
    "pm_prd": node_pm_prd,
    "cot": node_cot,
    "coach": node_coach,
    "cto": node_cto,
    "designer": node_designer,
    "ops": node_ops,
    "user_feedback": node_user_feedback,
    "canvas_synthesis": node_canvas_synthesis,
    "portrait": node_portrait,
    "pm_acceptance": node_pm_acceptance,
}

# 节点产出字段映射（节点 done 后将 output 存入 state 的哪个字段）
NODE_OUTPUT_FIELD = {
    "pm_prd": "prd",
    "cot": "cot_analysis",
    "coach": "coach_advice",
    "cto": "cto_solution",
    "designer": "designer_solution",
    "ops": "ops_solution",
    "user_feedback": "user_feedback",
    "canvas_synthesis": "canvas_tree",
    "portrait": "product_portrait",
    "pm_acceptance": "acceptance_result",
}


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def run_pipeline(
    session_id: str,
    problem_statement: str,
    api_key: str = "",
    base_url: str = "",
    model: str = "",
) -> AsyncGenerator[str, None]:
    """执行 Pipeline，yield SSE 事件。

    流程：
    1. pm_prd → cot → coach → cto → designer → ops → user_feedback → canvas_synthesis → portrait → pm_acceptance
    2. 若 pm_acceptance 验收不通过且修订次数 < 2，从 cot 重新执行（修订循环）
    3. 最多修订 2 次（共 3 轮）
    """
    state: Dict[str, Any] = {
        "session_id": session_id,
        "problem_statement": problem_statement,
        "user_api_key": api_key,
        "user_base_url": base_url,
        "user_model": model,
        "revision_count": 0,
        "messages": [],
    }

    yield _sse({"type": "pipeline_start", "session_id": session_id})

    total_tokens = 0
    error_occurred = False

    # 第一轮：执行所有节点
    for node_name in PIPELINE_NODES:
        if error_occurred:
            break
        async for event in _execute_node(node_name, state):
            yield event
            # 累加 tokens
            if "node_done" in event:
                try:
                    match = json.loads(event.replace("data: ", "").strip())
                    total_tokens += match.get("tokens", 0)
                except (json.JSONDecodeError, ValueError):
                    pass
            if '"type": "error"' in event and '"node":' in event:
                error_occurred = True

    # 修订循环
    while not error_occurred and state.get("revision_count", 0) < MAX_REVISIONS:
        acceptance = state.get("acceptance_result", {})
        if acceptance.get("passed", True):
            break

        state["revision_count"] = state.get("revision_count", 0) + 1
        revision_num = state["revision_count"]

        yield _sse({
            "type": "revision_start",
            "revision_count": revision_num,
            "gaps": acceptance.get("gaps", []),
            "suggestions": acceptance.get("suggestions", []),
        })

        # 从 cot 节点重新执行到 pm_acceptance
        revision_nodes = PIPELINE_NODES[PIPELINE_NODES.index(REVISION_ENTRY):]
        for node_name in revision_nodes:
            if error_occurred:
                break
            async for event in _execute_node(node_name, state):
                yield event
                if '"type": "error"' in event and '"node":' in event:
                    error_occurred = True

    # 最终结果
    yield _sse({
        "type": "pipeline_done",
        "session_id": session_id,
        "prd": state.get("prd", ""),
        "canvas_tree": state.get("canvas_tree", {}),
        "product_portrait": state.get("product_portrait", {}),
        "acceptance_result": state.get("acceptance_result", {}),
        "revision_count": state.get("revision_count", 0),
        "total_tokens": total_tokens,
    })

    if total_tokens > 0 and not api_key:
        yield _sse({"type": "quota_deduct", "tokens": total_tokens})

    yield _sse({"type": "done"})


async def _execute_node(node_name: str, state: Dict[str, Any]) -> AsyncGenerator[str, None]:
    """执行单个节点，更新 state，yield SSE 事件。"""
    func = NODE_FUNCTIONS.get(node_name)
    if func is None:
        yield _sse({"type": "error", "node": node_name, "message": f"未知节点: {node_name}"})
        return

    output_text = ""
    async for event in func(state):
        yield event
        # 从 node_done 事件中提取产出，更新 state
        if "node_done" in event:
            try:
                data = json.loads(event.replace("data: ", "").strip())
                output_text = data.get("output", "")
                # 特殊节点：更新结构化字段
                if node_name == "canvas_synthesis" and "canvas_tree" in data:
                    state["canvas_tree"] = data["canvas_tree"]
                elif node_name == "portrait" and "portrait" in data:
                    state["product_portrait"] = data["portrait"]
                elif node_name == "pm_acceptance" and "acceptance_result" in data:
                    state["acceptance_result"] = data["acceptance_result"]
            except (json.JSONDecodeError, ValueError):
                pass

    # 更新 state 的产出字段
    field = NODE_OUTPUT_FIELD.get(node_name)
    if field and output_text:
        state[field] = output_text
