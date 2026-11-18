"""M2 四角色 brainstorm 工作流（FR-04B / FR-04C）。

一次 ``TaskKind.brainstorm`` 运行会：

1. 用 :func:`services.reducer.select_roles` 按 PRD 的确定性规则决定本轮跑哪些角色
   （全员 / 指定 / 自动分配）；
2. **逐角色独立执行**：每个角色一个 step（``logical_key=brainstorm.role.<role>``，
   ``kind=role_draft``），各自用提交前历史快照 + 同一 ``base_product_version``
   构建上下文——因此**四角色首轮互不可见**（同轮产物只在本进程内存里，不落库、
   不进任何角色的上下文）；
3. 用 ``ctx.llm.complete_json(json_schema=RoleResult)`` 取结构化输出并做严格校验；
   校验失败**最多修复一次**（回喂错误），仍失败则该角色 step ``failed``、其余继续；
4. 成功角色的候选用 ``run_repo.reserve_candidate_id`` 登记到 staging
   (``(run_id, step_id, local_id) → 服务器 ID``)，映射写进 ``step.output_json``；
   **原始 JSON 绝不进产品消息**，消息正文只有 ``display_text``；
5. 交给 :func:`services.reducer.merge_role_results` 确定性归并，产出
   ``product_state(version+1)`` 与 ``product_state.updated`` 事件。

失败语义：

* 全部角色都失败 → ``RunOutcome(status=failed, error_code=MODEL_OUTPUT_INVALID)``；
* 部分角色失败 → ``partial_succeeded``，成功角色的结果照常合并；
* 预算超限 → ``failed(BUDGET_EXCEEDED)``（不再继续调用模型）；
* 其余 ``LLMError``（限流/不可用/超时）**向上抛**，交给 runner 的重试与 run 终结逻辑。

本模块**不提交**产品状态/消息/事件——这些由 ``RunOutcome`` 交给 runner 的事务提交；
只把 staging（step 与候选登记）写进短事务，避免依赖 runner 的长事务。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from domain.contracts import (
    MAX_CONTEXT_INPUT_TOKENS,
    MAX_OUTPUT_TOKENS,
    SCHEMA_VERSION,
    STEP_TIMEOUT_SECONDS,
    EventType,
    MessageStage,
    MessageStatus,
    ProductState,
    RoleCode,
    RoleResult,
    RunStatus,
    SenderKind,
    StepStatus,
    TaskKind,
    TargetRole,
    canonical_json,
    new_id,
    utc_now_iso,
)
from domain.errors import AppError, ErrorCode
from services.context_builder import build_context
from services.reducer import (
    RoleOutput,
    merge_role_results,
    role_logical_key,
    select_roles,
)

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #

#: 每角色 step 的 kind（与 runner 的粗粒度 step 区分开）。
ROLE_STEP_KIND = "role_draft"

#: 每次角色调用允许的最大输出 token（RoleResult 有硬上限，留些余量）。
ROLE_MAX_OUTPUT_TOKENS = MAX_OUTPUT_TOKENS

#: 修复轮只允许一次（PRD FR-04C："最多修复一次"）。
MAX_REPAIR_ROUNDS = 1

#: 修复指令模板；``{error}`` 用 ``str.replace`` 填充（避免 format 与 JSON 大括号冲突）。
REPAIR_INSTRUCTION = (
    "上一次输出无法通过校验：{error}\n"
    "请**只**输出一个符合约定 JSON Schema 的 JSON 对象，不要写解释、不要加代码围栏；"
    "字段名必须与协议完全一致，不得出现协议外的字段；"
    "local_id 必须在本结果内唯一，方案/实验里引用的 local_id 必须指向本结果中存在的条目。"
)

#: 允许的 msg 安全输出（防止把整个 RoleResult 塞进消息）。
_PURPOSE_PREFIX = "brainstorm.role"


# --------------------------------------------------------------------------- #
# 结果容器
# --------------------------------------------------------------------------- #


@dataclass
class _RoleRun:
    """单个角色的执行结果（成功时携带结构化产物与 staging 映射）。"""

    role_code: RoleCode
    step_id: str
    result: RoleResult | None = None
    stage_map: dict[str, dict[str, str]] = field(default_factory=dict)
    warning: str | None = None
    error: str | None = None
    calls: int = 0


# --------------------------------------------------------------------------- #
# 上下文 / 预算辅助
# --------------------------------------------------------------------------- #


def _run_id(ctx: Any) -> str | None:
    run = getattr(ctx, "run", None)
    return getattr(run, "id", None)


def _session_id(ctx: Any, state: ProductState) -> str:
    session = getattr(ctx, "session", None)
    return getattr(session, "id", None) or state.session_id


def _current_message(ctx: Any) -> Any:
    return getattr(ctx, "current_message", None)


def _brainstorm_content(ctx: Any) -> str:
    """智能分配规则读的输入：优先当前用户消息，退化为会话问题陈述。"""

    message = _current_message(ctx)
    content = getattr(message, "content", None)
    if isinstance(content, str) and content.strip():
        return content
    session = getattr(ctx, "session", None)
    problem = getattr(session, "problem_statement", None)
    return problem if isinstance(problem, str) else ""


def _resolve_target(ctx: Any) -> TargetRole:
    """解析本轮角色分配目标。

    ``RunRow`` 目前不落 ``BrainstormPayload.target``（命令层只保存 ``input_hash``），
    因此这里按"ctx 属性 → run 属性/预算附加字段 → auto"的顺序兜底。命令层将来把
    target 写进 run 的 budget 附加字段（``{"target": ...}``）即可零改动接通。
    """

    for holder in (ctx, getattr(ctx, "run", None), getattr(ctx, "budget", None)):
        if holder is None:
            continue
        for attr in ("target", "target_role", "clarify_target"):
            value = getattr(holder, attr, None)
            if value is not None:
                return value if isinstance(value, TargetRole) else _target_from_text(value)

    run = getattr(ctx, "run", None)
    raw_budget = getattr(run, "budget_json", None)
    if isinstance(raw_budget, str) and raw_budget:
        try:
            import json

            payload = json.loads(raw_budget)
        except (ValueError, TypeError):
            payload = {}
        if isinstance(payload, dict):
            value = payload.get("target") or payload.get("target_role")
            if value:
                return _target_from_text(value)
    return TargetRole.auto


def _target_from_text(value: Any) -> TargetRole:
    try:
        return TargetRole(str(value).strip().lower())
    except ValueError:
        return TargetRole.auto


def _max_context_tokens(ctx: Any) -> int:
    budget = getattr(ctx, "budget", None)
    value = getattr(budget, "max_context_input_tokens", None)
    return int(value) if isinstance(value, int) and value > 0 else MAX_CONTEXT_INPUT_TOKENS


def _remaining_timeout(ctx: Any) -> float:
    deadline = getattr(ctx, "deadline_epoch", None)
    if isinstance(deadline, (int, float)):
        remaining = float(deadline) - time.time()
    else:
        remaining = float(STEP_TIMEOUT_SECONDS)
    return max(5.0, min(float(STEP_TIMEOUT_SECONDS), remaining))


def _ensure_budget(ctx: Any, required_calls: int) -> None:
    """调用前检查剩余上游调用数；不足抛 ``BudgetExceededError``。"""

    budget = getattr(ctx, "budget", None)
    checker = getattr(budget, "ensure_required_calls", None)
    if callable(checker):
        checker(required_calls, purpose="brainstorm")


def _usage_from(result: Any) -> dict[str, Any]:
    usage = getattr(result, "usage", None)
    as_dict = getattr(usage, "as_dict", None)
    if callable(as_dict):
        return dict(as_dict())
    if isinstance(usage, dict):
        return dict(usage)
    return {}


def _merge_usage(total: dict[str, Any], part: dict[str, Any]) -> dict[str, Any]:
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = part.get(key)
        if isinstance(value, int):
            total[key] = int(total.get(key, 0)) + value
    if part.get("usage_source") and not total.get("usage_source"):
        total["usage_source"] = part["usage_source"]
    return total


# --------------------------------------------------------------------------- #
# staging（step 与候选登记，走短事务）
# --------------------------------------------------------------------------- #


def _staging_scope():
    """短事务：runner 的 ``ctx.db`` 从不提交，staging 必须自己落盘。"""

    from repositories.database import session_scope

    return session_scope()


def _require_run(db: Any, run_id: str) -> Any:
    from repositories import run_repo

    run = run_repo.get_run(db, run_id)
    if run is None:
        raise AppError(ErrorCode.NOT_FOUND, "运行记录不存在，无法执行 brainstorm", details={"run_id": run_id})
    return run


def _begin_role_step(run_id: str, role_code: RoleCode, *, product_version: int) -> str:
    """幂等建 step 并推到 running（独立短事务），返回 step_id。"""

    from repositories import run_repo

    with _staging_scope() as db:
        run = _require_run(db, run_id)
        step = run_repo.ensure_step(
            db,
            run,
            logical_key=role_logical_key(role_code),
            kind=ROLE_STEP_KIND,
            role_code=role_code,
            input_hash=run.input_hash,
            input_snapshot={"role_code": role_code.value, "base_product_version": product_version},
        )
        run_repo.start_step(db, step)
        return step.id


def _finish_role_step(
    run_id: str,
    step_id: str,
    *,
    status: Any,
    output: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    from repositories import run_repo

    with _staging_scope() as db:
        step = run_repo.get_step(db, step_id)
        if step is None:  # pragma: no cover - 同进程内不应消失
            return
        run_repo.finish_step(
            db,
            step,
            status=status,
            output=output,
            error_code=error_code,
            error_message=error_message,
        )


def _register_candidates(
    run_id: str,
    step_id: str,
    role_code: RoleCode,
    result: RoleResult,
) -> dict[str, dict[str, str]]:
    """把三类候选登记进 staging，返回 ``local_id → {"id","kind"}``。"""

    from repositories import run_repo

    mapping: dict[str, dict[str, str]] = {}
    with _staging_scope() as db:
        for candidate, kind in (
            *((item, "claim") for item in result.claims),
            *((item, "option") for item in result.option_proposals),
            *((item, "experiment") for item in result.suggested_experiments),
        ):
            server_id = run_repo.reserve_candidate_id(
                db,
                run_id=run_id,
                step_id=step_id,
                local_id=candidate.local_id,
                kind=kind,
                payload=candidate.model_dump(mode="json"),
            )
            mapping[candidate.local_id] = {"id": server_id, "kind": kind}
    return mapping


# --------------------------------------------------------------------------- #
# 模型调用与修复
# --------------------------------------------------------------------------- #


def _validate_role_result(parsed: Any, role_code: RoleCode) -> RoleResult:
    """严格校验：字段协议 + 角色一致性（未知角色一律拒绝）。"""

    result = RoleResult.model_validate(parsed)
    if result.role_code != role_code:
        raise ValueError(
            f"role_code 与当前角色不一致：期望 {role_code.value}，实际 {result.role_code}"
        )
    return result


def _coerce_metadata(result: RoleResult, role_code: RoleCode, warnings: list[str]) -> RoleResult:
    """模型漏标的元数据做最小补齐（不因此丢掉整个角色产物）。"""

    if role_code is RoleCode.target_user and not result.simulated_perspective:
        warnings.append(
            "目标用户角色未标记 simulated_perspective，已按协议补齐（模拟视角必须显式声明）"
        )
        return result.model_copy(update={"simulated_perspective": True})
    if result.schema_version != SCHEMA_VERSION:
        warnings.append(f"角色产物 schema_version={result.schema_version} 与当前版本不一致，已按 v{SCHEMA_VERSION} 接受")
        return result.model_copy(update={"schema_version": SCHEMA_VERSION})
    return result


def _format_validation_error(exc: BaseException) -> str:
    errors = getattr(exc, "errors", None)
    if callable(errors):
        try:
            parts = []
            for error in errors()[:6]:
                location = ".".join(str(part) for part in error.get("loc", ())) or "<root>"
                parts.append(f"{location}: {error.get('msg')}")
            text = "; ".join(parts)
        except Exception:  # pragma: no cover - 防御性
            text = ""
        if text:
            return text[:600]
    return str(exc)[:600]


async def _complete_role(
    ctx: Any,
    messages: list[dict[str, Any]],
    role_code: RoleCode,
) -> tuple[Any, Any]:
    """调用一次 ``complete_json``，返回 ``(parsed, llm_result)``。"""

    llm = getattr(ctx, "llm", None)
    if llm is None:
        raise AppError(ErrorCode.INTERNAL, "缺少 LLM 客户端，无法执行角色评审")
    complete_json = getattr(llm, "complete_json", None)
    if not callable(complete_json):
        raise AppError(ErrorCode.INTERNAL, "LLM 客户端不支持结构化输出（缺少 complete_json）")
    return await complete_json(
        messages=messages,
        max_output_tokens=ROLE_MAX_OUTPUT_TOKENS,
        timeout_seconds=_remaining_timeout(ctx),
        purpose=f"{_PURPOSE_PREFIX}.{role_code.value}",
        json_schema=RoleResult.model_json_schema(),
        temperature=0.2,
    )


async def _generate_role_result(
    ctx: Any,
    *,
    role_code: RoleCode,
    messages: list[dict[str, Any]],
    warnings: list[str],
    label: str,
) -> tuple[RoleResult, Any, int]:
    """取一个角色的结构化产物：首轮 + 至多一次修复。返回 (result, llm_result, calls)。"""

    calls = 0
    conversation = list(messages)
    last_error: Exception | None = None
    parsed: Any = None

    for attempt in range(MAX_REPAIR_ROUNDS + 1):
        calls += 1
        try:
            parsed, llm_result = await _complete_role(ctx, conversation, role_code)
        except _output_invalid_error() as exc:
            last_error = exc
            if attempt >= MAX_REPAIR_ROUNDS:
                break
            warnings.append(f"{label} 首次输出不是合法 JSON，已回喂错误修复一次：{str(exc)[:200]}")
            conversation = conversation + [
                {"role": "user", "content": REPAIR_INSTRUCTION.replace("{error}", _format_validation_error(exc))}
            ]
            continue

        try:
            result = _validate_role_result(parsed, role_code)
        except Exception as exc:  # pydantic ValidationError / role 不一致
            last_error = exc
            if attempt >= MAX_REPAIR_ROUNDS:
                break
            warnings.append(
                f"{label} 首次输出不符合协议，已回喂校验错误修复一次：{_format_validation_error(exc)}"
            )
            conversation = conversation + [
                {"role": "assistant", "content": canonical_json(parsed)[:4000]},
                {
                    "role": "user",
                    "content": REPAIR_INSTRUCTION.replace("{error}", _format_validation_error(exc)),
                },
            ]
            continue

        return _coerce_metadata(result, role_code, warnings), llm_result, calls

    error = last_error or ValueError("角色输出不可用")
    raise ValueError(_format_validation_error(error)) from error


def _output_invalid_error() -> type[BaseException]:
    """延迟取 ``LLMOutputInvalidError``，避免测试替身没装 llm_client 时 import 失败。"""

    from core.llm_client import LLMOutputInvalidError

    return LLMOutputInvalidError


# --------------------------------------------------------------------------- #
# 单角色执行
# --------------------------------------------------------------------------- #


def _role_messages(ctx: Any, role_code: RoleCode, state: ProductState) -> list[dict[str, Any]]:
    """为角色组装 system+上下文消息（不含本轮其他角色产物 → 首轮互不可见）。"""

    from core.role_prompts import PROMPT_VERSION, build_role_messages

    message = _current_message(ctx)
    bundle = build_context(
        getattr(ctx, "db", None),
        session_id=_session_id(ctx, state),
        task_kind=TaskKind.brainstorm,
        role_code=role_code,
        product_version=state.version,
        current_message_id=getattr(message, "id", None) if message is not None else None,
        prompt_version=PROMPT_VERSION,
        max_input_tokens=_max_context_tokens(ctx),
    )
    return build_role_messages(role_code, bundle, PROMPT_VERSION)


async def _run_role(
    ctx: Any,
    *,
    role_code: RoleCode,
    state: ProductState,
    run_id: str,
    warnings: list[str],
    usage: dict[str, Any],
) -> _RoleRun:
    """跑一个角色：建 step → 组装上下文 → 调模型（含修复）→ 登记候选 → 收尾 step。"""

    label = f"角色 {role_code.value}"
    step_id = _begin_role_step(run_id, role_code, product_version=state.version)
    outcome = _RoleRun(role_code=role_code, step_id=step_id)

    try:
        messages = _role_messages(ctx, role_code, state)
        result, llm_result, calls = await _generate_role_result(
            ctx, role_code=role_code, messages=messages, warnings=warnings, label=label
        )
    except ValueError as exc:
        outcome.error = str(exc)[:1000]
        _finish_role_step(
            run_id,
            step_id,
            status=StepStatus.failed,
            error_code=ErrorCode.MODEL_OUTPUT_INVALID,
            error_message=outcome.error,
        )
        warnings.append(f"{label} 修复后仍未通过校验，该角色本轮失败：{outcome.error}")
        return outcome

    outcome.calls = calls
    try:
        outcome.stage_map = _register_candidates(run_id, step_id, role_code, result)
    except AppError as exc:  # pragma: no cover - staging 写入失败属于基础设施问题
        _finish_role_step(
            run_id,
            step_id,
            status=StepStatus.failed,
            error_code=str(exc.code),
            error_message=exc.message,
        )
        raise

    outcome.result = result
    _merge_usage(usage, _usage_from(llm_result))
    _finish_role_step(
        run_id,
        step_id,
        status=StepStatus.succeeded,
        output={
            "role_code": role_code.value,
            "logical_key": role_logical_key(role_code),
            "product_version": state.version,
            "candidates": outcome.stage_map,
            "counts": {
                "claims": len(result.claims),
                "options": len(result.option_proposals),
                "experiments": len(result.suggested_experiments),
            },
        },
    )
    return outcome


# --------------------------------------------------------------------------- #
# workflow 入口
# --------------------------------------------------------------------------- #


def _role_message(outcome: _RoleRun) -> Any:
    """把角色 display_text 变成一条对外消息（**只含 display_text**）。"""

    from services.command_service import OutgoingMessage

    assert outcome.result is not None
    text = outcome.result.display_text.strip()
    return OutgoingMessage(
        content=text,
        sender_kind=SenderKind.agent,
        role_code=outcome.role_code,
        stage=MessageStage.brainstorm,
        status=MessageStatus.completed,
    )


def _id_factory_from(stage_maps: dict[RoleCode, dict[str, dict[str, str]]]):
    """归并时的 ID 工厂：优先复用 staging 已登记的服务器 ID。"""

    def factory(kind: str, local_id: str, role_code: str) -> str:
        mapped = stage_maps.get(RoleCode(role_code), {}).get(local_id)
        if mapped:
            return mapped["id"]
        return new_id()

    return factory


async def run_brainstorm(ctx: Any):
    """执行一轮四角色 brainstorm，返回 ``RunOutcome``（不提交事务）。"""

    from services.command_service import RunOutcome

    state = getattr(ctx, "product_state", None)
    if not isinstance(state, ProductState):
        return RunOutcome(
            status=RunStatus.failed,
            error_code=ErrorCode.INVALID_REQUEST,
            error_message="缺少产品状态快照，无法执行角色评审",
        )

    run_id = _run_id(ctx)
    if not run_id:
        return RunOutcome(
            status=RunStatus.failed,
            error_code=ErrorCode.INVALID_REQUEST,
            error_message="缺少运行上下文，无法执行角色评审",
        )

    warnings: list[str] = []
    usage: dict[str, Any] = {}
    content = _brainstorm_content(ctx)
    target = _resolve_target(ctx)
    roles = select_roles(target, content, state)
    warnings.append(f"本轮角色分配：{'、'.join(role.value for role in roles)}（target={_target_value(target)}）")

    try:
        _ensure_budget(ctx, len(roles))
    except AppError as exc:
        return RunOutcome(
            status=RunStatus.failed,
            error_code=str(exc.code),
            error_message=str(exc.message or exc),
            warnings=warnings,
            usage=usage,
            session_phase=None,
            brief_status=None,
        )

    role_runs: list[_RoleRun] = []
    for role_code in roles:
        try:
            role_runs.append(
                await _run_role(
                    ctx,
                    role_code=role_code,
                    state=state,
                    run_id=run_id,
                    warnings=warnings,
                    usage=usage,
                )
            )
        except AppError as exc:
            # 预算/上下文类终止性错误：停止本轮，不再调用模型
            warnings.append(f"角色 {role_code.value} 中止：{str(exc.message or exc)[:200]}")
            return RunOutcome(
                status=RunStatus.failed,
                error_code=str(exc.code),
                error_message=str(exc.message or exc),
                warnings=warnings,
                usage=usage,
                session_phase=None,
                brief_status=None,
            )

    succeeded = [run for run in role_runs if run.result is not None]
    failed = [run for run in role_runs if run.result is None]

    events: list[dict[str, Any]] = []
    for run in role_runs:
        events.append(
            {
                "type": EventType.role_completed.value,
                "payload": {
                    "role_code": run.role_code.value,
                    "step_id": run.step_id,
                    "status": "succeeded" if run.result is not None else "failed",
                    "claim_count": len(run.result.claims) if run.result is not None else 0,
                    "option_count": len(run.result.option_proposals) if run.result is not None else 0,
                },
            }
        )

    if not succeeded:
        return RunOutcome(
            status=RunStatus.failed,
            error_code=ErrorCode.MODEL_OUTPUT_INVALID,
            error_message="所有角色的输出都未通过校验，本轮没有可合并的产物",
            warnings=warnings,
            usage=usage,
            events=events,
            session_phase=None,
            brief_status=None,
        )

    messages: list[Any] = []
    for run in succeeded:
        message = _role_message(run)
        if message.content:
            messages.append(message)
        else:
            warnings.append(f"角色 {run.role_code.value} 的 display_text 为空，已跳过对外消息")

    stage_maps = {run.role_code: run.stage_map for run in succeeded}
    outputs = [
        RoleOutput(
            role_code=run.role_code,
            logical_key=role_logical_key(run.role_code),
            result=run.result,
        )
        for run in succeeded
        if run.result is not None
    ]
    merged = merge_role_results(
        state,
        outputs,
        run_id=run_id,
        id_factory=_id_factory_from(stage_maps),
    )

    payload = merged.model_dump(mode="json")
    payload.update(
        {
            "version": state.version + 1,
            "created_at": utc_now_iso(),
            "source_run_id": run_id,
            "schema_version": SCHEMA_VERSION,
        }
    )
    new_state = ProductState.model_validate(payload)

    events.append(
        {
            "type": EventType.product_state_updated.value,
            "payload": {"product_version": new_state.version},
        }
    )

    status = RunStatus.succeeded if not failed else RunStatus.partial_succeeded
    if failed:
        warnings.append(
            f"{len(failed)} 个角色本轮失败（{'、'.join(run.role_code.value for run in failed)}），"
            "其余角色的产物已合并"
        )

    return RunOutcome(
        status=status,
        product_state=new_state,
        messages=messages,
        events=events,
        result_product_version=new_state.version,
        warnings=warnings,
        usage=usage,
        session_phase=None,
        brief_status=None,
    )


def _target_value(target: TargetRole) -> str:
    return target.value if isinstance(target, TargetRole) else str(target)


__all__ = [
    "ROLE_STEP_KIND",
    "ROLE_MAX_OUTPUT_TOKENS",
    "MAX_REPAIR_ROUNDS",
    "REPAIR_INSTRUCTION",
    "run_brainstorm",
]
