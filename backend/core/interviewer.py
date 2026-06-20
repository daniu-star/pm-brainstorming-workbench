import json
from typing import List, Optional
from openai import APIError
from core.role_prompts import build_interviewer_prompt
from core.llm_client import llm_stream
from db.session_store import session_store, interview_session_store

INTERVIEW_DIMENSIONS = [
    "problem_validity",
    "solution_effectiveness",
    "technical_risk",
    "business_viability",
    "user_adoption",
    "execution_risk",
]


def _error_event(message: str) -> str:
    return f"data: {json.dumps({'type': 'error', 'message': message})}\n\n"


async def run_interview_start(session_id: str, api_key: str = "", base_url: str = "", model: str = ""):
    session = session_store.get(session_id)
    if session is None:
        yield _error_event("会话未找到")
        return

    session_store.update(session_id, {
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

    yield f"data: {json.dumps({'type': 'phase_change', 'phase': 'interview'})}\n\n"
    yield f"data: {json.dumps({'type': 'interview_start'})}\n\n"

    full_response = ""
    total_tokens = 0
    try:
        async for token, token_count in llm_stream(messages, api_key=api_key or None, base_url=base_url or None, model=model or None):
            if token:
                full_response += token
                yield f"data: {json.dumps({'type': 'token', 'role': 'interviewer', 'token': token})}\n\n"
            if token_count > 0:
                total_tokens = token_count
    except ValueError as e:
        yield _error_event(str(e))
        return
    except APIError as e:
        yield _error_event(f"AI 服务请求失败：{e.message if hasattr(e, 'message') else str(e)}")
        return
    except Exception as e:
        yield _error_event(f"AI 服务异常：{str(e)}")
        return

    dim = _classify_dimension(full_response, [])
    if dim:
        session = session_store.get(session_id)
        covered = session.get("interview_dimensions_covered", [])
        if dim not in covered:
            covered.append(dim)
            session_store.update(session_id, {"interview_dimensions_covered": covered})

    session_store.add_message(session_id, "assistant", full_response, role_name="interviewer")
    session_store.update(session_id, {"interview_question_count": 1})

    yield f"data: {json.dumps({'type': 'role_done', 'role': 'interviewer', 'role_name': 'AI面试官'})}\n\n"
    if total_tokens > 0 and not api_key:
        yield f"data: {json.dumps({'type': 'quota_deduct', 'tokens': total_tokens})}\n\n"
    yield f"data: {json.dumps({'type': 'done'})}\n\n"


async def run_interview_respond(session_id: str, user_answer: str, api_key: str = "", base_url: str = "", model: str = ""):
    session = session_store.get(session_id)
    if session is None:
        yield _error_event("会话未找到")
        return

    session_store.add_message(session_id, "user", user_answer)

    covered = session.get("interview_dimensions_covered", [])
    question_count = session.get("interview_question_count", 0)

    if len(covered) >= 6 and question_count >= 10:
        system_prompt = build_interviewer_prompt() + "\n\n所有 6 个维度已覆盖。请结束面试，列出 2-4 个未解决的缺口。"
    else:
        remaining = [d for d in INTERVIEW_DIMENSIONS if d not in covered]
        guidance = f"\n\n优先覆盖尚未涉及的维度：{', '.join(remaining)}。已覆盖：{', '.join(covered) if covered else '无'}。当前是第 {question_count + 1} 个问题。"
        system_prompt = build_interviewer_prompt() + guidance

    recent = session_store.get_recent_messages(session_id, n=20)
    messages = [{"role": "system", "content": system_prompt}]
    for m in recent:
        content = m["content"]
        if m.get("role_name") == "interviewer":
            content = f"[AI面试官]: {content}"
        messages.append({"role": m["role"], "content": content})

    full_response = ""
    total_tokens = 0
    try:
        async for token, token_count in llm_stream(messages, api_key=api_key or None, base_url=base_url or None, model=model or None):
            if token:
                full_response += token
                yield f"data: {json.dumps({'type': 'token', 'role': 'interviewer', 'token': token})}\n\n"
            if token_count > 0:
                total_tokens = token_count
    except ValueError as e:
        yield _error_event(str(e))
        return
    except APIError as e:
        yield _error_event(f"AI 服务请求失败：{e.message if hasattr(e, 'message') else str(e)}")
        return
    except Exception as e:
        yield _error_event(f"AI 服务异常：{str(e)}")
        return

    dim = _classify_dimension(full_response, covered)
    if dim and dim not in covered:
        covered.append(dim)

    session = session_store.get(session_id)
    question_count = session.get("interview_question_count", 0)

    session_store.add_message(session_id, "assistant", full_response, role_name="interviewer")
    session_store.update(session_id, {
        "interview_dimensions_covered": covered,
        "interview_question_count": question_count + 1,
    })

    yield f"data: {json.dumps({'type': 'role_done', 'role': 'interviewer', 'role_name': 'AI面试官'})}\n\n"
    if total_tokens > 0 and not api_key:
        yield f"data: {json.dumps({'type': 'quota_deduct', 'tokens': total_tokens})}\n\n"
    yield f"data: {json.dumps({'type': 'done'})}\n\n"


def _classify_dimension(text: str, covered: List[str]) -> Optional[str]:
    text_lower = text.lower()
    keywords = {
        "problem_validity": ["问题", "证据", "痛点", "真实", "假设", "验证", "谁有"],
        "solution_effectiveness": ["解决", "方案", "根因", "症状", "最简单", "核心功能"],
        "technical_risk": ["技术", "架构", "数据", "依赖", "扩展", "故障", "安全"],
        "business_viability": ["付费", "收入", "成本", "竞品", "壁垒", "市场", "盈利", "定价"],
        "user_adoption": ["用户", "使用", "下载", "激活", "留存", "切换", "习惯", "卸载"],
        "execution_risk": ["上线", "团队", "资源", "时间", "范围", "优先级", "裁员", "砍掉"],
    }
    for dim, kws in keywords.items():
        if dim in covered:
            continue
        if any(kw in text_lower for kw in kws):
            return dim
    return None


async def run_interview_start_space(interview_id: str, api_key: str = "", base_url: str = "", model: str = ""):
    """启动独立面试空间的第一轮提问。"""
    space = interview_session_store.get(interview_id)
    if space is None:
        yield _error_event("面试空间未找到")
        return

    interview_session_store.update(interview_id, {
        "status": "active",
        "dimensions_covered": [],
        "question_count": 0,
    })

    system_prompt = build_interviewer_prompt()
    canvas_context = ""
    if space.get("canvas_tree"):
        canvas_context = f"\n\n## 当前产品方案摘要\n{json.dumps(space['canvas_tree'], ensure_ascii=False)}"

    problem = space.get("problem_statement", "")
    user_msg = f"产品想法：{problem}\n\n请开始面试。基于产品方案的完整内容，提出第一个尖锐问题。记住：每次只问一个问题，2-3句话。"

    messages = [
        {"role": "system", "content": system_prompt + canvas_context},
        {"role": "user", "content": user_msg},
    ]

    yield f"data: {json.dumps({'type': 'phase_change', 'phase': 'interview'})}\n\n"
    yield f"data: {json.dumps({'type': 'interview_start', 'interview_id': interview_id})}\n\n"

    full_response = ""
    total_tokens = 0
    try:
        async for token, token_count in llm_stream(messages, api_key=api_key or None, base_url=base_url or None, model=model or None):
            if token:
                full_response += token
                yield f"data: {json.dumps({'type': 'token', 'role': 'interviewer', 'token': token})}\n\n"
            if token_count > 0:
                total_tokens = token_count
    except ValueError as e:
        yield _error_event(str(e))
        return
    except APIError as e:
        yield _error_event(f"AI 服务请求失败：{e.message if hasattr(e, 'message') else str(e)}")
        return
    except Exception as e:
        yield _error_event(f"AI 服务异常：{str(e)}")
        return

    dim = _classify_dimension(full_response, [])
    covered = []
    if dim:
        covered.append(dim)
        interview_session_store.update(interview_id, {"dimensions_covered": covered})

    interview_session_store.add_message(interview_id, "assistant", full_response, role_name="interviewer")
    interview_session_store.update(interview_id, {"question_count": 1})

    yield f"data: {json.dumps({'type': 'role_done', 'role': 'interviewer', 'role_name': 'AI面试官', 'dimensions_covered': covered, 'question_count': 1})}\n\n"
    if total_tokens > 0 and not api_key:
        yield f"data: {json.dumps({'type': 'quota_deduct', 'tokens': total_tokens})}\n\n"
    yield f"data: {json.dumps({'type': 'done'})}\n\n"


async def run_interview_respond_space(interview_id: str, user_answer: str, api_key: str = "", base_url: str = "", model: str = ""):
    """独立面试空间的后续问答。"""
    space = interview_session_store.get(interview_id)
    if space is None:
        yield _error_event("面试空间未找到")
        return

    interview_session_store.add_message(interview_id, "user", user_answer)

    covered = space.get("dimensions_covered", [])
    question_count = space.get("question_count", 0)

    if len(covered) >= 6 and question_count >= 10:
        system_prompt = build_interviewer_prompt() + "\n\n所有 6 个维度已覆盖。请结束面试，列出 2-4 个未解决的缺口。"
        interview_session_store.update(interview_id, {"status": "completed"})
    else:
        remaining = [d for d in INTERVIEW_DIMENSIONS if d not in covered]
        guidance = f"\n\n优先覆盖尚未涉及的维度：{', '.join(remaining)}。已覆盖：{', '.join(covered) if covered else '无'}。当前是第 {question_count + 1} 个问题。"
        system_prompt = build_interviewer_prompt() + guidance

    recent = interview_session_store.get_recent_messages(interview_id, n=20)
    messages = [{"role": "system", "content": system_prompt}]
    for m in recent:
        content = m["content"]
        if m.get("role_name") == "interviewer":
            content = f"[AI面试官]: {content}"
        messages.append({"role": m["role"], "content": content})

    full_response = ""
    total_tokens = 0
    try:
        async for token, token_count in llm_stream(messages, api_key=api_key or None, base_url=base_url or None, model=model or None):
            if token:
                full_response += token
                yield f"data: {json.dumps({'type': 'token', 'role': 'interviewer', 'token': token})}\n\n"
            if token_count > 0:
                total_tokens = token_count
    except ValueError as e:
        yield _error_event(str(e))
        return
    except APIError as e:
        yield _error_event(f"AI 服务请求失败：{e.message if hasattr(e, 'message') else str(e)}")
        return
    except Exception as e:
        yield _error_event(f"AI 服务异常：{str(e)}")
        return

    dim = _classify_dimension(full_response, covered)
    if dim and dim not in covered:
        covered.append(dim)

    new_count = question_count + 1
    interview_session_store.add_message(interview_id, "assistant", full_response, role_name="interviewer")
    interview_session_store.update(interview_id, {
        "dimensions_covered": covered,
        "question_count": new_count,
    })

    is_completed = len(covered) >= 6 and new_count >= 10
    yield f"data: {json.dumps({'type': 'role_done', 'role': 'interviewer', 'role_name': 'AI面试官', 'dimensions_covered': covered, 'question_count': new_count, 'interview_completed': is_completed})}\n\n"
    if total_tokens > 0 and not api_key:
        yield f"data: {json.dumps({'type': 'quota_deduct', 'tokens': total_tokens})}\n\n"
    yield f"data: {json.dumps({'type': 'done'})}\n\n"
