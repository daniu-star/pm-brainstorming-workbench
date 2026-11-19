"""M3 审计闭环工作流（PRD FR-07）。

三个入口对应三种 Run：

* :func:`run_audit_open` —— 审计开场：生成第 1 个问题，``opening -> active``。
* :func:`run_audit_answer` —— 评估用户回答并生成下一个问题；达到目标/上限时
  在同一 Run 内转入 ``closing`` 并生成报告。
* :func:`run_audit_report` —— 基于结构化记录生成报告，``closing -> completed``。

设计要点（与 ``workflows/brainstorm.py`` 的差异）：

* 审计的写入是**主产物**，因此全部通过 ``RunOutcome.apply(db)`` 在
  ``command_service._commit_outcome`` 的同一事务里落库（问题、评估修订、
  维度状态、报告）。工作流自身不打开事务、也不写库。
* 所有模型调用都发生在提交之前；``apply`` 闭包里只做数据库写入，且带
  原子守卫：Run 已取消、审计已结束、或 ``run.base_audit_version`` 与当前
  ``audit.audit_version`` 不一致时**静默放弃**（PRD「进入 closing/cancelled
  后旧生成的下一问与评估结果失去提交资格」）。
* 上游传输失败（``LLMError``）分两段处理：仍有重试额度时继续向上抛给
  runner（保持「单步骤最多两次上游尝试」的既有语义）；额度耗尽或错误本身
  不可重试时，转成带 ``apply`` 的 failed 结果，让审计状态、结束原因与会话
  阶段一起落库——否则页面会永久停在「开场中/等待回答」，用户既看不到原因
  也没有恢复入口。
* 其他非 :class:`~domain.errors.AppError` 异常仍向上抛给 runner；
  ``product_state`` 恒为 ``None``（审计不推进产品版本），``brief_status``
  恒为 ``None``。
"""

from __future__ import annotations

import json
import time
from typing import Any, Iterable, Sequence

from pydantic import Field, ValidationError

from core.llm_client import LLMError
from core.role_prompts import PROMPT_VERSION, build_role_messages
from domain import state_machine as SM
from domain.contracts import (
    AUDIT_DIMENSIONS,
    AUDIT_DIMENSION_LABELS,
    AUDIT_QUESTION_HARD_LIMIT,
    AUDIT_QUESTION_TARGET,
    MAX_CONTEXT_INPUT_TOKENS,
    MAX_DISPLAY_TEXT_CHARS,
    MAX_OUTPUT_TOKENS,
    MAX_REFS_PER_ITEM,
    MAX_SYNTHESIS_OUTPUT_TOKENS,
    STEP_TIMEOUT_SECONDS,
    Assessment,
    AuditReport,
    AuditStatus,
    DimensionState,
    EndReason,
    EventType,
    MessageStage,
    MessageStatus,
    RoleCode,
    RunStatus,
    SenderKind,
    SessionPhase,
    StrictModel,
    TaskKind,
    canonical_json,
    new_id,
    utc_now_iso,
)
from domain.errors import AppError, ErrorCode
from services.context_builder import build_context
from pydantic import Field

__all__ = [
    "QUESTION_MAX_OUTPUT_TOKENS",
    "REPORT_MAX_OUTPUT_TOKENS",
    "MAX_REPAIR_ROUNDS",
    "QuestionDraft",
    "AnswerDraft",
    "ReportDraft",
    "build_dimension_states",
    "plan_next_dimension",
    "should_close",
    "normalize_assessment",
    "build_fallback_report",
    "render_report_message",
    "run_audit_open",
    "run_audit_answer",
    "run_audit_report",
]

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #

#: 单个问题生成的最大输出 token（开场与追问共用）。
QUESTION_MAX_OUTPUT_TOKENS = MAX_OUTPUT_TOKENS
#: 报告生成的最大输出 token（与主持人合成同档）。
REPORT_MAX_OUTPUT_TOKENS = MAX_SYNTHESIS_OUTPUT_TOKENS
#: 输出不合协议时最多回喂修复一次。
MAX_REPAIR_ROUNDS = 1

PURPOSE_OPEN = "audit.open"
PURPOSE_ANSWER = "audit.answer"
PURPOSE_REPORT = "audit.report"

#: 维度评估的"信息量"优先级：用于把同一维度的多次问答归并成一个结论。
_DIMENSION_PRIORITY: dict[Assessment, int] = {
    Assessment.supported: 40,
    Assessment.gap: 30,
    Assessment.needs_clarification: 20,
    Assessment.not_applicable: 10,
    Assessment.awaiting_answer: 5,
    Assessment.not_discussed: 0,
}

#: 允许由模型给出的评估结论（未讨论/等待回答对"已回答"的问题不成立）。
_MODEL_ASSESSMENTS = (
    Assessment.supported,
    Assessment.gap,
    Assessment.needs_clarification,
    Assessment.not_applicable,
)

REPAIR_INSTRUCTION = (
    "你上一次的输出没有被系统接受，原因是：{error}\n"
    "请只输出符合约定的严格 JSON 对象：不要 Markdown 围栏、不要解释文字、"
    "不要新增未定义的字段；无法确定的字段使用 null 或空数组。"
)


# --------------------------------------------------------------------------- #
# 模型输出协议（与 core.role_prompts.AUDIT_PROMPT 对齐，extra=forbid）
# --------------------------------------------------------------------------- #


class QuestionDraft(StrictModel):
    """一个审计问题（开场与追问共用同一协议）。"""

    dimension: str = Field(default="", max_length=48)
    question_text: str = Field(min_length=1, max_length=1000)
    issue_refs: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)
    evidence_refs: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)
    rationale: str = Field(default="", max_length=2000)


class AnswerDraft(StrictModel):
    """对用户回答的评估 + 可选的下一问。"""

    assessment: Assessment = Assessment.needs_clarification
    rationale: str = Field(default="", max_length=2000)
    evidence_refs: list[str] = Field(default_factory=list, max_length=MAX_REFS_PER_ITEM)
    next_question: QuestionDraft | None = None
    message: str = Field(default="", max_length=1000)


class ReportDraft(StrictModel):
    """结项报告的模型部分；无法确定的维度不得凭空补齐。"""

    supported_points: list[str] = Field(default_factory=list, max_length=16)
    unresolved_gaps: list[str] = Field(default_factory=list, max_length=16)
    recommended_experiments: list[str] = Field(default_factory=list, max_length=8)
    limitations: str = Field(default="", max_length=2000)
    summary: str = Field(default="", max_length=4000)


# --------------------------------------------------------------------------- #
# 纯函数：维度状态 / 计划 / 评估归一化 / 报告
# --------------------------------------------------------------------------- #


def _assessment_value(raw: Any) -> Assessment:
    """把 str / 枚举 / None 统一成 :class:`Assessment`（未知值视为未讨论）。"""

    if isinstance(raw, Assessment):
        return raw
    if raw is None:
        return Assessment.not_discussed
    try:
        return Assessment(str(raw))
    except ValueError:
        return Assessment.not_discussed


def _refs_of(row: Any) -> list[str]:
    """读取问题行上的 ``evidence_refs_json``（坏 JSON 退回空列表）。"""

    raw = getattr(row, "evidence_refs_json", None)
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return []
    else:
        data = raw
    if not isinstance(data, (list, tuple)):
        return []
    return [str(item) for item in data if str(item).strip()]


def build_dimension_states(rows: Iterable[Any]) -> list[DimensionState]:
    """把审计问题行归并成六个维度状态（顺序固定 = ``AUDIT_DIMENSIONS``）。

    同一维度有多个问题时，评估结论取"信息量最高"的那条（supported > gap >
    needs_clarification > not_applicable > awaiting_answer），同分取序号更大的
    （后问覆盖前问），保证纯函数、同输入必定同输出。
    """

    grouped: dict[str, list[Any]] = {key: [] for key in AUDIT_DIMENSIONS}
    for row in rows or []:
        key = str(getattr(row, "dimension", "") or "")
        if key in grouped:
            grouped[key].append(row)

    states: list[DimensionState] = []
    for key in AUDIT_DIMENSIONS:
        items = sorted(
            grouped[key],
            key=lambda row: (int(getattr(row, "ordinal", 0) or 0), str(getattr(row, "id", "") or "")),
        )
        asked = [
            str(getattr(row, "id"))
            for row in items
            if getattr(row, "id", None)
        ]
        answered = [
            str(getattr(row, "id"))
            for row in items
            if getattr(row, "id", None) and getattr(row, "answer_message_id", None)
        ]

        chosen: Any = None
        best = -1
        for row in items:
            score = _DIMENSION_PRIORITY.get(_assessment_value(getattr(row, "assessment", None)), 0)
            if score >= best:  # 同分取后出现的（ordinal 更大）
                best, chosen = score, row

        if chosen is None:
            states.append(DimensionState(key=key))
            continue

        states.append(
            DimensionState(
                key=key,
                asked_question_ids=asked,
                answered_question_ids=answered,
                assessment=_assessment_value(getattr(chosen, "assessment", None)),
                rationale=str(getattr(chosen, "rationale", "") or "")[:2000],
                evidence_refs=_refs_of(chosen)[:MAX_REFS_PER_ITEM],
            )
        )
    return states


def plan_next_dimension(states: Sequence[DimensionState]) -> str | None:
    """确定性问题计划：先补"从未讨论"的维度，再补"有缺口"的维度。

    维度顺序固定为 :data:`AUDIT_DIMENSIONS`，不依赖模型文本，也不依赖网络
    到达顺序；返回 ``None`` 表示六个维度都已有结论。
    """

    by_key = {state.key: state for state in states or []}
    for key in AUDIT_DIMENSIONS:
        state = by_key.get(key)
        if state is None or state.assessment is Assessment.not_discussed:
            return key
    for key in AUDIT_DIMENSIONS:
        state = by_key[key]
        if state.assessment in (Assessment.gap, Assessment.needs_clarification):
            return key
    return None


def should_close(*, ordinal: int, question_count: int, states: Sequence[DimensionState]) -> bool:
    """是否在本轮结束审计（进入 closing 并产报告）。

    规则（确定性）：
    * 第 ``AUDIT_QUESTION_HARD_LIMIT``(18) 问的回答被接收后必须收尾，禁止第 19 问；
    * 六个维度都已有结论时收尾；
    * 否则达到目标问题数 ``AUDIT_QUESTION_TARGET``(12) 时收尾。
    """

    if int(ordinal) >= AUDIT_QUESTION_HARD_LIMIT:
        return True
    if plan_next_dimension(states) is None:
        return True
    return int(question_count) >= AUDIT_QUESTION_TARGET


def normalize_assessment(
    assessment: Assessment,
    *,
    rationale: str,
    evidence_refs: Sequence[str],
    allowed_evidence: Iterable[str],
    warnings: list[str],
) -> tuple[Assessment, str, list[str]]:
    """按 FR-07 收敛模型给出的评估结论。

    * 只保留上下文里真实存在的 evidence_id（越权引用被丢弃并记 warning）；
    * ``supported`` 必须同时具备具体理由与至少一个有效证据，否则降级为
      ``needs_clarification``（"仅讨论过或口头声称不能自动升 supported"）；
    * 对"已回答"的问题，``not_discussed`` / ``awaiting_answer`` 不成立。
    """

    allowed = {str(item) for item in allowed_evidence or ()}
    kept = [str(ref) for ref in evidence_refs or [] if str(ref) in allowed]
    dropped = [str(ref) for ref in evidence_refs or [] if str(ref) not in allowed]
    if dropped:
        warnings.append("评估引用了上下文中不存在的证据 " + "、".join(dropped[:3]) + "，已忽略")

    value = _assessment_value(assessment)
    if value not in _MODEL_ASSESSMENTS:
        warnings.append("评估结论不能是未讨论/等待回答（用户已提交回答），已改为 needs_clarification")
        value = Assessment.needs_clarification

    text = str(rationale or "").strip()[:2000]
    if value is Assessment.supported and (not text or not kept):
        warnings.append("评估为 supported 但缺少具体理由或有效证据，已降级为 needs_clarification")
        value = Assessment.needs_clarification

    return value, text, kept[:MAX_REFS_PER_ITEM]


def _dimension_label(key: str) -> str:
    return AUDIT_DIMENSION_LABELS.get(str(key), str(key))


def _end_reason_of(audit: Any) -> EndReason:
    raw = getattr(audit, "end_reason", None)
    if raw is None:
        return EndReason.completed
    try:
        return EndReason(str(raw))
    except ValueError:
        return EndReason.completed


def _experiment_methods(product_state: Any) -> list[str]:
    methods: list[str] = []
    for item in getattr(product_state, "experiments", None) or []:
        method = str(getattr(item, "method", "") or "").strip()
        if method:
            methods.append(method)
        if len(methods) >= 8:
            break
    return methods


def build_fallback_report(
    *,
    audit: Any,
    states: Sequence[DimensionState],
    product_state: Any = None,
    note: str = "",
) -> AuditReport:
    """只用结构化记录生成"有限总结"（模型报告不可用时的确定性兜底）。

    不推断、不补齐任何未记录的评估；``generated_by="fallback"``，并在
    ``limitations`` 里明确说明来源，满足 PRD「明确标记的有限总结」。
    """

    supported: list[str] = []
    gaps: list[str] = []
    for state in states or []:
        label = _dimension_label(state.key)
        if state.assessment is Assessment.supported:
            supported.append(f"{label}：{state.rationale.strip() or '（未记录具体理由）'}")
        elif state.assessment in (Assessment.gap, Assessment.needs_clarification):
            gaps.append(f"{label}：{state.rationale.strip() or '尚未获得充分回答'}")
        elif state.assessment is Assessment.not_discussed:
            gaps.append(f"{label}：本次审计未覆盖")

    limitations = str(note or "").strip()
    if not limitations:
        limitations = (
            "模型报告不可用，本报告由结构化审计记录生成的有限总结："
            "只包含已被评估的维度，未讨论的维度不做推断。"
        )

    question_count = int(getattr(audit, "question_count", 0) or 0)
    question_count = max(0, min(question_count, AUDIT_QUESTION_HARD_LIMIT))
    return AuditReport(
        audit_id=str(getattr(audit, "id", "") or ""),
        product_version=int(getattr(audit, "product_version", 1) or 1),
        audit_version=int(getattr(audit, "audit_version", 1) or 1),
        end_reason=_end_reason_of(audit),
        question_count=question_count,
        dimension_states=list(states or []),
        supported_points=supported[:16],
        unresolved_gaps=gaps[:16],
        recommended_experiments=_experiment_methods(product_state)[:8],
        limitations=limitations[:2000],
        generated_by="fallback",
    )


def render_report_message(report: AuditReport) -> str:
    """把报告渲染成一条给用户看的消息（确定性、不含原始 JSON）。"""

    lines = [f"审计已完成，共 {int(report.question_count)} 个问题。"]
    if report.supported_points:
        lines.append("")
        lines.append("已支撑的结论：")
        lines.extend(f"- {item}" for item in report.supported_points)
    if report.unresolved_gaps:
        lines.append("")
        lines.append("仍未解决的缺口：")
        lines.extend(f"- {item}" for item in report.unresolved_gaps)
    if report.recommended_experiments:
        lines.append("")
        lines.append("建议验证：")
        lines.extend(f"- {item}" for item in report.recommended_experiments)
    if report.limitations:
        lines.append("")
        lines.append(f"局限：{report.limitations}")
    return "\n".join(lines)[:MAX_DISPLAY_TEXT_CHARS]


# --------------------------------------------------------------------------- #
# 仓储 / 命令服务（延迟 import，避免与 runner 形成环）
# --------------------------------------------------------------------------- #


def _audit_repo() -> Any:
    from repositories import audit_repo

    return audit_repo


def _run_repo() -> Any:
    from repositories import run_repo

    return run_repo


def _session_repo() -> Any:
    from repositories import session_repo

    return session_repo


def _command_service() -> Any:
    from services import command_service

    return command_service


def _llm_client_module() -> Any:
    from core import llm_client

    return llm_client


# --------------------------------------------------------------------------- #
# 通用小工具
# --------------------------------------------------------------------------- #


def _load_audit(db: Any, audit_id: str) -> Any:
    audit = _audit_repo().get_audit(db, audit_id)
    if audit is None:
        raise AppError(ErrorCode.NOT_FOUND, "审计不存在", details={"audit_id": audit_id})
    return audit


def _load_state(db: Any, session_id: str, product_version: int) -> Any:
    return _session_repo().get_product_state(db, session_id, version=int(product_version))


def _questions(db: Any, audit_id: str) -> list[Any]:
    return list(_audit_repo().questions_for_audit(db, audit_id))


def _current_message_id(ctx: Any) -> str | None:
    row = getattr(ctx, "current_message", None)
    return getattr(row, "id", None) if row is not None else None


def _run_id(ctx: Any) -> str:
    return str(getattr(getattr(ctx, "run", None), "id", "") or "")


def _session_id(ctx: Any) -> str:
    run = getattr(ctx, "run", None)
    return str(getattr(run, "session_id", "") or "")


def _run_status(ctx: Any) -> RunStatus | None:
    raw = getattr(getattr(ctx, "run", None), "status", None)
    try:
        return RunStatus(str(raw))
    except (TypeError, ValueError):
        return None


def _question_id_from_run(ctx: Any) -> str:
    """从 Run 行读取绑定问题 id（ctx.run 可能是过期快照）。"""

    run_id = _run_id(ctx)
    if not run_id:
        return ""
    try:
        row = _run_repo().get_run(ctx.db, run_id)
    except Exception:  # pragma: no cover - 读取失败按缺失处理
        return ""
    return str(getattr(row, "question_id", "") or "") if row is not None else ""


def _remaining_timeout(ctx: Any) -> float:
    deadline = getattr(ctx, "deadline_epoch", None)
    if deadline is None:
        return float(STEP_TIMEOUT_SECONDS)
    try:
        remaining = float(deadline) - time.time()
    except (TypeError, ValueError):
        return float(STEP_TIMEOUT_SECONDS)
    return max(5.0, min(float(STEP_TIMEOUT_SECONDS), remaining))


def _max_context_tokens(ctx: Any) -> int:
    budget = getattr(ctx, "budget", None)
    value = getattr(budget, "max_context_input_tokens", None)
    try:
        return int(value) if value else MAX_CONTEXT_INPUT_TOKENS
    except (TypeError, ValueError):
        return MAX_CONTEXT_INPUT_TOKENS


def _merge_usage(target: dict[str, Any], result: Any) -> None:
    usage = getattr(result, "usage", None)
    payload: dict[str, Any] = {}
    if usage is not None:
        if hasattr(usage, "as_dict"):
            payload = dict(usage.as_dict() or {})
        elif isinstance(usage, dict):
            payload = dict(usage)
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        try:
            target[key] = int(target.get(key, 0)) + int(payload.get(key, 0) or 0)
        except (TypeError, ValueError):
            continue
    target["calls"] = int(target.get("calls", 0)) + 1


def _format_validation_error(error: Exception) -> str:
    errors = getattr(error, "errors", None)
    if callable(errors):
        try:
            items = errors()
        except Exception:  # pragma: no cover - pydantic 内部异常
            items = []
        parts = []
        for item in list(items)[:6]:
            loc = ".".join(str(piece) for piece in item.get("loc", ()))
            parts.append(f"{loc}: {item.get('msg', '')}".strip(": "))
        if parts:
            return "；".join(parts)
    return str(error)[:600]


def _failed(
    ctx: Any,
    code: Any,
    message: str,
    *,
    warnings: Sequence[str] | None = None,
    events: Sequence[dict[str, Any]] | None = None,
    session_phase: SessionPhase | None = None,
    apply: Any = None,
    usage: dict[str, Any] | None = None,
) -> Any:
    cs = _command_service()
    return cs.RunOutcome(
        status=RunStatus.failed,
        error_code=str(code),
        error_message=str(message),
        warnings=list(warnings or []),
        events=list(events or []),
        session_phase=session_phase,
        brief_status=None,
        usage=usage or None,
        apply=apply,
    )


# --------------------------------------------------------------------------- #
# 上下文与模型调用
# --------------------------------------------------------------------------- #


def _context_messages(
    ctx: Any,
    *,
    task_kind: TaskKind,
    audit: Any,
    instruction: str,
    current_message_id: str | None = None,
) -> tuple[list[dict[str, Any]], Any]:
    """装配审计上下文；返回 ``(messages, bundle)``。

    审计的会话阶段与问题都通过 ``build_context`` 的审计分节注入；这里不再
    自行拼接历史消息，避免与 FR-02 的注入顺序产生第二套真相。
    """

    bundle = build_context(
        ctx.db,
        session_id=_session_id(ctx),
        task_kind=task_kind,
        role_code=RoleCode.interviewer,
        product_version=int(getattr(audit, "product_version", 1) or 1),
        current_message_id=current_message_id,
        audit_id=str(getattr(audit, "id", "")),
        prompt_version=PROMPT_VERSION,
        max_input_tokens=_max_context_tokens(ctx),
    )
    messages = build_role_messages(RoleCode.interviewer, bundle, PROMPT_VERSION)
    messages.append({"role": "user", "content": instruction})
    return messages, bundle


async def _complete_json(
    ctx: Any,
    *,
    messages: list[dict[str, Any]],
    schema: dict[str, Any],
    max_output_tokens: int,
    purpose: str,
) -> tuple[Any, Any]:
    llm = getattr(ctx, "llm", None)
    if llm is None or not hasattr(llm, "complete_json"):
        raise AppError(ErrorCode.INTERNAL, "缺少 LLM 客户端，无法执行审计")
    return await llm.complete_json(
        messages=messages,
        max_output_tokens=max_output_tokens,
        timeout_seconds=_remaining_timeout(ctx),
        purpose=purpose,
        json_schema=schema,
        temperature=0.2,
    )


async def _structured(
    ctx: Any,
    *,
    messages: list[dict[str, Any]],
    model_cls: Any,
    schema: dict[str, Any],
    max_output_tokens: int,
    purpose: str,
    label: str,
    warnings: list[str],
    usage: dict[str, Any],
) -> tuple[Any, list[dict[str, Any]]]:
    """调用模型并按 ``model_cls`` 严格校验；失败最多回喂修复一次。

    ``LLMOutputInvalidError``（非法 JSON）与 pydantic 校验失败都会回喂错误
    后重试一次，仍失败则抛 :class:`ValueError`（调用方转成 failed 结果）。
    """

    working = list(messages)
    last_error: Exception | None = None
    for attempt in range(MAX_REPAIR_ROUNDS + 1):
        parsed: Any = None
        try:
            parsed, result = await _complete_json(
                ctx,
                messages=working,
                schema=schema,
                max_output_tokens=max_output_tokens,
                purpose=purpose,
            )
            _merge_usage(usage, result)
            return model_cls.model_validate(parsed), working
        except ValidationError as error:
            last_error = error
            if attempt >= MAX_REPAIR_ROUNDS:
                raise ValueError(_format_validation_error(error)) from error
            detail = _format_validation_error(error)
            warnings.append(f"{label}输出不符合协议，已回喂校验错误修复一次：{detail[:200]}")
            working = working + [
                {"role": "assistant", "content": canonical_json(parsed)[:4000]},
                {"role": "user", "content": REPAIR_INSTRUCTION.replace("{error}", detail[:600])},
            ]
        except Exception as error:  # noqa: BLE001 - 只识别"输出不可解析"这一类
            if not _is_output_invalid(error):
                raise
            last_error = error
            if attempt >= MAX_REPAIR_ROUNDS:
                raise ValueError(str(error)[:600]) from error
            warnings.append(f"{label}输出不是合法 JSON，已回喂错误修复一次：{str(error)[:200]}")
            working = working + [
                {
                    "role": "user",
                    "content": REPAIR_INSTRUCTION.replace("{error}", str(error)[:600]),
                }
            ]
    raise ValueError(str(last_error)[:600])  # pragma: no cover - 循环必然返回或抛出


def _is_output_invalid(error: Exception) -> bool:
    try:
        cls = _llm_client_module().LLMOutputInvalidError
    except Exception:  # pragma: no cover - llm_client 不可用
        return False
    return isinstance(error, cls)


def _max_step_attempts() -> int:
    """runner 的单步骤上游尝试上限；延迟导入避免 workflows 包内循环依赖。"""

    try:
        from workflows import runner

        value = int(getattr(runner, "MAX_STEP_ATTEMPTS", 1))
    except Exception:  # pragma: no cover - runner 不可用
        return 1
    return max(1, value)


def _defer_transport_failure(ctx: Any, error: LLMError) -> bool:
    """可重试且仍有尝试额度：交回 runner 重试，此处不落库。

    与 runner 的 ``except LLMError`` 分支保持一致（``attempt_no`` 由 runner 通过
    :class:`workflows.runner.StepContext` 注入）；额度耗尽后由本模块把失败写进
    审计状态，避免「Run 失败但审计停在 opening/active」的两套状态脱节。
    """

    if not bool(getattr(error, "retryable", False)):
        return False
    attempt_no = int(getattr(ctx, "attempt_no", 1) or 1)
    return attempt_no < _max_step_attempts()


def _transport_failure_message(error: LLMError) -> str:
    """把上游错误转成用户可读的失败原因（保留原文里的处置提示，如「更新 API Key」）。"""

    text = str(error).strip() or "上游模型服务调用失败"
    return text[:300]


# --------------------------------------------------------------------------- #
# 守卫与 apply 工厂
# --------------------------------------------------------------------------- #


def _guarded_audit(
    db: Any,
    *,
    ctx: Any,
    audit_id: str,
    expected: Iterable[AuditStatus],
) -> Any | None:
    """提交时的原子守卫：状态不符 / Run 已取消 / 版本已过期 → 放弃写入。"""

    audit = _audit_repo().get_audit(db, audit_id)
    if audit is None:
        return None
    try:
        status = AuditStatus(str(audit.status))
    except ValueError:  # pragma: no cover - 数据损坏
        return None
    if status not in tuple(expected):
        return None

    run = _run_repo().get_run(db, _run_id(ctx))
    if run is None:
        return None
    if RunStatus(str(run.status)) is RunStatus.cancelled:
        return None
    base_version = getattr(run, "base_audit_version", None)
    if base_version is not None and int(base_version) != int(getattr(audit, "audit_version", 0) or 0):
        return None
    return audit


def _transition(current: Any, target: AuditStatus) -> None:
    """校验状态机路径（仅校验，不改行）。"""

    SM.validate_audit_transition(AuditStatus(str(current)), target)


def _apply_open(
    *,
    ctx: Any,
    audit_id: str,
    dimension: str,
    question_text: str,
    issue_refs: Sequence[str],
    question_message_id: str,
) -> Any:
    def _apply(db: Any) -> None:
        audit = _guarded_audit(
            db,
            ctx=ctx,
            audit_id=audit_id,
            expected=(AuditStatus.created, AuditStatus.opening, AuditStatus.failed),
        )
        if audit is None:
            return
        now = utc_now_iso()
        current = AuditStatus(str(audit.status))
        if current is not AuditStatus.opening:
            # created → opening 是首轮；failed → opening 是开场失败后的重放。
            _transition(current, AuditStatus.opening)

        repo = _audit_repo()
        row = repo.add_question(
            db,
            audit,
            ordinal=1,
            dimension=dimension,
            issue_refs=list(issue_refs),
            question_text=question_text,
            question_message_id=question_message_id,
            now_iso=now,
        )
        states = build_dimension_states(_questions(db, audit.id))
        repo.update_audit(
            db,
            audit,
            now_iso=now,
            status=AuditStatus.active,
            question_count=max(1, int(row.ordinal)),
            active_question_id=row.id,
            dimension_states=states,
            audit_version=int(audit.audit_version) + 1,
            pending_continuation=0,
        )

    return _apply


def _apply_open_failure(*, ctx: Any, audit_id: str) -> Any:
    def _apply(db: Any) -> None:
        audit = _guarded_audit(db, ctx=ctx, audit_id=audit_id, expected=(AuditStatus.opening,))
        if audit is None:
            return
        now = utc_now_iso()
        _transition(audit.status, AuditStatus.failed)
        # 失败不推进 audit_version（与 _apply_closing_failure 一致）：开场失败没有
        # 写入任何审计内容，且 retry_run 建子 Run 时会复制父 Run 的
        # base_audit_version，这里 bump 会让「重试该步骤」被
        # RETRY_AUDIT_CONTEXT_CHANGED 拒绝。重复写入由状态守卫
        # （opening → failed，成功后为 active）与 Run 的 base 版本校验兜住。
        _audit_repo().update_audit(
            db,
            audit,
            now_iso=now,
            status=AuditStatus.failed,
            end_reason=EndReason.failed,
            question_count=0,
            active_question_id=None,
        )

    return _apply


def _apply_closing_failure(*, ctx: Any, audit_id: str, code: Any) -> Any:
    """closing 阶段报告生成失败：closing → failed，并保留待完成的报告草稿。

    与 ``_apply_open_failure`` 的差别：
    - ``end_reason`` 记录稳定错误码（``ErrorCode`` 全大写），作为「报告可恢复」
      的判据，``AuditSnapshot`` 侧会降级为 ``failed``；
    - **不 bump** ``audit_version``：失败没有产出新报告，版本保持不变，
      ``retry_run`` 的 ``base_audit_version`` 校验才能继续复跑；
    - 不清 ``closing_control_id`` / ``report_json``：恢复后仍可生成报告。
    """

    def _apply(db: Any) -> None:
        audit = _guarded_audit(db, ctx=ctx, audit_id=audit_id, expected=(AuditStatus.closing,))
        if audit is None:
            return
        now = utc_now_iso()
        _transition(audit.status, AuditStatus.failed)
        _audit_repo().update_audit(
            db,
            audit,
            now_iso=now,
            status=AuditStatus.failed,
            end_reason=str(code) if code else EndReason.failed,
            active_question_id=None,
        )

    return _apply


def _apply_answer(
    *,
    ctx: Any,
    audit_id: str,
    question_id: str,
    assessment: Assessment,
    rationale: str,
    evidence_refs: Sequence[str],
    next_question: dict[str, Any] | None,
    report: AuditReport | None,
    generated_by: str,
) -> Any:
    """把评估 +（可选）下一问 +（可选）报告写进同一事务。"""

    def _apply(db: Any) -> None:
        audit = _guarded_audit(db, ctx=ctx, audit_id=audit_id, expected=(AuditStatus.active,))
        if audit is None:
            return
        repo = _audit_repo()
        question = repo.get_question(db, audit.id, question_id)
        if question is None or not getattr(question, "answer_message_id", None):
            return

        now = utc_now_iso()
        before = {
            "assessment": str(question.assessment),
            "rationale": str(question.rationale or ""),
            "evidence_refs": _refs_of(question),
        }
        new_version = int(audit.audit_version) + 1
        repo.record_assessment(
            db,
            question,
            assessment=assessment,
            rationale=rationale,
            evidence_refs=list(evidence_refs),
            now_iso=now,
        )
        repo.add_assessment_revision(
            db,
            audit_id=audit.id,
            audit_version=new_version,
            dimension=str(question.dimension),
            question_id=question.id,
            before=before,
            after={
                "assessment": assessment.value,
                "rationale": rationale,
                "evidence_refs": list(evidence_refs),
            },
            origin="model",
            source_message_ids=_source_ids(ctx),
            evidence_refs=list(evidence_refs),
            rationale=rationale,
            now_iso=now,
        )

        if next_question is not None:
            row = repo.add_question(
                db,
                audit,
                ordinal=int(question.ordinal) + 1,
                dimension=next_question["dimension"],
                issue_refs=list(next_question.get("issue_refs") or ()),
                question_text=next_question["question_text"],
                question_message_id=next_question.get("message_id"),
                now_iso=now,
            )
            states = build_dimension_states(_questions(db, audit.id))
            repo.update_audit(
                db,
                audit,
                now_iso=now,
                status=AuditStatus.active,
                question_count=max(int(audit.question_count or 0), int(row.ordinal)),
                active_question_id=row.id,
                dimension_states=states,
                audit_version=new_version,
                pending_continuation=0,
            )
            return

        # 收尾：active -> closing -> completed 一次写入（报告同事务落库）。
        _transition(audit.status, AuditStatus.closing)
        _transition(AuditStatus.closing, AuditStatus.completed)
        states = build_dimension_states(_questions(db, audit.id))
        final_report = report or build_fallback_report(audit=audit, states=states)
        final_report = final_report.model_copy(
            update={
                "audit_version": new_version,
                "question_count": int(audit.question_count or 0),
                "dimension_states": states,
            }
        )
        repo.save_report_revision(
            db,
            audit_id=audit.id,
            audit_version=new_version,
            report=final_report.model_dump(mode="json"),
            generated_by=generated_by,
            now_iso=now,
        )
        repo.update_audit(
            db,
            audit,
            now_iso=now,
            status=AuditStatus.completed,
            end_reason=_end_reason_of(audit),
            active_question_id=None,
            dimension_states=states,
            report=final_report.model_dump(mode="json"),
            audit_version=new_version,
            pending_continuation=0,
        )

    return _apply


def _apply_answer_failure(*, ctx: Any, audit_id: str) -> Any:
    """评估/下一问生成失败：保留已提交回答，标记待继续（PRD 异常映射表）。"""

    def _apply(db: Any) -> None:
        audit = _guarded_audit(db, ctx=ctx, audit_id=audit_id, expected=(AuditStatus.active,))
        if audit is None:
            return
        _audit_repo().update_audit(
            db,
            audit,
            now_iso=utc_now_iso(),
            active_question_id=None,
            pending_continuation=1,
            # 不推进 audit_version：失败没有写入评估/下一问，且 retry_run 的子 Run
            # 复制父 Run 的 base_audit_version，bump 会让「重试该步骤」被
            # RETRY_AUDIT_CONTEXT_CHANGED 拒绝（与 _apply_closing_failure 一致）。
        )

    return _apply


def _apply_report(
    *,
    ctx: Any,
    audit_id: str,
    report: AuditReport,
    generated_by: str,
) -> Any:
    def _apply(db: Any) -> None:
        audit = _guarded_audit(
            db,
            ctx=ctx,
            audit_id=audit_id,
            expected=(AuditStatus.closing, AuditStatus.failed),
        )
        if audit is None:
            return
        now = utc_now_iso()
        current = AuditStatus(str(audit.status))
        if current is AuditStatus.failed:
            _transition(current, AuditStatus.closing)
        _transition(AuditStatus.closing, AuditStatus.completed)

        repo = _audit_repo()
        new_version = int(audit.audit_version) + 1
        states = build_dimension_states(_questions(db, audit.id))
        final_report = report.model_copy(
            update={
                "audit_version": new_version,
                "question_count": int(audit.question_count or 0),
                "dimension_states": states,
            }
        )
        repo.save_report_revision(
            db,
            audit_id=audit.id,
            audit_version=new_version,
            report=final_report.model_dump(mode="json"),
            generated_by=generated_by,
            now_iso=now,
        )
        repo.update_audit(
            db,
            audit,
            now_iso=now,
            status=AuditStatus.completed,
            end_reason=_end_reason_of(audit),
            active_question_id=None,
            dimension_states=states,
            report=final_report.model_dump(mode="json"),
            audit_version=new_version,
            pending_continuation=0,
        )

    return _apply


def _source_ids(ctx: Any) -> list[str]:
    """评估修订的来源消息：当前输入（用户回答）优先。"""

    message_id = _current_message_id(ctx)
    return [message_id] if message_id else []


# --------------------------------------------------------------------------- #
# 指令文本
# --------------------------------------------------------------------------- #


def _open_instruction(audit: Any, *, dimension: str, issue_ids: Sequence[str]) -> str:
    known = "、".join(sorted(set(issue_ids))[:12]) or "无"
    return (
        "现在是审计开场。\n"
        f"产品版本：v{int(getattr(audit, 'product_version', 1) or 1)}\n"
        f"计划首先覆盖的维度：{_dimension_label(dimension)}（{dimension}）\n"
        "请提出第 1 个问题：指出该维度下最需要被审视的一个具体主张、证据缺口或未决假设。\n"
        f"可用的问题关联 id（issue_id / claim_id）：{known}。\n"
        "严格输出 JSON：dimension / question_text / issue_refs / evidence_refs / rationale，"
        "不要输出任何其他字段。"
    )


def _answer_instruction(
    audit: Any,
    *,
    question: Any,
    ordinal: int,
    closing: bool,
    next_dimension: str | None,
    issue_ids: Sequence[str],
) -> str:
    known = "、".join(sorted(set(issue_ids))[:12]) or "无"
    if closing:
        step2 = "这是本次审计的最后一个问题（已达目标或硬上限），不要再给出新问题，next_question 必须为 null。"
    else:
        step2 = (
            f"给出下一个问题：目标维度固定为「{_dimension_label(str(next_dimension))}」"
            f"（{next_dimension}），不要改用其他维度。"
        )
    return (
        f"用户在审计中回答了第 {ordinal} 个问题（维度：{_dimension_label(str(getattr(question, 'dimension', '')))}）。\n\n"
        "请按顺序完成两件事：\n"
        "1. 评估这个回答：assessment 取 supported / gap / needs_clarification / not_applicable；"
        "只有回答给出了具体理由且引用了上下文里真实存在的证据时才可判 supported。\n"
        f"2. {step2}\n\n"
        f"可用的问题关联 id（issue_id / claim_id）：{known}。\n"
        "严格输出 JSON：assessment / rationale / evidence_refs / next_question"
        "（含 dimension、question_text、issue_refs、evidence_refs、rationale，可为 null）/ message"
        "（可选的一两句确认语），不要输出任何其他字段。"
    )


def _report_instruction(
    audit: Any,
    *,
    states: Sequence[DimensionState],
    question_count: int,
) -> str:
    lines = [
        "请根据本次审计的结构化记录撰写结项报告。",
        f"产品版本：v{int(getattr(audit, 'product_version', 1) or 1)}；问题数：{int(question_count)}；"
        f"结束原因：{_end_reason_of(audit).value}",
        "各维度评估：",
    ]
    for state in states:
        label = _dimension_label(state.key)
        if state.assessment is Assessment.not_discussed:
            lines.append(f"- {label}：未讨论")
        else:
            detail = state.rationale.strip() or "（无理由记录）"
            lines.append(f"- {label}：{state.assessment.value} — {detail}")
    lines.extend(
        [
            "",
            "请输出 JSON：supported_points（只保留有证据支撑的结论，最多 16 条）/ "
            "unresolved_gaps（最多 16 条）/ recommended_experiments（最多 8 条）/ "
            "limitations（本次审计的局限，<=2000 字）/ summary（给用户的中文总结，<=4000 字）。",
            "不得凭空补齐未讨论维度的评价；若没有任何 supported 结论，supported_points 必须为空数组。",
        ]
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 报告生成
# --------------------------------------------------------------------------- #


def _apply_report_draft(
    draft: ReportDraft,
    *,
    audit: Any,
    states: Sequence[DimensionState],
    product_state: Any,
    warnings: list[str],
) -> AuditReport:
    """把模型报告与结构化记录对齐：不凭空虚增 supported 结论。"""

    has_supported = any(state.assessment is Assessment.supported for state in states)
    supported = [item.strip() for item in draft.supported_points if str(item).strip()][:16]
    if not has_supported and supported:
        warnings.append("没有任何维度被评估为 supported，已丢弃模型给出的已支撑结论")
        supported = []

    fallback = build_fallback_report(audit=audit, states=states, product_state=product_state)
    gaps = [item.strip() for item in draft.unresolved_gaps if str(item).strip()][:16] or fallback.unresolved_gaps
    experiments = (
        [item.strip() for item in draft.recommended_experiments if str(item).strip()][:8]
        or fallback.recommended_experiments
    )
    limitations = str(draft.limitations or "").strip()[:2000]
    if not has_supported:
        extra = "本次审计没有任何维度被评估为 supported，因此不列出已支撑结论。"
        limitations = (limitations + " " + extra).strip() if limitations else extra

    return AuditReport(
        audit_id=str(getattr(audit, "id", "") or ""),
        product_version=int(getattr(audit, "product_version", 1) or 1),
        audit_version=int(getattr(audit, "audit_version", 1) or 1),
        end_reason=_end_reason_of(audit),
        question_count=max(0, min(int(getattr(audit, "question_count", 0) or 0), AUDIT_QUESTION_HARD_LIMIT)),
        dimension_states=list(states),
        supported_points=supported,
        unresolved_gaps=gaps,
        recommended_experiments=experiments,
        limitations=limitations,
        generated_by="model",
    )


async def _generate_report(
    ctx: Any,
    *,
    audit: Any,
    states: Sequence[DimensionState],
    product_state: Any,
    warnings: list[str],
    usage: dict[str, Any],
    allow_model: bool = True,
) -> tuple[AuditReport, str]:
    """生成报告；模型不可用时回落到确定性「有限总结」。"""

    answered = any(state.answered_question_ids for state in states) or any(
        state.assessment is not Assessment.not_discussed and state.assessment is not Assessment.awaiting_answer
        for state in states
    )
    if not allow_model or not answered:
        note = ""
        if not answered:
            note = (
                "本次审计没有可用的结构化评估记录，"
                "因此只给出有限总结，不包含任何推断性结论。"
            )
        warnings.append("审计记录不足以支撑模型报告，已使用确定性有限总结")
        return build_fallback_report(audit=audit, states=states, product_state=product_state, note=note), "fallback"

    instruction = _report_instruction(audit, states=states, question_count=int(audit.question_count or 0))
    try:
        messages, _bundle = _context_messages(
            ctx,
            task_kind=TaskKind.audit_report,
            audit=audit,
            instruction=instruction,
            current_message_id=_current_message_id(ctx),
        )
        draft, _working = await _structured(
            ctx,
            messages=messages,
            model_cls=ReportDraft,
            schema=ReportDraft.model_json_schema(),
            max_output_tokens=REPORT_MAX_OUTPUT_TOKENS,
            purpose=PURPOSE_REPORT,
            label="审计报告",
            warnings=warnings,
            usage=usage,
        )
    except ValueError as error:
        warnings.append(f"审计报告生成失败，已使用确定性有限总结：{str(error)[:200]}")
        note = (
            "模型报告不可用（输出不合协议），本报告由结构化审计记录生成的有限总结："
            "只包含已被评估的维度，未讨论的维度不做推断。"
        )
        return build_fallback_report(audit=audit, states=states, product_state=product_state, note=note), "fallback"

    return _apply_report_draft(draft, audit=audit, states=states, product_state=product_state, warnings=warnings), "model"


# --------------------------------------------------------------------------- #
# 入口：开场
# --------------------------------------------------------------------------- #


async def run_audit_open(ctx: Any) -> Any:
    """审计开场：写入第 1 个问题并把审计推进到 active。"""

    audit_id = str(getattr(getattr(ctx, "run", None), "audit_id", "") or "")
    try:
        return await _open(ctx)
    except AppError as error:
        return _failed(ctx, error.code, error.message)
    except LLMError as error:
        if _defer_transport_failure(ctx, error):
            raise
        return _failed(
            ctx,
            error.code,
            _transport_failure_message(error),
            session_phase=SessionPhase.brainstorming,
            apply=_apply_open_failure(ctx=ctx, audit_id=audit_id) if audit_id else None,
        )


async def _open(ctx: Any) -> Any:
    cs = _command_service()
    audit_id = str(getattr(getattr(ctx, "run", None), "audit_id", "") or "")
    if not audit_id:
        return _failed(ctx, ErrorCode.INVALID_REQUEST, "审计开场缺少 audit_id")

    audit = _load_audit(ctx.db, audit_id)
    status = AuditStatus(str(audit.status))
    warnings: list[str] = []
    usage: dict[str, Any] = {}

    if _run_status(ctx) is RunStatus.cancel_requested:
        warnings.append("执行已收到取消请求，本次不再提问")
        return cs.RunOutcome(status=RunStatus.succeeded, warnings=warnings, session_phase=None)

    question_count = int(audit.question_count or 0)
    # 开场传输失败后审计落到 failed（question_count 仍为 0）。此时允许重放开场：
    # failed → opening 是状态机里的既有恢复路径（见 AUDIT_TRANSITIONS）。
    replay_failed_open = status is AuditStatus.failed and question_count == 0
    if status not in (AuditStatus.created, AuditStatus.opening) and not replay_failed_open:
        warnings.append(f"审计状态为 {status.value}，开场步骤不再重复执行")
        return cs.RunOutcome(status=RunStatus.succeeded, warnings=warnings, session_phase=None)
    if question_count > 0:
        warnings.append(f"审计状态为 {status.value}，开场步骤不再重复执行")
        return cs.RunOutcome(status=RunStatus.succeeded, warnings=warnings, session_phase=None)

    states = build_dimension_states(_questions(ctx.db, audit_id))
    dimension = plan_next_dimension(states) or AUDIT_DIMENSIONS[0]
    issue_ids = _known_issue_ids(ctx, audit)

    messages, bundle = _context_messages(
        ctx,
        task_kind=TaskKind.audit_open,
        audit=audit,
        instruction=_open_instruction(audit, dimension=dimension, issue_ids=issue_ids),
        current_message_id=_current_message_id(ctx),
    )

    try:
        draft, _working = await _structured(
            ctx,
            messages=messages,
            model_cls=QuestionDraft,
            schema=QuestionDraft.model_json_schema(),
            max_output_tokens=QUESTION_MAX_OUTPUT_TOKENS,
            purpose=PURPOSE_OPEN,
            label="审计开场问题",
            warnings=warnings,
            usage=usage,
        )
    except ValueError as error:
        warnings.append(f"审计开场问题两次未通过协议校验：{str(error)[:200]}")
        return _failed(
            ctx,
            ErrorCode.MODEL_OUTPUT_INVALID,
            "开场问题未能通过协议校验，审计未启动",
            warnings=warnings,
            usage=usage,
            session_phase=SessionPhase.brainstorming,
            apply=_apply_open_failure(ctx=ctx, audit_id=audit_id),
        )

    planned = dimension
    if draft.dimension and draft.dimension not in AUDIT_DIMENSIONS:
        warnings.append(f"模型给出的维度 {draft.dimension!r} 非法，已按计划维度 {planned} 提问")
    elif draft.dimension and draft.dimension != planned:
        warnings.append(f"模型建议维度 {draft.dimension} 与计划维度 {planned} 不一致，按计划维度提问")

    issue_refs = _valid_issue_refs(draft.issue_refs, issue_ids, warnings)
    question_text = draft.question_text.strip()[:1000]
    message_id = new_id()

    message = cs.OutgoingMessage(
        content=(
            f"我们开始一次结构化的产品评审，目标约 {AUDIT_QUESTION_TARGET} 个问题。\n\n{question_text}"
        )[:MAX_DISPLAY_TEXT_CHARS],
        sender_kind=SenderKind.agent,
        role_code=RoleCode.interviewer,
        stage=MessageStage.audit,
        status=MessageStatus.completed,
        audit_id=audit_id,
        message_id=message_id,
    )
    events = [
        {
            "type": EventType.audit_updated.value,
            "payload": {
                "audit_id": audit_id,
                "status": AuditStatus.active.value,
                "question_count": 1,
                "ordinal": 1,
                "dimension": planned,
            },
        }
    ]
    return cs.RunOutcome(
        status=RunStatus.succeeded,
        messages=[message],
        events=events,
        warnings=warnings,
        usage=usage or None,
        session_phase=SessionPhase.auditing,
        brief_status=None,
        apply=_apply_open(
            ctx=ctx,
            audit_id=audit_id,
            dimension=planned,
            question_text=question_text,
            issue_refs=issue_refs,
            question_message_id=message_id,
        ),
    )


# --------------------------------------------------------------------------- #
# 入口：回答
# --------------------------------------------------------------------------- #


async def run_audit_answer(ctx: Any) -> Any:
    """评估一个已回答的问题，并生成下一个问题或收尾报告。"""

    audit_id = str(getattr(getattr(ctx, "run", None), "audit_id", "") or "")
    try:
        return await _answer(ctx)
    except AppError as error:
        return _failed(ctx, error.code, error.message)
    except LLMError as error:
        if _defer_transport_failure(ctx, error):
            raise
        return _failed(
            ctx,
            error.code,
            _transport_failure_message(error),
            session_phase=SessionPhase.auditing,
            apply=_apply_answer_failure(ctx=ctx, audit_id=audit_id) if audit_id else None,
        )


async def _answer(ctx: Any) -> Any:
    cs = _command_service()
    run = getattr(ctx, "run", None)
    audit_id = str(getattr(run, "audit_id", "") or "")
    question_id = str(getattr(run, "question_id", "") or "") or str(getattr(ctx, "question_id", "") or "")
    if audit_id and not question_id:
        # 兜底：运行上下文可能只带 run.id（重放/恢复路径），从 Run 行重新读取绑定问题。
        question_id = _question_id_from_run(ctx)
    if not audit_id or not question_id:
        return _failed(ctx, ErrorCode.INVALID_REQUEST, "回答评估缺少 audit_id 或 question_id")

    audit = _load_audit(ctx.db, audit_id)
    status = AuditStatus(str(audit.status))
    warnings: list[str] = []
    usage: dict[str, Any] = {}

    if _run_status(ctx) is RunStatus.cancel_requested:
        warnings.append("执行已收到取消请求，本次不再写入评估或新问题")
        return cs.RunOutcome(status=RunStatus.succeeded, warnings=warnings, session_phase=None)

    if status is not AuditStatus.active:
        warnings.append(f"审计状态为 {status.value}，本次回答不再写入评估或新问题")
        return cs.RunOutcome(status=RunStatus.succeeded, warnings=warnings, session_phase=None)

    question = _audit_repo().get_question(ctx.db, audit_id, question_id)
    if question is None:
        return _failed(ctx, ErrorCode.NOT_FOUND, "审计问题不存在", usage=usage)
    if not getattr(question, "answer_message_id", None):
        warnings.append("该问题还没有已提交的回答，本次不生成评估")
        return cs.RunOutcome(status=RunStatus.succeeded, warnings=warnings, session_phase=None)

    ordinal = int(getattr(question, "ordinal", 0) or 0)
    state = _load_state(ctx.db, _session_id(ctx), int(audit.product_version))
    issue_ids = _known_issue_ids(ctx, audit)

    states_before = build_dimension_states(_questions(ctx.db, audit_id))
    preview = _preview_states(states_before, question, assessment=Assessment.supported)
    closing = should_close(ordinal=ordinal, question_count=int(audit.question_count or 0), states=preview)
    next_dimension = None if closing else plan_next_dimension(preview)

    messages, bundle = _context_messages(
        ctx,
        task_kind=TaskKind.audit_answer,
        audit=audit,
        instruction=_answer_instruction(
            audit,
            question=question,
            ordinal=ordinal,
            closing=closing,
            next_dimension=next_dimension,
            issue_ids=issue_ids,
        ),
        current_message_id=_current_message_id(ctx),
    )

    try:
        draft, _working = await _structured(
            ctx,
            messages=messages,
            model_cls=AnswerDraft,
            schema=AnswerDraft.model_json_schema(),
            max_output_tokens=QUESTION_MAX_OUTPUT_TOKENS,
            purpose=PURPOSE_ANSWER,
            label="审计评估",
            warnings=warnings,
            usage=usage,
        )
    except ValueError as error:
        warnings.append(f"审计评估两次未通过协议校验：{str(error)[:200]}")
        return _failed(
            ctx,
            ErrorCode.MODEL_OUTPUT_INVALID,
            "回答已保存，但评估或下一问生成失败，可重试继续",
            warnings=warnings,
            usage=usage,
            session_phase=SessionPhase.auditing,
            apply=_apply_answer_failure(ctx=ctx, audit_id=audit_id),
        )

    assessment, rationale, evidence_refs = normalize_assessment(
        draft.assessment,
        rationale=draft.rationale,
        evidence_refs=draft.evidence_refs,
        allowed_evidence=getattr(bundle, "evidence_refs", []) or [],
        warnings=warnings,
    )

    message_id = new_id()
    report: AuditReport | None = None
    report_message = ""
    next_question: dict[str, Any] | None = None

    if closing:
        states_after = _preview_states(states_before, question, assessment=assessment)
        report, generated_by = await _generate_report(
            ctx,
            audit=audit,
            states=states_after,
            product_state=state,
            warnings=warnings,
            usage=usage,
        )
        content = render_report_message(report)
    else:
        generated_by = "n/a"
        content = _next_question_text(
            draft,
            audit=audit,
            dimension=str(next_dimension),
            issue_ids=issue_ids,
            warnings=warnings,
        )
        next_question = {
            "dimension": str(next_dimension),
            "question_text": content,
            "issue_refs": _valid_issue_refs(
                draft.next_question.issue_refs if draft.next_question else [], issue_ids, warnings
            ),
            "message_id": message_id,
        }

    message = cs.OutgoingMessage(
        content=content,
        sender_kind=SenderKind.agent,
        role_code=RoleCode.interviewer,
        stage=MessageStage.audit,
        status=MessageStatus.completed,
        audit_id=audit_id,
        message_id=message_id,
    )
    if closing:
        event_payload: dict[str, Any] = {
            "audit_id": audit_id,
            "status": AuditStatus.completed.value,
            "question_count": int(audit.question_count or 0),
            "end_reason": _end_reason_of(audit).value,
            "generated_by": generated_by,
        }
    else:
        event_payload = {
            "audit_id": audit_id,
            "status": AuditStatus.active.value,
            "question_count": ordinal + 1,
            "ordinal": ordinal + 1,
            "dimension": str(next_dimension),
            "assessed_ordinal": ordinal,
            "assessment": assessment.value,
        }

    return cs.RunOutcome(
        status=RunStatus.succeeded,
        messages=[message],
        events=[{"type": EventType.audit_updated.value, "payload": event_payload}],
        warnings=warnings,
        usage=usage or None,
        session_phase=SessionPhase.audit_completed if closing else SessionPhase.auditing,
        brief_status=None,
        apply=_apply_answer(
            ctx=ctx,
            audit_id=audit_id,
            question_id=question_id,
            assessment=assessment,
            rationale=rationale,
            evidence_refs=evidence_refs,
            next_question=next_question,
            report=report,
            generated_by=generated_by,
        ),
    )


def _preview_states(
    states: Sequence[DimensionState],
    question: Any,
    *,
    assessment: Assessment,
) -> list[DimensionState]:
    """在内存里把当前问题的评估合进维度状态（用于确定收尾/计划）。"""

    dimension = str(getattr(question, "dimension", "") or "")
    preview: list[DimensionState] = []
    for state in states:
        if state.key != dimension:
            preview.append(state)
            continue
        asked = list(state.asked_question_ids)
        if question.id not in asked:
            asked.append(str(question.id))
        answered = list(state.answered_question_ids)
        if question.id not in answered:
            answered.append(str(question.id))
        preview.append(
            state.model_copy(
                update={
                    "asked_question_ids": asked,
                    "answered_question_ids": answered,
                    "assessment": assessment,
                }
            )
        )
    return preview


def _next_question_text(
    draft: AnswerDraft,
    *,
    audit: Any,
    dimension: str,
    issue_ids: Sequence[str],
    warnings: list[str],
) -> str:
    """下一问的正文：优先模型输出，缺省用确定性模板（维度来自计划）。"""

    candidate = draft.next_question.question_text if draft.next_question else ""
    text = str(candidate or "").strip()
    if text:
        return text[:1000]
    label = _dimension_label(dimension)
    warnings.append(f"模型没有给出可用的下一个问题，已按计划维度「{label}」使用模板问题")
    return (
        f"关于「{label}」，目前还有哪个关键假设没有被验证？"
        "请给出你的判断依据，以及可以低成本验证它的办法。"
    )[:1000]


def _known_issue_ids(ctx: Any, audit: Any) -> list[str]:
    """上下文中可被引用的 issue_id / claim_id（评审问题关联用）。"""

    try:
        state = _load_state(ctx.db, _session_id(ctx), int(audit.product_version))
    except Exception:  # pragma: no cover - 读取失败时退化为空引用
        return []
    ids: list[str] = []
    try:
        ids.extend(str(item) for item in state.claim_ids())
    except Exception:  # pragma: no cover - 模型实例缺失方法
        pass
    for issue in getattr(state, "unresolved_issues", None) or []:
        issue_id = getattr(issue, "id", None)
        if issue_id:
            ids.append(str(issue_id))
    return list(dict.fromkeys(ids))


def _valid_issue_refs(refs: Sequence[str], known: Sequence[str], warnings: list[str]) -> list[str]:
    allowed = {str(item) for item in known}
    kept = [str(ref) for ref in refs or [] if str(ref) in allowed]
    dropped = [str(ref) for ref in refs or [] if str(ref) not in allowed]
    if dropped:
        warnings.append("问题引用了上下文中不存在的 issue_id/claim_id " + "、".join(dropped[:3]) + "，已忽略")
    return kept[:MAX_REFS_PER_ITEM]


# --------------------------------------------------------------------------- #
# 入口：报告
# --------------------------------------------------------------------------- #


async def run_audit_report(ctx: Any) -> Any:
    """基于结构化审计记录生成结项报告（closing -> completed）。"""

    try:
        return await _report(ctx)
    except AppError as error:
        return _failed(
            ctx,
            error.code,
            error.message,
            session_phase=SessionPhase.auditing,
            apply=_apply_closing_failure(
                ctx=ctx,
                audit_id=str(getattr(getattr(ctx, "run", None), "audit_id", "") or ""),
                code=error.code,
            ),
        )


async def _report(ctx: Any) -> Any:
    cs = _command_service()
    run = getattr(ctx, "run", None)
    audit_id = str(getattr(run, "audit_id", "") or "")
    if not audit_id:
        return _failed(ctx, ErrorCode.INVALID_REQUEST, "报告生成缺少 audit_id")

    audit = _load_audit(ctx.db, audit_id)
    status = AuditStatus(str(audit.status))
    warnings: list[str] = []
    usage: dict[str, Any] = {}

    if _run_status(ctx) is RunStatus.cancel_requested:
        warnings.append("取消请求优先于报告生成，本次不写报告")
        return cs.RunOutcome(status=RunStatus.succeeded, warnings=warnings, session_phase=None)

    if status is AuditStatus.completed:
        warnings.append("报告已经生成，重复请求不再重写")
        return cs.RunOutcome(status=RunStatus.succeeded, warnings=warnings, session_phase=None)
    if status is AuditStatus.cancelled:
        warnings.append("审计已取消，不生成报告")
        return cs.RunOutcome(status=RunStatus.succeeded, warnings=warnings, session_phase=None)
    if status not in (AuditStatus.closing, AuditStatus.failed):
        warnings.append(f"审计状态为 {status.value}，不生成报告")
        return cs.RunOutcome(status=RunStatus.succeeded, warnings=warnings, session_phase=None)

    states = build_dimension_states(_questions(ctx.db, audit_id))
    product_state = _load_state(ctx.db, _session_id(ctx), int(audit.product_version))
    report, generated_by = await _generate_report(
        ctx,
        audit=audit,
        states=states,
        product_state=product_state,
        warnings=warnings,
        usage=usage,
    )

    content = render_report_message(report)
    message = cs.OutgoingMessage(
        content=content,
        sender_kind=SenderKind.agent,
        role_code=RoleCode.interviewer,
        stage=MessageStage.audit,
        status=MessageStatus.completed,
        audit_id=audit_id,
        message_id=new_id(),
    )
    events = [
        {
            "type": EventType.audit_updated.value,
            "payload": {
                "audit_id": audit_id,
                "status": AuditStatus.completed.value,
                "question_count": int(audit.question_count or 0),
                "end_reason": _end_reason_of(audit).value,
                "generated_by": generated_by,
            },
        }
    ]
    return cs.RunOutcome(
        status=RunStatus.succeeded,
        messages=[message],
        events=events,
        warnings=warnings,
        usage=usage or None,
        session_phase=SessionPhase.audit_completed,
        brief_status=None,
        apply=_apply_report(ctx=ctx, audit_id=audit_id, report=report, generated_by=generated_by),
    )