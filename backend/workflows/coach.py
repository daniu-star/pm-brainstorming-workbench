"""澄清工作流（PRD FR-01 / FR-04，接口契约 §4.3）。

职责边界
--------
* 只做“读上下文 → 调一次模型 → 产出一轮提问 + 候选 Brief 更新”；
* **不写库、不提交事务**：全部读取走 ``repositories.session_repo`` 公开 API，
  结果以 :class:`services.command_service.RunOutcome` 交回 runner，由
  ``command_service.apply_outcome`` 在单一事务内落库（PRD ADR-02）；
* 模型调用一律经 ``ctx.llm``（runner 注入的 ``BudgetedLLM``），本模块不建
  HTTP 客户端、不读环境变量。

确定性边界
----------
``coach_question_plan`` 是纯函数，决定“要不要问 / 问哪个字段 / 是否就绪”；
模型只负责组织措辞与提出候选 Brief 值。因此模型抽风时存在完全确定的降级
路径（见 :func:`_fallback_content`），且 ``confirmed`` 永远只能由用户命令产生。

对外接口
--------
``coach_question_plan(brief, *, clarify_round) -> dict``
    纯函数问题计划：``{"target_field", "should_ask", "brief_ready"}``。
``async def run_clarify(ctx) -> RunOutcome``
    M1 唯一的工作流入口（runner 直接 ``await``）。
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from pydantic import Field, ValidationError

from core.role_prompts import (
    COACH_SCHEMA,
    MAX_COACH_FOLLOW_UPS,
    PROMPT_VERSION,
    build_role_messages,
)
from domain.contracts import (
    BRIEF_FIELDS,
    MAX_USER_INPUT_CHARS,
    STEP_TIMEOUT_SECONDS,
    BriefFieldStatus,
    BriefStatus,
    EventType,
    MessageStage,
    MessageStatus,
    ProductBrief,
    ProductState,
    RoleCode,
    RunStatus,
    SenderKind,
    StrictModel,
    utc_now_iso,
)
from domain.errors import AppError, ErrorCode
from services.context_builder import build_context

logger = logging.getLogger("workflows.coach")

#: 单轮教练调用的输出上限（远小于全局 ``MAX_OUTPUT_TOKENS``，问题不需要长文本）。
COACH_MAX_OUTPUT_TOKENS = 900

#: 澄清轮次上限；达到后不再提问，改为让用户补充或直接确认。
CLARIFY_ROUND_LIMIT = 6

#: 写回 Brief 字段值时的长度上限（与 ``BriefField.value`` 一致）。
BRIEF_VALUE_MAX_CHARS = 2000

#: 教练输出的 LLM 用途标签（写入日志/使用量统计）。
LLM_PURPOSE = "coach.clarify"

#: 七个 Brief 字段的中文标签（提问与降级文案共用）。
FIELD_LABELS: dict[str, str] = {
    "target_users": "目标用户",
    "usage_scenario": "使用场景",
    "core_problem": "核心问题",
    "current_alternatives": "现有替代方案",
    "product_form": "产品形态",
    "constraints": "硬约束",
    "success_criteria": "成功标准",
}

#: 确定性降级问题模板：键与 ``BRIEF_FIELDS`` 对齐，保证任何字段都有话可问。
FALLBACK_QUESTIONS: dict[str, str] = {
    "target_users": "这个产品最主要面向谁？请描述他们的身份、所处场景和最头疼的问题。",
    "usage_scenario": "请描述一个具体的使用时刻：用户在什么情况下会打开这个产品？",
    "core_problem": "这个产品要解决的核心问题是什么？如果不解决，用户会付出什么代价？",
    "current_alternatives": "在还没有这个产品时，用户目前用什么方式应对这个问题？",
    "product_form": "你希望它以什么形态交付给用户（网页、移动应用、内部工具还是服务）？",
    "constraints": "这个项目有哪些不能违反的硬性约束（合规、预算、技术栈、上线时间）？",
    "success_criteria": "你如何判断这件事做成了？请给出可衡量的成功标准。",
}

#: 修复轮的追加指令（``{error}`` 用 ``str.replace`` 填充，避免用户文本里的花括号）。
REPAIR_INSTRUCTION = (
    "上面的输出不符合约定的 JSON 协议：{error}\n"
    "请只输出一个修正后的 JSON 对象，不要解释、不要 markdown 代码块、不要额外字段。"
)

_FENCE_RE = re.compile(r"^```(?:json|JSON)?\s*(.*?)\s*```$", re.DOTALL)


# --------------------------------------------------------------------------- #
# 输出协议（pydantic 严格校验，extra=forbid）
# --------------------------------------------------------------------------- #


class BriefUpdates(StrictModel):
    """教练提出的候选 Brief 字段值；全部可选，未知字段用 ``null``。"""

    target_users: str | None = None
    usage_scenario: str | None = None
    core_problem: str | None = None
    current_alternatives: str | None = None
    product_form: str | None = None
    constraints: str | None = None
    success_criteria: str | None = None


class CoachOutput(StrictModel):
    """教练一轮输出：一个主问题 + 至多两个追问 + 候选 Brief 更新 + 是否就绪。"""

    message: str = Field(min_length=1, max_length=MAX_USER_INPUT_CHARS)
    follow_ups: list[str] = Field(default_factory=list, max_length=8)
    brief_updates: BriefUpdates | None = None
    brief_ready: bool = False


# --------------------------------------------------------------------------- #
# 问题计划（纯函数）
# --------------------------------------------------------------------------- #


def coach_question_plan(brief: ProductBrief, *, clarify_round: int) -> dict:
    """决定这一轮是否提问、瞄准哪个字段。

    纯函数：只依赖 ``brief`` 与 ``clarify_round``，无 I/O、无随机、无时间。

    规则
    ----
    1. ``required_missing``（目标用户/使用场景/核心问题/成功标准）优先；
       四个必填都齐了才退而求其次问剩余的可选字段；
    2. 七个字段全都有值时 ``target_field`` 为 ``None``、``should_ask`` 为
       ``False``；
    3. ``clarify_round >= CLARIFY_ROUND_LIMIT``（6）时 ``should_ask`` 为
       ``False``——宁可停下让用户补充，也不无限追问；
    4. ``brief_ready`` 只表示“可供用户确认”，不代表任何字段被 ``confirmed``。
    """
    if not isinstance(clarify_round, int) or clarify_round < 0:
        clarify_round = 0
    missing_required = list(brief.required_missing()) if brief is not None else list(BRIEF_FIELDS)
    missing_optional = list(brief.missing_fields()) if brief is not None else []

    target_field = (missing_required or missing_optional or [None])[0]
    brief_ready = not missing_required and brief is not None and brief.ready()
    should_ask = (
        target_field is not None and clarify_round < CLARIFY_ROUND_LIMIT and not brief_ready
    )
    return {
        "target_field": target_field,
        "should_ask": bool(should_ask),
        "brief_ready": bool(brief_ready),
    }


# --------------------------------------------------------------------------- #
# 解析工具
# --------------------------------------------------------------------------- #


def extract_json_object(text: str) -> dict:
    """从模型输出里取出 JSON 对象；容忍 ```json 围栏与前后闲话。"""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("模型返回为空")
    raw = text.strip()
    fenced = _FENCE_RE.match(raw)
    if fenced:
        raw = fenced.group(1).strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise ValueError(f"响应中没有 JSON 对象：{exc.msg}") from exc
        try:
            payload = json.loads(raw[start : end + 1])
        except json.JSONDecodeError as exc2:
            raise ValueError(f"JSON 解析失败：{exc2.msg}") from exc2
    if not isinstance(payload, dict):
        raise ValueError(f"顶层必须是 JSON 对象，实际是 {type(payload).__name__}")
    return payload


def _format_validation_error(exc: ValidationError) -> str:
    parts: list[str] = []
    for item in exc.errors()[:6]:
        location = ".".join(str(part) for part in item.get("loc", ())) or "<root>"
        parts.append(f"{location}: {item.get('msg')}")
    return "；".join(parts) or str(exc)


def parse_coach_output(text: str) -> CoachOutput:
    """解析 + 严格校验教练输出；任何不合规都抛 ``ValueError``（供修复轮使用）。"""
    payload = extract_json_object(text)
    try:
        return CoachOutput.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(_format_validation_error(exc)) from exc


# --------------------------------------------------------------------------- #
# LLM 调用与降级
# --------------------------------------------------------------------------- #


def _remaining_timeout(ctx: Any) -> float:
    """按 ``ctx.deadline_epoch`` 收敛单次调用超时，避免把步骤拖死。"""
    limit = float(STEP_TIMEOUT_SECONDS)
    deadline = getattr(ctx, "deadline_epoch", None)
    if isinstance(deadline, (int, float)) and not isinstance(deadline, bool):
        limit = min(limit, float(deadline) - time.time())
    return max(5.0, limit)


async def _complete(ctx: Any, messages: list[dict]) -> Any:
    """调用注入的 LLM 客户端（``LLMClient.complete`` 契约 §4.4）。"""
    llm = getattr(ctx, "llm", None)
    if llm is None:
        raise AppError(ErrorCode.INTERNAL, "缺少 LLM 客户端，无法执行澄清")
    return await llm.complete(
        messages=messages,
        max_output_tokens=COACH_MAX_OUTPUT_TOKENS,
        timeout_seconds=_remaining_timeout(ctx),
        purpose=LLM_PURPOSE,
        json_schema=COACH_SCHEMA,
    )


async def _generate(ctx: Any, messages: list[dict], warnings: list[str]) -> tuple[CoachOutput, Any]:
    """生成教练输出；JSON/校验失败时把错误回喂**修复一次**，仍失败抛 ``ValueError``。"""
    first = await _complete(ctx, messages)
    try:
        return parse_coach_output(getattr(first, "text", "")), first
    except ValueError as exc:
        warnings.append(f"教练输出不符合协议，已回喂校验错误修复一次：{exc}")
        logger.warning("教练输出不合规，进入修复轮：%s", exc)
        repair_messages = list(messages) + [
            {"role": "assistant", "content": (getattr(first, "text", "") or "")[:4000]},
            {"role": "user", "content": REPAIR_INSTRUCTION.replace("{error}", str(exc)[:600])},
        ]
        second = await _complete(ctx, repair_messages)
        return parse_coach_output(getattr(second, "text", "")), second


def _limit_follow_ups(follow_ups: list[str] | None, warnings: list[str]) -> list[str]:
    """一次最多 2 个追问；超出截断并记录 warning（契约要求）。"""
    cleaned = [item.strip() for item in (follow_ups or []) if isinstance(item, str) and item.strip()]
    if len(cleaned) > MAX_COACH_FOLLOW_UPS:
        warnings.append(
            f"教练返回 {len(cleaned)} 个追问，已截断为 {MAX_COACH_FOLLOW_UPS} 个"
        )
        cleaned = cleaned[:MAX_COACH_FOLLOW_UPS]
    return cleaned


def _render_content(message: str, follow_ups: list[str]) -> str:
    """把主问题与追问拼成一条人类可读消息（产品状态里只存结构化 JSON）。"""
    text = (message or "").strip()
    if not follow_ups:
        return text
    lines = [text, "", "追问："]
    lines.extend(f"{index}. {item}" for index, item in enumerate(follow_ups, start=1))
    return "\n".join(lines)


def _fallback_content(brief: ProductBrief, plan: dict, warnings: list[str]) -> str:
    """确定性降级提问：模板问题 + 缺失字段清单 + “（系统降级提问）”标记。"""
    target = plan.get("target_field")
    if not target:
        missing = brief.required_missing() or brief.missing_fields()
        target = missing[0] if missing else BRIEF_FIELDS[0]
    label = FIELD_LABELS.get(target, target)
    question = FALLBACK_QUESTIONS.get(target) or "请再补充一些这条产品定义的信息。"
    warnings.append(f"教练输出两次不可用，已使用确定性降级提问（字段 {target}）")

    parts = [f"关于「{label}」，我想先确认一下：{question}"]
    pending = brief.required_missing()
    if pending:
        labels = "、".join(FIELD_LABELS.get(field, field) for field in pending)
        parts.append(f"目前还缺：{labels}。")
    parts.append("（系统降级提问）")
    return "\n\n".join(parts)


def _closing_content(brief: ProductBrief, plan: dict, clarify_round: int) -> str:
    """不需要再提问时的收尾文案（定义已齐 / 轮次到顶）。"""
    if plan.get("brief_ready"):
        text = (
            "产品定义的关键部分已经齐了：目标用户、使用场景、核心问题和成功标准都有内容。\n\n"
            "请确认这份产品定义；确认后我们进入多角色方案评审。"
        )
    else:
        pending = brief.required_missing() or brief.missing_fields()
        labels = "、".join(FIELD_LABELS.get(field, field) for field in pending) or "（无）"
        text = (
            f"我们已经澄清了 {clarify_round} 轮，先停在这里，避免过度追问。\n\n"
            f"目前还缺：{labels}。你可以直接补充，或者先确认已有内容进入方案评审。"
        )
    text += "\n\n如还有可补充的约束（替代方案/形态/硬约束），可直接告诉我。"
    locked = brief.confirmed_fields()
    if locked:
        text += "\n\n已由你确认的字段：" + "、".join(
            FIELD_LABELS.get(field, field) for field in locked
        )
    return text


# --------------------------------------------------------------------------- #
# Brief 更新
# --------------------------------------------------------------------------- #


def _apply_brief_updates(
    brief: ProductBrief,
    updates: BriefUpdates | None,
    *,
    current_message: Any,
    warnings: list[str],
) -> tuple[ProductBrief, list[str]]:
    """把模型的候选值写进 Brief，**只允许 unknown→proposed / proposed 值更新**。

    * 已 ``confirmed`` 的字段一律拒绝模型改写（confirmed 只能由用户命令产生）；
    * ``status`` 的 draft→ready 推进由 :func:`run_clarify` 统一负责，本函数不改 status；
    * 值相同则不产生新状态（避免版本噪声）。
    """
    if updates is None:
        return brief, []

    source_ids: list[str] = []
    message_id = getattr(current_message, "id", None)
    if message_id:
        source_ids.append(str(message_id))

    payload = brief.model_dump()
    changed: list[str] = []
    for field in BRIEF_FIELDS:
        raw = getattr(updates, field, None)
        if not isinstance(raw, str):
            continue
        value = raw.strip()
        if not value:
            continue
        current: Any = getattr(brief, field)
        if current.status == BriefFieldStatus.confirmed:
            warnings.append(f"字段 {field} 已由用户确认，模型提出的更新被忽略")
            continue
        if current.value == value[:BRIEF_VALUE_MAX_CHARS]:
            continue
        merged = list(dict.fromkeys(list(current.source_message_ids) + source_ids))
        payload[field] = {
            "value": value[:BRIEF_VALUE_MAX_CHARS],
            "status": BriefFieldStatus.proposed.value,
            "source_message_ids": merged[:8],
        }
        changed.append(field)

    return ProductBrief.model_validate(payload), changed


# --------------------------------------------------------------------------- #
# 轮次
# --------------------------------------------------------------------------- #


def _count_clarify_rounds(db: Any, session_id: str) -> int:
    """已完成的澄清轮次 = 历史里 agent 发出的澄清消息数。"""
    if db is None:
        return 0
    from repositories import session_repo

    try:
        messages = session_repo.list_messages(
            db, session_id, limit=100, stage=MessageStage.clarify.value
        )
    except TypeError:  # pragma: no cover - 兼容未实现 stage 过滤的早期版本
        messages = session_repo.list_messages(db, session_id, limit=100)
    return sum(
        1
        for message in messages
        if str(getattr(message, "sender_kind", "")) == SenderKind.agent.value
    )


def _clarify_round(ctx: Any, state: ProductState) -> int:
    explicit = getattr(ctx, "clarify_round", None)
    if isinstance(explicit, int) and not isinstance(explicit, bool) and explicit >= 0:
        return explicit
    return _count_clarify_rounds(getattr(ctx, "db", None), state.session_id)


# --------------------------------------------------------------------------- #
# 工作流入口
# --------------------------------------------------------------------------- #


def _task_kind(ctx: Any) -> Any:
    from domain.contracts import TaskKind

    task_kind = getattr(ctx, "task_kind", None)
    return task_kind if isinstance(task_kind, TaskKind) else TaskKind.clarify


def _usage_dict(result: Any) -> dict | None:
    if result is None:
        return None
    try:
        usage = getattr(result, "usage", None)
        if usage is None:
            return None
        as_dict = getattr(usage, "as_dict", None)
        if callable(as_dict):
            return as_dict()
        if isinstance(usage, dict):
            return dict(usage)
    except Exception:  # pragma: no cover - usage 仅用于统计，不影响正确性
        logger.debug("读取 usage 失败", exc_info=True)
    return None


async def run_clarify(ctx: Any) -> Any:
    """执行一轮澄清（FR-01）。

    * 调模型前先用 :func:`coach_question_plan` 判断是否值得花一次上游调用；
    * 输出严格 JSON，解析/校验失败最多修复一次，仍失败则确定性降级；
    * 产出一条 ``stage=clarify`` 的 agent 消息、一个 ``product_state.updated`` 事件；
    * 事务由 runner 负责，本函数只读库。
    """
    from services.command_service import OutgoingMessage, RunOutcome

    state = getattr(ctx, "product_state", None)
    if not isinstance(state, ProductState):
        return RunOutcome(
            status=RunStatus.failed,
            error_code=ErrorCode.INVALID_REQUEST,
            error_message="缺少产品状态快照，无法执行澄清",
        )

    session_id = state.session_id
    current_message = getattr(ctx, "current_message", None)
    current_message_id = str(getattr(current_message, "id", "") or "") or None
    warnings: list[str] = []

    clarify_round = _clarify_round(ctx, state)
    plan = coach_question_plan(state.brief, clarify_round=clarify_round)

    content: str
    brief_updates: BriefUpdates | None = None
    llm_result: Any = None

    if not plan["should_ask"]:
        content = _closing_content(state.brief, plan, clarify_round)
        logger.info(
            "澄清无需提问 session=%s round=%s ready=%s",
            session_id,
            clarify_round,
            plan["brief_ready"],
        )
    else:
        try:
            bundle = build_context(
                getattr(ctx, "db", None),
                session_id=session_id,
                task_kind=_task_kind(ctx),
                role_code=RoleCode.coach,
                product_version=state.version,
                current_message_id=current_message_id,
                prompt_version=PROMPT_VERSION,
            )
        except AppError as exc:
            logger.warning("构建上下文失败 session=%s code=%s", session_id, exc.code)
            return RunOutcome(
                status=RunStatus.failed,
                error_code=str(exc.code),
                error_message=exc.message,
                warnings=warnings,
            )

        messages = build_role_messages(RoleCode.coach, bundle, PROMPT_VERSION)
        try:
            output, llm_result = await _generate(ctx, messages, warnings)
        except ValueError as exc:
            logger.warning("教练输出两次不合规，降级为确定性提问：%s", exc)
            warnings.append(f"修复一次后仍不符合输出协议：{exc}")
            content = _fallback_content(state.brief, plan, warnings)
        else:
            follow_ups = _limit_follow_ups(output.follow_ups, warnings)
            content = _render_content(output.message, follow_ups)
            brief_updates = output.brief_updates
            if output.brief_ready and not state.brief.ready():
                warnings.append("模型声称产品定义已就绪，但必填字段仍有缺口，已忽略该判断")

    new_brief, changed_fields = _apply_brief_updates(
        state.brief, brief_updates, current_message=current_message, warnings=warnings
    )
    # 状态推进统一放在公共段：提问路径与收敛路径（不调模型）都会执行。
    if new_brief.status == BriefStatus.draft and new_brief.ready():
        new_brief = new_brief.model_copy(update={"status": BriefStatus.ready})
    if new_brief.ready() and not state.brief.ready():
        warnings.append("产品定义已具备确认条件，等待用户确认（confirmed 只由用户命令产生）")

    payload = state.model_dump()
    payload["version"] = state.version + 1
    payload["brief"] = new_brief.model_dump()
    payload["created_at"] = utc_now_iso()
    run = getattr(ctx, "run", None)
    run_id = getattr(run, "id", None)
    if run_id:
        payload["source_run_id"] = str(run_id)
    new_state = ProductState.model_validate(payload)

    message = OutgoingMessage(
        content=content,
        sender_kind=SenderKind.agent,
        role_code=RoleCode.coach,
        stage=MessageStage.clarify,
        status=MessageStatus.completed,
    )
    events = [
        {
            "type": EventType.product_state_updated.value,
            "payload": {"product_version": new_state.version},
        }
    ]

    logger.info(
        "澄清完成 session=%s product_version=%s round=%s changed=%s brief_status=%s",
        session_id,
        new_state.version,
        clarify_round,
        changed_fields,
        new_brief.status.value,
    )
    return RunOutcome(
        status=RunStatus.succeeded,
        product_state=new_state,
        messages=[message],
        events=events,
        result_product_version=new_state.version,
        warnings=warnings,
        usage=_usage_dict(llm_result),
        session_phase=None,
        # phase / brief_status 由状态机与 command_service 决定，coach 不做推断：
        # session 侧会从已提交的 ProductState 推导 brief_status。
        brief_status=None,
    )


__all__ = [
    "BRIEF_VALUE_MAX_CHARS",
    "BriefUpdates",
    "CLARIFY_ROUND_LIMIT",
    "COACH_MAX_OUTPUT_TOKENS",
    "CoachOutput",
    "FALLBACK_QUESTIONS",
    "FIELD_LABELS",
    "coach_question_plan",
    "extract_json_object",
    "parse_coach_output",
    "run_clarify",
]