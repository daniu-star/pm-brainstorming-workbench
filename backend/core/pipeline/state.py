"""Pipeline 状态定义。

LangGraph 需要显式状态 schema 在节点间传递。
"""
from typing import TypedDict, Optional


class PipelineState(TypedDict, total=False):
    """Pipeline 执行状态。

    在节点间传递，每个节点读取所需字段并更新自己的产出字段。
    """
    # 输入
    session_id: str
    problem_statement: str
    user_api_key: str
    user_base_url: str
    user_model: str

    # 各节点产出
    prd: str                              # PM 写的 PRD 文档
    cot_analysis: str                     # CoT 思维链分析
    coach_advice: str                     # 产品教练建议
    cto_solution: str                     # CTO 技术方案
    designer_solution: str                # 设计师方案
    ops_solution: str                     # 运营方案
    user_feedback: str                    # 用户 Agent 反馈
    canvas_tree: dict                     # 综合画布
    product_portrait: dict                # 产品画像
    acceptance_result: dict               # PM 验收结果 {passed, gaps, suggestions, summary}

    # 控制
    revision_count: int                   # 修订次数（最多 2 次）
    messages: list                        # SSE 事件流累积
    error: Optional[str]                  # 错误信息
