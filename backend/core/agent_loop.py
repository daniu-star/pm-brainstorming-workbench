import json
import asyncio
import uuid
from openai import APIError
from core.role_prompts import build_system_prompt, COACH_PROMPT
from core.llm_client import llm_complete, llm_stream
from db.session_store import (
    CLARIFICATION_FIELD_ORDER,
    _new_clarification_state,
    session_store,
)


CLARIFICATION_FIELD_LABELS = {
    "target_user": "目标用户",
    "current_alternative": "当前替代方案",
    "product_form": "产品形态",
    "success_metric": "成功指标",
    "constraints": "关键约束",
}

CLARIFICATION_QUESTION_GUIDANCE = {
    "target_user": "请锁定最核心的一类用户，尽量具体到岗位、团队或使用场景。",
    "current_alternative": "请了解用户目前如何解决这件事，以及现有方法最痛的地方。",
    "product_form": "请确认预期交付的产品形态和最关键的使用入口。",
    "success_metric": "请确认上线后用什么可量化结果判断产品是否有效。",
    "constraints": "请确认时间、预算、合规、数据或技术方面的关键边界；没有也可以明确说没有。",
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


def _governance_context(session: dict) -> str:
    config = session.get("decision_hub", {}).get("agent_config", {})
    company_knowledge = str(config.get("company_knowledge", "")).strip()
    audit_rules = [
        str(rule).strip() for rule in config.get("audit_rules", []) if str(rule).strip()
    ]
    if not company_knowledge and not audit_rules:
        return ""
    formatted_rules = "\n".join(f"- {rule}" for rule in audit_rules)
    return (
        "\n\n## 公司业务上下文\n"
        f"{company_knowledge or '未配置'}\n\n"
        "## 团队审计规则\n"
        f"{formatted_rules or '- 未配置'}\n"
        "这些是用户明确配置的业务约束，不得忽略。"
    )


async def run_agent_turn(session_id: str, user_message: str, target_role: str, rag_context: str = "", api_key: str = "", base_url: str = "", model: str = ""):
    session = session_store.get(session_id)
    if session is None:
        yield _error_event("会话未找到")
        return

    system_prompt = build_system_prompt(target_role)

    if rag_context:
        system_prompt += f"\n\n## 相关知识库参考\n{rag_context}"
    system_prompt += _governance_context(session)

    recent = session_store.get_recent_messages(session_id, n=20)
    messages = [{"role": "system", "content": system_prompt}]
    for m in recent:
        role = m["role"]
        content = m["content"]
        if m.get("role_name"):
            content = f"[{m['role_name']}]: {content}"
        messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message})

    round_id = "round_" + uuid.uuid4().hex[:10]
    session_store.add_message(
        session_id,
        "user",
        user_message,
        stage="brainstorm",
        round_id=round_id,
    )

    yield f"data: {json.dumps({'type': 'round_started', 'round_id': round_id})}\n\n"
    yield f"data: {json.dumps({'type': 'role_start', 'role': target_role})}\n\n"

    full_response = ""
    total_tokens = 0
    try:
        async for token, token_count in llm_stream(messages, api_key=api_key or None, base_url=base_url or None, model=model or None):
            if token:
                full_response += token
                yield f"data: {json.dumps({'type': 'token', 'role': target_role, 'token': token})}\n\n"
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

    session_store.add_message(
        session_id,
        "assistant",
        full_response,
        role_name=target_role,
        stage="brainstorm",
        round_id=round_id,
        agent_role=target_role,
    )

    yield f"data: {json.dumps({'type': 'role_done', 'role': target_role})}\n\n"
    if total_tokens > 0 and not api_key:
        yield f"data: {json.dumps({'type': 'quota_deduct', 'tokens': total_tokens})}\n\n"
    yield f"data: {json.dumps({'type': 'round_completed', 'round_id': round_id})}\n\n"
    yield f"data: {json.dumps({'type': 'done'})}\n\n"


def _next_clarification_field(state: dict) -> str | None:
    fields = state.get("fields", {})
    for field in CLARIFICATION_FIELD_ORDER:
        if not str(fields.get(field, "")).strip():
            return field
    return None


def _coach_prompt(state: dict) -> str:
    current_field = state.get("current_field")
    fields = state.get("fields", {})
    if state.get("status") == "awaiting_confirmation":
        labels = {
            "target_user": "目标用户",
            "core_problem": "核心问题",
            "current_alternative": "当前替代方案",
            "product_form": "产品形态",
            "success_metric": "成功指标",
            "constraints": "关键约束",
        }
        brief = "\n".join(
            f"- {labels[key]}：{str(value).strip() or '尚未明确'}"
            for key, value in fields.items()
        )
        return (
            f"{COACH_PROMPT}\n\n"
            "你正在结束需求澄清。请严格基于下面的信息输出一份精炼的《需求澄清摘要》。"
            "不得补造事实，不要继续追问，不要出现问号。最后只提示用户可以确认进入多角色脑暴，"
            "或继续补充需要修改的信息。\n\n"
            f"{brief}"
        )

    field_label = CLARIFICATION_FIELD_LABELS.get(current_field, "待澄清信息")
    guidance = CLARIFICATION_QUESTION_GUIDANCE.get(current_field, "")
    return (
        f"{COACH_PROMPT}\n\n"
        "你正在执行结构化需求澄清。所有已知信息如下：\n"
        f"{json.dumps(fields, ensure_ascii=False)}\n\n"
        f"本轮唯一允许澄清的字段是“{field_label}”。{guidance}\n"
        "输出要求：先用一句不超过30字的话承接用户，再只问一个问题；"
        "全文只能出现一个问号；不得重复已填写字段，不得同时追问第二件事。"
    )


async def run_coach(
    session_id: str,
    user_message: str | None = None,
    *,
    start_only: bool = False,
    rag_context: str = "",
    api_key: str = "",
    base_url: str = "",
    model: str = "",
):
    session = session_store.get(session_id)
    if session is None:
        yield _error_event("会话未找到")
        return

    state = session.get("clarification_state") or _new_clarification_state(
        session.get("problem_statement", "")
    )
    if state.get("status") in ("confirmed", "skipped"):
        yield f"data: {json.dumps({'type': 'phase_change', 'phase': 'brainstorm'})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"
        return

    if not start_only:
        answer = (user_message or "").strip()
        if not answer:
            yield _error_event("回答不能为空")
            return
        current_field = state.get("current_field")
        if current_field:
            state.setdefault("fields", {})[current_field] = answer
            answered = state.setdefault("answered_fields", [])
            if current_field not in answered:
                answered.append(current_field)
        elif state.get("status") == "awaiting_confirmation":
            existing_constraints = state.setdefault("fields", {}).get("constraints", "")
            state["fields"]["constraints"] = (
                f"{existing_constraints}\n补充说明：{answer}".strip()
            )
        session_store.add_message(
            session_id,
            "user",
            answer,
            stage="clarify",
        )

    next_field = _next_clarification_field(state)
    if next_field:
        state["status"] = "collecting"
        state["current_field"] = next_field
        asked = state.setdefault("asked_fields", [])
        if next_field not in asked:
            asked.append(next_field)
    else:
        state["status"] = "awaiting_confirmation"
        state["current_field"] = None

    from datetime import datetime

    state["updated_at"] = datetime.now().isoformat()
    session_store.update(
        session_id,
        {"phase": "clarify", "clarification_state": state},
    )

    system_prompt = _coach_prompt(state)
    if rag_context:
        system_prompt += (
            "\n\n## 可引用的知识库上下文\n"
            f"{rag_context}\n"
            "只能把这些内容作为辅助背景；如果它与用户回答冲突，以用户回答为准。"
        )
    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": (
                "请基于已知信息开始第一轮澄清。"
                if start_only
                else f"用户刚才的回答是：{(user_message or '').strip()}"
            ),
        },
    ]

    yield f"data: {json.dumps({'type': 'phase_change', 'phase': 'clarify'})}\n\n"
    yield f"data: {json.dumps({'type': 'clarification_state', 'clarification_state': state}, ensure_ascii=False)}\n\n"
    yield f"data: {json.dumps({'type': 'role_start', 'role': 'coach'})}\n\n"

    full_response = ""
    total_tokens = 0
    try:
        async for token, token_count in llm_stream(messages, api_key=api_key or None, base_url=base_url or None, model=model or None):
            if token:
                full_response += token
                yield f"data: {json.dumps({'type': 'token', 'role': 'coach', 'token': token})}\n\n"
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

    session_store.add_message(
        session_id,
        "assistant",
        full_response,
        role_name="coach",
        stage="clarify",
        agent_role="coach",
    )

    yield f"data: {json.dumps({'type': 'role_done', 'role': 'coach', 'role_name': '产品教练'})}\n\n"
    if total_tokens > 0 and not api_key:
        yield f"data: {json.dumps({'type': 'quota_deduct', 'tokens': total_tokens})}\n\n"
    yield f"data: {json.dumps({'type': 'done'})}\n\n"


async def _collect_independent_opinion(
    role: str,
    frozen_context: list[dict],
    user_message: str,
    rag_context: str,
    governance_context: str,
    api_key: str,
    base_url: str,
    model: str,
) -> tuple[dict, int]:
    system_prompt = build_system_prompt(role)
    if rag_context:
        system_prompt += f"\n\n## 相关知识库参考\n{rag_context}"
    system_prompt += governance_context
    system_prompt += (
        "\n\n## 独立评审规则\n"
        "你正在进行独立首轮评审，看不到其他本轮 Agent 的答案。"
        "必须明确给出：核心判断、建议、依据、风险、证据缺口、隐含假设。"
        "不允许虚构数据或来源。只输出严格 JSON："
        '{"stance":"核心判断","recommendation":"建议","rationale":["依据"],'
        '"risks":["风险"],"evidence_gaps":["证据缺口"],"assumptions":["隐含假设"],'
        '"confidence":0到100的整数}。'
    )
    messages = [{"role": "system", "content": system_prompt}, *frozen_context]
    messages.append({"role": "user", "content": user_message})
    response, tokens = await llm_complete(
        messages,
        temperature=0.55,
        api_key=api_key or None,
        base_url=base_url or None,
        model=model or None,
    )
    cleaned = response.strip()
    if "```json" in cleaned:
        cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
    elif cleaned.startswith("```"):
        cleaned = cleaned.split("```", 1)[1].split("```", 1)[0].strip()
    try:
        parsed = json.loads(cleaned)
        opinion = {
            "agent_role": role,
            "stance": str(parsed.get("stance", "")).strip(),
            "recommendation": str(parsed.get("recommendation", "")).strip(),
            "rationale": [str(item) for item in parsed.get("rationale", [])][:6],
            "risks": [str(item) for item in parsed.get("risks", [])][:6],
            "evidence_gaps": [str(item) for item in parsed.get("evidence_gaps", [])][:6],
            "assumptions": [str(item) for item in parsed.get("assumptions", [])][:6],
            "confidence": max(0, min(100, int(parsed.get("confidence", 50)))),
        }
    except (json.JSONDecodeError, TypeError, ValueError):
        opinion = {
            "agent_role": role,
            "stance": response[:240].strip(),
            "recommendation": response.strip(),
            "rationale": [],
            "risks": [],
            "evidence_gaps": ["模型未返回结构化证据缺口，需人工补充"],
            "assumptions": [],
            "confidence": 40,
        }
    return opinion, tokens


def _format_opinion(opinion: dict) -> str:
    def section(title: str, values: list[str]) -> str:
        if not values:
            return ""
        return f"\n\n### {title}\n" + "\n".join(f"- {value}" for value in values)

    return (
        f"### 核心判断\n{opinion.get('stance') or '未形成明确判断'}"
        f"\n\n### 建议\n{opinion.get('recommendation') or '暂无'}"
        + section("依据", opinion.get("rationale", []))
        + section("风险", opinion.get("risks", []))
        + section("证据缺口", opinion.get("evidence_gaps", []))
        + section("隐含假设", opinion.get("assumptions", []))
        + f"\n\n置信度：{opinion.get('confidence', 0)}%"
    )


async def run_ask_all(session_id: str, user_message: str, rag_context: str = "", api_key: str = "", base_url: str = "", model: str = ""):
    session = session_store.get(session_id)
    if session is None:
        yield _error_event("会话未找到")
        return

    frozen_recent = session_store.get_recent_messages(session_id, n=20)
    frozen_context = []
    for message in frozen_recent:
        content = message.get("content", "")
        if message.get("role_name"):
            content = f"[{message['role_name']}]: {content}"
        frozen_context.append({"role": message.get("role", "user"), "content": content})

    round_id = "round_" + uuid.uuid4().hex[:10]
    session_store.add_message(
        session_id,
        "user",
        user_message,
        stage="brainstorm",
        round_id=round_id,
    )
    roles = ["cto", "designer", "ops", "user"]
    yield f"data: {json.dumps({'type': 'round_started', 'round_id': round_id, 'roles': roles})}\n\n"

    try:
        results = await asyncio.gather(
            *[
                _collect_independent_opinion(
                    role,
                    frozen_context,
                    user_message,
                    rag_context,
                    _governance_context(session),
                    api_key,
                    base_url,
                    model,
                )
                for role in roles
            ]
        )
    except ValueError as e:
        yield _error_event(str(e), "CONFIGURATION_ERROR")
        return
    except APIError as e:
        status = getattr(e, "status_code", None)
        code = "AUTHENTICATION_ERROR" if status == 401 else "AI_PROVIDER_ERROR"
        yield _error_event(f"AI 服务请求失败：{e.message if hasattr(e, 'message') else str(e)}", code, status not in (400, 401, 403))
        return
    except Exception as e:
        yield _error_event(f"AI 服务异常：{str(e)}", "AI_SERVICE_ERROR", True)
        return

    opinions = []
    accumulated_tokens = 0
    for role, (opinion, tokens) in zip(roles, results):
        opinions.append(opinion)
        accumulated_tokens += tokens
        response = _format_opinion(opinion)
        yield f"data: {json.dumps({'type': 'role_start', 'role': role})}\n\n"
        yield f"data: {json.dumps({'type': 'token', 'role': role, 'token': response}, ensure_ascii=False)}\n\n"
        session_store.add_message(
            session_id,
            "assistant",
            response,
            role_name=role,
            stage="brainstorm",
            round_id=round_id,
            agent_role=role,
        )
        yield f"data: {json.dumps({'type': 'agent_opinion', 'round_id': round_id, 'opinion': opinion}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type': 'role_done', 'role': role, 'round_id': round_id})}\n\n"

    synthesis_messages = [
        {
            "role": "system",
            "content": (
                "你是产品决策综合官。基于四份彼此独立的评审，输出结构化综合结论。"
                "必须包含五个二级标题：共识、关键冲突、证据缺口、隐含假设、建议优先讨论。"
                "不得用多数票抹平冲突，不得补造数据；每个冲突要写清各方立场和需要什么证据才能裁决。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {"question": user_message, "opinions": opinions},
                ensure_ascii=False,
            ),
        },
    ]
    try:
        synthesis, synthesis_tokens = await llm_complete(
            synthesis_messages,
            temperature=0.25,
            api_key=api_key or None,
            base_url=base_url or None,
            model=model or None,
        )
        accumulated_tokens += synthesis_tokens
    except Exception as error:
        yield _error_event(f"角色观点已保存，但综合结论生成失败：{str(error)}")
        return

    yield f"data: {json.dumps({'type': 'role_start', 'role': 'synthesizer'})}\n\n"
    yield f"data: {json.dumps({'type': 'token', 'role': 'synthesizer', 'token': synthesis}, ensure_ascii=False)}\n\n"
    session_store.add_message(
        session_id,
        "assistant",
        synthesis,
        role_name="synthesizer",
        stage="brainstorm",
        round_id=round_id,
        agent_role="synthesizer",
    )
    yield f"data: {json.dumps({'type': 'role_done', 'role': 'synthesizer', 'role_name': '决策综合官', 'round_id': round_id})}\n\n"

    session = session_store.get(session_id) or {}
    rounds = session.get("brainstorm_rounds", [])
    rounds.append(
        {
            "id": round_id,
            "context_message_count": len(frozen_context),
            "opinions": opinions,
            "synthesis": synthesis,
            "status": "completed",
        }
    )
    session_store.update(session_id, {"brainstorm_rounds": rounds})

    if accumulated_tokens > 0 and not api_key:
        yield f"data: {json.dumps({'type': 'quota_deduct', 'tokens': accumulated_tokens})}\n\n"
    yield f"data: {json.dumps({'type': 'round_completed', 'round_id': round_id})}\n\n"
    yield f"data: {json.dumps({'type': 'done'})}\n\n"
