import json
from typing import List, Optional
from openai import APIError
from core.role_prompts import build_interviewer_prompt
from core.llm_client import llm_complete, llm_stream
from db.session_store import session_store, interview_session_store

INTERVIEW_DIMENSIONS = [
    "problem_validity",
    "solution_effectiveness",
    "technical_risk",
    "business_viability",
    "user_adoption",
    "execution_risk",
]

DIMENSION_LABELS = {
    "problem_validity": "问题有效性",
    "solution_effectiveness": "方案有效性",
    "technical_risk": "技术风险",
    "business_viability": "商业可行性",
    "user_adoption": "用户采纳",
    "execution_risk": "执行风险",
}


def _error_event(
    message: str,
    code: str = "AI_SERVICE_ERROR",
    retryable: bool = False,
) -> str:
    return (
        "data: "
        + json.dumps(
            {
                "type": "error",
                "message": message,
                "error_code": code,
                "retryable": retryable,
            }
        )
        + "\n\n"
    )


async def run_interview_start(session_id: str, api_key: str = "", base_url: str = "", model: str = ""):
    session = session_store.get(session_id)
    if session is None:
        yield _error_event("会话未找到")
        return

    session_store.transition_phase(session_id, "audit", "legacy_audit_started")
    if session.get("interview_question_count", 0) == 0:
        session_store.update(session_id, {
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

    yield f"data: {json.dumps({'type': 'phase_change', 'phase': 'audit'})}\n\n"
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


def _audit_state_event(space: dict) -> str:
    payload = {
        "type": "audit_state",
        "audit_run_id": space.get("audit_run_id") or space.get("id"),
        "audit_status": space.get("status", "not_started"),
        "dimensions_covered": space.get("dimensions_covered", []),
        "current_dimension": space.get("current_dimension"),
        "question_count": space.get("question_count", 0),
        "interview_completed": space.get("status") == "completed",
        "audit_report": space.get("report"),
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _dimension_prompt(dimension: str, question_number: int, rag_context: str = "") -> str:
    label = DIMENSION_LABELS[dimension]
    prompt = (
        build_interviewer_prompt()
        + f"\n\n本轮审计维度已由系统明确指定为“{label}”。"
        + f"这是第 {question_number} 个问题。"
        + "只能围绕该维度提出一个问题，不得根据关键词自行切换维度；"
        + "问题最多2句话，只出现一个问号，不要称赞用户。"
    )
    if rag_context:
        prompt += (
            "\n\n可引用的知识库上下文如下。不得把上下文中的一般性陈述伪装成该用户产品的事实：\n"
            + rag_context
        )
    return prompt


async def run_interview_start_space(interview_id: str, rag_context: str = "", api_key: str = "", base_url: str = "", model: str = ""):
    """幂等启动审计；已有进度时只恢复，不重置。"""
    space = interview_session_store.get(interview_id)
    if space is None:
        yield _error_event("面试空间未找到")
        return

    if space.get("question_count", 0) > 0 or space.get("status") == "completed":
        yield _audit_state_event(space)
        yield f"data: {json.dumps({'type': 'done'})}\n\n"
        return

    plan = space.get("dimension_plan") or INTERVIEW_DIMENSIONS
    current_dimension = plan[0]
    interview_session_store.update(interview_id, {
        "status": "active",
        "current_dimension": current_dimension,
    })
    parent_id = space.get("parent_session_id")
    if parent_id:
        session_store.transition_phase(parent_id, "audit", "audit_started")

    system_prompt = _dimension_prompt(current_dimension, 1, rag_context)
    canvas_context = ""
    if space.get("canvas_tree"):
        canvas_context = f"\n\n## 当前产品方案摘要\n{json.dumps(space['canvas_tree'], ensure_ascii=False)}"

    problem = space.get("problem_statement", "")
    user_msg = f"产品想法：{problem}\n\n请开始面试。基于产品方案的完整内容，提出第一个尖锐问题。记住：每次只问一个问题，2-3句话。"

    messages = [
        {"role": "system", "content": system_prompt + canvas_context},
        {"role": "user", "content": user_msg},
    ]

    yield f"data: {json.dumps({'type': 'phase_change', 'phase': 'audit'})}\n\n"
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

    interview_session_store.add_message(interview_id, "assistant", full_response, role_name="interviewer")
    interview_session_store.update(
        interview_id,
        {"question_count": 1, "current_dimension": current_dimension},
    )

    yield f"data: {json.dumps({'type': 'role_done', 'role': 'interviewer', 'role_name': 'AI审计官', 'dimensions_covered': [], 'question_count': 1, 'current_dimension': current_dimension})}\n\n"
    yield _audit_state_event(interview_session_store.get(interview_id) or {})
    if total_tokens > 0 and not api_key:
        yield f"data: {json.dumps({'type': 'quota_deduct', 'tokens': total_tokens})}\n\n"
    yield f"data: {json.dumps({'type': 'done'})}\n\n"


async def run_interview_respond_space(interview_id: str, user_answer: str, rag_context: str = "", api_key: str = "", base_url: str = "", model: str = ""):
    """独立面试空间的后续问答。"""
    space = interview_session_store.get(interview_id)
    if space is None:
        yield _error_event("面试空间未找到")
        return

    if space.get("status") == "completed":
        yield _audit_state_event(space)
        yield f"data: {json.dumps({'type': 'done'})}\n\n"
        return

    interview_session_store.add_message(interview_id, "user", user_answer)

    covered = list(space.get("dimensions_covered", []))
    question_count = space.get("question_count", 0)
    current_dimension = space.get("current_dimension")
    if current_dimension in INTERVIEW_DIMENSIONS and current_dimension not in covered:
        covered.append(current_dimension)

    recent = interview_session_store.get_recent_messages(interview_id, n=20)
    plan = space.get("dimension_plan") or INTERVIEW_DIMENSIONS

    if question_count >= len(plan):
        report_prompt = (
            "你是产品审计报告官。基于完整审计记录输出最终报告，必须包含："
            "审计结论、六维发现、关键风险、证据缺口、待验证假设、下一步行动。"
            "不得继续提问，不得虚构数据。使用简洁 Markdown。"
        )
        report_messages = [{"role": "system", "content": report_prompt}]
        for message in recent:
            report_messages.append(
                {"role": message["role"], "content": message.get("content", "")}
            )
        try:
            report, total_tokens = await llm_complete(
                report_messages,
                temperature=0.2,
                api_key=api_key or None,
                base_url=base_url or None,
                model=model or None,
            )
        except ValueError as error:
            yield _error_event(str(error))
            return
        except APIError as error:
            yield _error_event(f"AI 服务请求失败：{error.message if hasattr(error, 'message') else str(error)}")
            return
        except Exception as error:
            yield _error_event(f"AI 服务异常：{str(error)}")
            return

        interview_session_store.add_message(
            interview_id,
            "assistant",
            report,
            role_name="audit_report",
        )
        interview_session_store.update(
            interview_id,
            {
                "status": "completed",
                "dimensions_covered": covered,
                "current_dimension": None,
                "report": report,
            },
        )
        parent_id = space.get("parent_session_id")
        if parent_id:
            session_store.transition_phase(parent_id, "decision_ready", "audit_completed")
        yield f"data: {json.dumps({'type': 'role_start', 'role': 'audit_report'})}\n\n"
        yield f"data: {json.dumps({'type': 'token', 'role': 'audit_report', 'token': report}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type': 'role_done', 'role': 'audit_report', 'role_name': 'AI审计报告', 'dimensions_covered': covered, 'question_count': question_count, 'interview_completed': True})}\n\n"
        yield _audit_state_event(interview_session_store.get(interview_id) or {})
        if total_tokens > 0 and not api_key:
            yield f"data: {json.dumps({'type': 'quota_deduct', 'tokens': total_tokens})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"
        return

    next_dimension = plan[question_count]
    system_prompt = _dimension_prompt(next_dimension, question_count + 1, rag_context)
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

    new_count = question_count + 1
    interview_session_store.add_message(interview_id, "assistant", full_response, role_name="interviewer")
    interview_session_store.update(interview_id, {
        "dimensions_covered": covered,
        "question_count": new_count,
        "current_dimension": next_dimension,
        "status": "active",
    })

    yield f"data: {json.dumps({'type': 'role_done', 'role': 'interviewer', 'role_name': 'AI审计官', 'dimensions_covered': covered, 'question_count': new_count, 'interview_completed': False, 'current_dimension': next_dimension})}\n\n"
    yield _audit_state_event(interview_session_store.get(interview_id) or {})
    if total_tokens > 0 and not api_key:
        yield f"data: {json.dumps({'type': 'quota_deduct', 'tokens': total_tokens})}\n\n"
    yield f"data: {json.dumps({'type': 'done'})}\n\n"
