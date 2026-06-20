"""Pipeline 图结构定义。

使用 LangGraph StateGraph 定义节点和边。由于 LangGraph 的 compile().astream() 不支持
token 级流式，runner.py 会手动按拓扑顺序执行节点。图结构主要用于：
1. 明确定义节点执行顺序
2. 定义条件边（PM 验收闭环）
3. 作为可验证的架构文档
"""
from langgraph.graph import StateGraph, END
from core.pipeline.state import PipelineState
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

# 节点执行顺序（线性部分）
PIPELINE_NODES = [
    "pm_prd",
    "cot",
    "coach",
    "cto",
    "designer",
    "ops",
    "user_feedback",
    "canvas_synthesis",
    "portrait",
    "pm_acceptance",
]

# 修订循环时跳过的节点（从 cot 重新开始）
REVISION_ENTRY = "cot"

MAX_REVISIONS = 2


def build_graph():
    """构建 LangGraph 图结构。

    注意：此图用于结构定义和验证。实际执行由 runner.py 手动驱动以支持 token 级流式。
    """
    graph_builder = StateGraph(PipelineState)

    graph_builder.add_node("pm_prd", node_pm_prd)
    graph_builder.add_node("cot", node_cot)
    graph_builder.add_node("coach", node_coach)
    graph_builder.add_node("cto", node_cto)
    graph_builder.add_node("designer", node_designer)
    graph_builder.add_node("ops", node_ops)
    graph_builder.add_node("user_feedback", node_user_feedback)
    graph_builder.add_node("canvas_synthesis", node_canvas_synthesis)
    graph_builder.add_node("portrait", node_portrait)
    graph_builder.add_node("pm_acceptance", node_pm_acceptance)

    graph_builder.set_entry_point("pm_prd")
    graph_builder.add_edge("pm_prd", "cot")
    graph_builder.add_edge("cot", "coach")
    graph_builder.add_edge("coach", "cto")
    graph_builder.add_edge("cto", "designer")
    graph_builder.add_edge("designer", "ops")
    graph_builder.add_edge("ops", "user_feedback")
    graph_builder.add_edge("user_feedback", "canvas_synthesis")
    graph_builder.add_edge("canvas_synthesis", "portrait")
    graph_builder.add_edge("portrait", "pm_acceptance")

    def acceptance_router(state):
        acceptance = state.get("acceptance_result", {})
        passed = acceptance.get("passed", True)
        revision_count = state.get("revision_count", 0)
        if not passed and revision_count < MAX_REVISIONS:
            return "revise"
        return "end"

    graph_builder.add_conditional_edges("pm_acceptance", acceptance_router, {
        "revise": "cot",
        "end": END,
    })

    return graph_builder.compile()
