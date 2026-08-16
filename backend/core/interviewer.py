"""AI 面试官：六维审计压力测试面试。"""
import asyncio
import json
import logging

from core.llm_client import LLMError, llm_stream
from core.role_prompts import build_interviewer_prompt
from core.sse import sse_event
from db.session_store import session_store

logger = logging.getLogger(__name__)

# 六个审计维度 key（前后端契约，business_loop 为商业闭环维度）。
INTERVIEW_DIMENSIONS = [
    "problem_validity",
    "solution_effectiveness",
    "technical_risk",
    "business_loop",
    "user_adoption",
    "execution_risk",
]

# 问题数兜底上限：达到后强制结束面试（B037）。
MAX_INTERVIEW_QUESTIONS = 18

INTERRUPT_MARK = "[生成中断]"


def _dimensions_update_event(covered: list[str]):
    return sse_event("dimensions_update", covered=covered, total=len(INTERVIEW_DIMENSIONS))


async def run_interview_start(session_id: str):
    """开始面试 — AI 提出第一个问题。"""
    session = await asyncio.to_thread(session_store.get, session_id)
    if session is None:
        yield sse_event("error", message="会话未找到")
        return

    await asyncio.to_thread(session_store.update, session_id, {
        "phase": "interview",
        "interview_dimensions_covered": [],
        "interview_question_count": 0,
    })

    system_prompt = build_interviewer_prompt()
    canvas_context = ""
    if session.get("canvas_tree"):
        canvas_context = f"\n\n## 当前产品方案摘要\n{json.dumps(session['canvas_tree'], ensure_ascii=False)}"

    messages = [
        {"role": "system", "content": system_prompt + canvas_context},
        {"role": "user", "content": "请开始面试。基于产品方案的完整内容，提出第一个尖锐问题。记住：每次只问一个问题，2-3句话。"},
    ]

    yield sse_event("phase_change", phase="interview")
    yield sse_event("interview_start")

    full_response = ""
    try:
        async for token in llm_stream(messages):
            full_response += token
            yield sse_event("token", role="interviewer", token=token)
    except LLMError as exc:
        logger.warning("面试官开场生成中断：%s", exc)
        if full_response:
            await asyncio.to_thread(
                session_store.add_message, session_id, "assistant", full_response + INTERRUPT_MARK, role_name="interviewer"
            )
        yield sse_event("error", role="interviewer", message=f"生成中断：{exc}")
        yield sse_event("done")
        return

    # 分类器返回 None 时默认标记第一个维度，避免漏标（B037）。
    dim = _classify_dimension(full_response, []) or INTERVIEW_DIMENSIONS[0]
    covered = [dim]

    await asyncio.to_thread(session_store.add_message, session_id, "assistant", full_response, role_name="interviewer")
    # 只在此处一次性 +1，避免双重递增（B037）。
    await asyncio.to_thread(session_store.update, session_id, {
        "interview_dimensions_covered": covered,
        "interview_question_count": 1,
    })

    yield _dimensions_update_event(covered)
    yield sse_event("role_done", role="interviewer", role_name="AI面试官")
    yield sse_event("done")


async def run_interview_respond(session_id: str, user_answer: str):
    """用户回答面试问题，AI 追问下一个。"""
    session = await asyncio.to_thread(session_store.get, session_id)
    if session is None:
        yield sse_event("error", message="会话未找到")
        return

    await asyncio.to_thread(session_store.add_message, session_id, "user", user_answer)

    covered = list(session.get("interview_dimensions_covered", []))
    question_count = int(session.get("interview_question_count", 0))

    if question_count >= MAX_INTERVIEW_QUESTIONS:
        # 达到问题上限，强制结束兜底（B037）。
        system_prompt = build_interviewer_prompt() + (
            f"\n\n已达到最大问题数（{MAX_INTERVIEW_QUESTIONS}）。请立即结束面试，列出 2-4 个未解决的缺口。"
        )
    elif len(covered) >= 6 and question_count >= 10:
        system_prompt = build_interviewer_prompt() + "\n\n所有 6 个维度已覆盖。请结束面试，列出 2-4 个未解决的缺口。"
    else:
        remaining = [d for d in INTERVIEW_DIMENSIONS if d not in covered]
        guidance = (
            f"\n\n优先覆盖尚未涉及的维度：{', '.join(remaining)}。"
            f"已覆盖：{', '.join(covered) if covered else '无'}。当前是第 {question_count + 1} 个问题。"
        )
        system_prompt = build_interviewer_prompt() + guidance

    recent = await asyncio.to_thread(session_store.get_recent_messages, session_id, 20)
    messages = [{"role": "system", "content": system_prompt}]
    for m in recent:
        content = m["content"]
        if m.get("role_name") == "interviewer":
            content = f"[AI面试官]: {content}"
        messages.append({"role": m["role"], "content": content})

    full_response = ""
    try:
        async for token in llm_stream(messages):
            full_response += token
            yield sse_event("token", role="interviewer", token=token)
    except LLMError as exc:
        logger.warning("面试官追问生成中断：%s", exc)
        if full_response:
            await asyncio.to_thread(
                session_store.add_message, session_id, "assistant", full_response + INTERRUPT_MARK, role_name="interviewer"
            )
        yield sse_event("error", role="interviewer", message=f"生成中断：{exc}")
        yield sse_event("done")
        return

    dim = _classify_dimension(full_response, covered)
    if dim and dim not in covered:
        covered.append(dim)

    await asyncio.to_thread(session_store.add_message, session_id, "assistant", full_response, role_name="interviewer")
    # 问题计数只在回合结束时一次性 +1（此前入口处还有一次递增导致双重计数，B037）。
    await asyncio.to_thread(session_store.update, session_id, {
        "interview_dimensions_covered": covered,
        "interview_question_count": question_count + 1,
    })

    yield _dimensions_update_event(covered)
    yield sse_event("role_done", role="interviewer", role_name="AI面试官")
    yield sse_event("done")


def _classify_dimension(text: str, covered: list[str]) -> str | None:
    """基于关键词的维度分类器：返回未覆盖维度中首个命中的 key。"""
    text_lower = text.lower()
    keywords = {
        "problem_validity": ["问题", "证据", "痛点", "真实", "假设", "验证", "谁有"],
        "solution_effectiveness": ["解决", "方案", "根因", "症状", "最简单", "核心功能"],
        "technical_risk": ["技术", "架构", "数据", "依赖", "扩展", "故障", "安全"],
        "business_loop": ["付费", "收入", "成本", "竞品", "壁垒", "市场", "盈利", "定价", "商业"],
        "user_adoption": ["用户", "使用", "下载", "激活", "留存", "切换", "习惯", "卸载"],
        "execution_risk": ["上线", "团队", "资源", "时间", "范围", "优先级", "裁员", "砍掉"],
    }
    # 按 INTERVIEW_DIMENSIONS 的固定顺序遍历，避免依赖字典迭代顺序（B103）。
    for dim in INTERVIEW_DIMENSIONS:
        if dim in covered:
            continue
        if any(kw in text_lower for kw in keywords[dim]):
            return dim
    return None
