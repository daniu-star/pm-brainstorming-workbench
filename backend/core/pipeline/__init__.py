"""LangGraph Pipeline for PM Brainstorm Workbench.

Pipeline 架构：
    pm_prd → cot → coach → cto → designer → ops → user_feedback → canvas_synthesis → portrait → pm_acceptance
                                                                                                    ↓
                                                                                            (条件边)
                                                                                            passed → END
                                                                                            failed & revision<2 → cot (修订循环)
"""
