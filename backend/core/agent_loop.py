"""多角色脑暴主循环：并发生成、异常兜底、SSE 流式输出。"""
import asyncio
import logging

from core.config import settings
from core.llm_client import LLMError, llm_stream
from core.role_prompts import COACH_PROMPT, build_system_prompt
from core.sse import sse_event
from db.session_store import session_store

logger = logging.getLogger(__name__)

ASK_ALL_ROLES = ["cto", "designer", "ops", "user"]
INTERRUPT_MARK = "[生成中断]"
ROLE_DISPLAY_NAMES = {
    "cto": "技术负责人",
    "designer": "设计师",
    "ops": "运营负责人",
    "user": "目标用户",
    "coach": "产品教练",
    "interviewer": "AI 面试官",
}


def _build_llm_messages(system_prompt: str, recent: list[dict], user_message: str | None = None) -> list[dict]:
    """基于历史消息快照构造 LLM 消息列表。"""
    messages = [{"role": "system", "content": system_prompt}]
    for m in recent:
        content = m["content"]
        if m.get("role_name"):
            content = f"[{m['role_name']}]: {content}"
        messages.append({"role": m["role"], "content": content})
    if user_message is not None:
        messages.append({"role": "user", "content": user_message})
    return messages


async def _persist_partial(session_id: str, role: str, content: str) -> str:
    """流中断时保存已生成部分并追加中断标记（B030）。"""
    saved = content + INTERRUPT_MARK
    await asyncio.to_thread(session_store.add_message, session_id, "assistant", saved, role_name=role)
    return saved


async def run_agent_turn(session_id: str, user_message: str, target_role: str, rag_context: str = ""):
    """单角色回合。"""
    session = await asyncio.to_thread(session_store.get, session_id)
    if session is None:
        yield sse_event("error", message="会话未找到")
        return

    # 任何脑暴消息都意味着离开 coach 引导阶段（B111）。
    if session.get("phase") != "brainstorm":
        await asyncio.to_thread(session_store.update, session_id, {"phase": "brainstorm"})
        yield sse_event("phase_change", phase="brainstorm")

    system_prompt = build_system_prompt(target_role)
    if rag_context:
        system_prompt += f"\n\n## 相关知识库参考\n{rag_context}"

    recent = await asyncio.to_thread(session_store.get_recent_messages, session_id, 20)
    messages = _build_llm_messages(system_prompt, recent, user_message)

    await asyncio.to_thread(session_store.add_message, session_id, "user", user_message)

    yield sse_event("role_start", role=target_role, role_name=ROLE_DISPLAY_NAMES.get(target_role, target_role))

    full_response = ""
    try:
        async for token in llm_stream(messages):
            full_response += token
            yield sse_event("token", role=target_role, token=token)
    except LLMError as exc:
        logger.warning("单角色生成中断（role=%s）：%s", target_role, exc)
        if full_response:
            await _persist_partial(session_id, target_role, full_response)
        yield sse_event("error", role=target_role, message=f"生成中断：{exc}")
        yield sse_event("done")
        return

    await asyncio.to_thread(session_store.add_message, session_id, "assistant", full_response, role_name=target_role)

    # role_done 携带全量内容与角色名，前端不依赖 token 流拼接（B110/B118）。
    yield sse_event("role_done", role=target_role, role_name=ROLE_DISPLAY_NAMES.get(target_role, target_role), content=full_response)
    yield sse_event("done")


async def run_coach(session_id: str, user_message: str):
    """产品教练引导式发问 — 在四角色脑暴前先帮用户理清思路。"""
    session = await asyncio.to_thread(session_store.get, session_id)
    if session is None:
        yield sse_event("error", message="会话未找到")
        return

    await asyncio.to_thread(session_store.add_message, session_id, "user", user_message)
    await asyncio.to_thread(session_store.update, session_id, {"phase": "coach"})

    messages = [
        {"role": "system", "content": COACH_PROMPT},
        {"role": "user", "content": f"我有一个产品想法，请帮我理清思路：\n\n{user_message}"},
    ]

    yield sse_event("phase_change", phase="coach")
    yield sse_event("role_start", role="coach", role_name="产品教练")

    full_response = ""
    try:
        async for token in llm_stream(messages):
            full_response += token
            yield sse_event("token", role="coach", token=token)
    except LLMError as exc:
        logger.warning("教练生成中断：%s", exc)
        if full_response:
            await _persist_partial(session_id, "coach", full_response)
        yield sse_event("error", role="coach", message=f"生成中断：{exc}")
        yield sse_event("done")
        return

    await asyncio.to_thread(session_store.add_message, session_id, "assistant", full_response, role_name="coach")

    yield sse_event("role_done", role="coach", role_name="产品教练", content=full_response)
    yield sse_event("done")


async def run_ask_all(session_id: str, user_message: str, rag_context: str = ""):
    """四角色并发脑暴。

    每个角色基于同一份调用前消息快照（+ 当前用户消息）独立生成，
    互不可见彼此的回答，避免串行污染（B029）；
    token 事件按到达顺序经队列转发；每个角色完成后立即持久化，
    再发 role_done（携带全量 content），保证画布增量与前端消息不依赖事件时序（B110/B112）。
    """
    session = await asyncio.to_thread(session_store.get, session_id)
    if session is None:
        yield sse_event("error", message="会话未找到")
        return

    # 任何群发脑暴消息都意味着离开 coach 引导阶段（B111）。
    if session.get("phase") != "brainstorm":
        await asyncio.to_thread(session_store.update, session_id, {"phase": "brainstorm"})
        yield sse_event("phase_change", phase="brainstorm")

    # 先取一次调用前快照，四个角色共享同一份上下文。
    recent = await asyncio.to_thread(session_store.get_recent_messages, session_id, 20)
    await asyncio.to_thread(session_store.add_message, session_id, "user", user_message)

    queue: asyncio.Queue = asyncio.Queue()

    async def run_role(role: str) -> None:
        system_prompt = build_system_prompt(role)
        if rag_context:
            system_prompt += f"\n\n## 相关知识库参考\n{rag_context}"
        messages = _build_llm_messages(system_prompt, recent, user_message)
        display_name = ROLE_DISPLAY_NAMES.get(role, role)

        await queue.put((sse_event("role_start", role=role, role_name=display_name), False))
        full_response = ""
        try:
            async for token in llm_stream(messages):
                full_response += token
                await queue.put((sse_event("token", role=role, token=token), False))
        except LLMError as exc:
            logger.warning("角色生成中断（role=%s）：%s", role, exc)
            # 空响应不落库，避免裸 [生成中断] 占位消息（B113）。
            if full_response:
                await _persist_partial(session_id, role, full_response)
            # 单角色失败是非终结性错误，不中断其余角色（B116）。
            await queue.put((sse_event(
                "role_error", role=role, role_name=display_name,
                message=f"{display_name} 生成中断：{exc}",
            ), False))
            return
        if settings.agent_role_interval > 0:
            await asyncio.sleep(settings.agent_role_interval)
        # 先持久化再发 role_done，保证随后到达的画布增量请求能看到该消息（B112）。
        try:
            await asyncio.to_thread(session_store.add_message, session_id, "assistant", full_response, role_name=role)
        except Exception as exc:  # noqa: BLE001
            # 会话被并发删除等边缘场景：转非终结错误，保证 done 仍能发出（B131）。
            logger.warning("角色消息持久化失败（role=%s）：%s", role, exc)
            await queue.put((sse_event(
                "role_error", role=role, role_name=display_name,
                message=f"{display_name} 回复保存失败",
            ), False))
            return
        await queue.put((sse_event(
            "role_done", role=role, role_name=display_name, content=full_response,
        ), False))

    tasks = [asyncio.create_task(run_role(role)) for role in ASK_ALL_ROLES]
    try:
        # 按到达顺序转发各角色事件。
        while True:
            all_done = all(task.done() for task in tasks)
            while not queue.empty():
                event, _ = queue.get_nowait()
                yield event
            if all_done:
                break
            await asyncio.sleep(0.02)

        # 单个任务异常不应阻断 done 事件（B131）。
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for role, result in zip(ASK_ALL_ROLES, results):
            if isinstance(result, Exception):
                logger.warning("角色任务异常退出（role=%s）：%s", role, result)
        yield sse_event("done")
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
