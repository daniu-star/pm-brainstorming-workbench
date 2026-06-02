import json
import asyncio
from openai import APIError
from core.role_prompts import build_system_prompt, COACH_PROMPT
from core.llm_client import llm_stream
from db.session_store import session_store


def _error_event(message: str) -> str:
    return f"data: {json.dumps({'type': 'error', 'message': message})}\n\n"


async def run_agent_turn(session_id: str, user_message: str, target_role: str, rag_context: str = "", api_key: str = "", base_url: str = "", model: str = ""):
    session = session_store.get(session_id)
    if session is None:
        yield _error_event("会话未找到")
        return

    system_prompt = build_system_prompt(target_role)

    if rag_context:
        system_prompt += f"\n\n## 相关知识库参考\n{rag_context}"

    recent = session_store.get_recent_messages(session_id, n=20)
    messages = [{"role": "system", "content": system_prompt}]
    for m in recent:
        role = m["role"]
        content = m["content"]
        if m.get("role_name"):
            content = f"[{m['role_name']}]: {content}"
        messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message})

    session_store.add_message(session_id, "user", user_message)

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

    session_store.add_message(session_id, "assistant", full_response, role_name=target_role)

    yield f"data: {json.dumps({'type': 'role_done', 'role': target_role})}\n\n"
    if total_tokens > 0 and not api_key:
        yield f"data: {json.dumps({'type': 'quota_deduct', 'tokens': total_tokens})}\n\n"
    yield f"data: {json.dumps({'type': 'done'})}\n\n"


async def run_coach(session_id: str, user_message: str, api_key: str = "", base_url: str = "", model: str = ""):
    session = session_store.get(session_id)
    if session is None:
        yield _error_event("会话未找到")
        return

    session_store.add_message(session_id, "user", user_message)
    session_store.update(session_id, {"phase": "coach"})

    messages = [
        {"role": "system", "content": COACH_PROMPT},
        {"role": "user", "content": f"我有一个产品想法，请帮我理清思路：\n\n{user_message}"},
    ]

    yield f"data: {json.dumps({'type': 'phase_change', 'phase': 'coach'})}\n\n"
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

    session_store.add_message(session_id, "assistant", full_response, role_name="coach")

    yield f"data: {json.dumps({'type': 'role_done', 'role': 'coach', 'role_name': '产品教练'})}\n\n"
    if total_tokens > 0 and not api_key:
        yield f"data: {json.dumps({'type': 'quota_deduct', 'tokens': total_tokens})}\n\n"
    yield f"data: {json.dumps({'type': 'done'})}\n\n"


async def run_ask_all(session_id: str, user_message: str, rag_context: str = "", api_key: str = "", base_url: str = "", model: str = ""):
    session = session_store.get(session_id)
    if session is None:
        yield _error_event("会话未找到")
        return

    session_store.add_message(session_id, "user", user_message)

    roles = ["cto", "designer", "ops", "user"]
    accumulated_tokens = 0

    for role in roles:
        system_prompt = build_system_prompt(role)

        if rag_context:
            system_prompt += f"\n\n## 相关知识库参考\n{rag_context}"

        recent = session_store.get_recent_messages(session_id, n=20)
        messages = [{"role": "system", "content": system_prompt}]
        for m in recent:
            r = m["role"]
            content = m["content"]
            if m.get("role_name"):
                content = f"[{m['role_name']}]: {content}"
            messages.append({"role": r, "content": content})

        yield f"data: {json.dumps({'type': 'role_start', 'role': role})}\n\n"

        full_response = ""
        try:
            async for token, token_count in llm_stream(messages, api_key=api_key or None, base_url=base_url or None, model=model or None):
                if token:
                    full_response += token
                    yield f"data: {json.dumps({'type': 'token', 'role': role, 'token': token})}\n\n"
                if token_count > 0:
                    accumulated_tokens += token_count
        except ValueError as e:
            yield _error_event(str(e))
            return
        except APIError as e:
            yield _error_event(f"AI 服务请求失败：{e.message if hasattr(e, 'message') else str(e)}")
            return
        except Exception as e:
            yield _error_event(f"AI 服务异常：{str(e)}")
            return

        session_store.add_message(session_id, "assistant", full_response, role_name=role)

        yield f"data: {json.dumps({'type': 'role_done', 'role': role})}\n\n"

        await asyncio.sleep(0.3)

    if accumulated_tokens > 0 and not api_key:
        yield f"data: {json.dumps({'type': 'quota_deduct', 'tokens': accumulated_tokens})}\n\n"
    yield f"data: {json.dumps({'type': 'done'})}\n\n"
