"""Loop Studio 五阶段闭环工作流（契约 §5.3）。

一次 ``TaskKind.loop_round`` 运行按固定顺序跑五个阶段：

``ideate`` → ``validate`` → ``develop`` → ``test`` → ``evaluate``

每个阶段都是独立 step（``logical_key=loop.stage.<stage>``、``kind=loop_stage``），
用一次 ``complete_json`` 取结构化产物，结构失败最多修复一次；阶段之间只通过
**摘要**传递上下文（原始 JSON 不回流）。``evaluate`` 之后用
:mod:`services.loop_harness` 的确定性规则覆盖模型自评分与结论。

事务纪律：本模块**不提交**产品状态 / 消息 / 事件（交给 ``RunOutcome`` 与 runner 的
同事务提交），只把 staging（step 行、阶段结果、产物）写进独立短事务，因此中途崩溃后
可以用同一 Run 重放：已完成的阶段会从 ``loop_stage_results`` 还原，不再重复调模型。

失败语义：

* 缺少 ``loop_id`` / ``loop_round`` 预算参数 → ``failed(INVALID_REQUEST)``；
* 预算不足（本轮至少需要 5 次上游调用）→ ``failed(BUDGET_EXCEEDED)``，不写 product_state；
* 某阶段结构输出始终无法通过校验 → ``failed(MODEL_OUTPUT_INVALID)``；
* 某阶段确定性校验给出阻塞项 → 该阶段 step ``failed``，但**继续执行后续阶段**，
  Run 最终为 ``partial_succeeded``；
* 其余 ``LLMError``（限流/不可用）向上抛，交给 runner 的重试逻辑。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from domain.contracts import (
    SCHEMA_VERSION,
    STEP_TIMEOUT_SECONDS,
    Claim,
    ClaimKind,
    ClaimStatus,
    DecisionItem,
    DecisionStatus,
    EventType,
    FeaturePriority,
    IssueStatus,
    LoopArtifactKind,
    LoopMvpSpec,
    LoopRoundStatus,
    LoopScoreCard,
    LoopStage,
    LoopStageStatus,
    LoopStatus,
    LoopTestReport,
    LoopUpdatedPayload,
    LoopValidation,
    LoopVerdict,
    MessageStage,
    MessageStatus,
    OptionItem,
    OriginKind,
    ProductState,
    PrototypeSpec,
    RunStatus,
    SenderKind,
    StepStatus,
    TaskKind,
    UnresolvedIssue,
    canonical_json,
    new_id,
    utc_now_iso,
)
from domain.errors import AppError, ErrorCode
from services import loop_harness
from services.command_service import OutgoingMessage, RunOutcome

#: 五阶段 step 的 kind（与 runner 的粗粒度 step 区分开）。
LOOP_STEP_KIND = "loop_stage"

#: 每次结构修复最多一次（与契约 §5.3 一致）。
MAX_REPAIR_ROUNDS = 1

#: 一轮闭环的最小上游调用数（五阶段各一次）；不足直接失败，不浪费预算。
REQUIRED_UPSTREAM_CALLS = 5

#: 修复指令模板；``{error}`` 用 ``str.replace`` 填充（避免 format 与 JSON 大括号冲突）。
REPAIR_INSTRUCTION = (
    "上一次输出无法通过校验：{error}\n"
    "请**只**输出一个符合约定 JSON Schema 的 JSON 对象，不要写解释、不要加代码围栏；"
    "字段名必须与协议完全一致，不得出现协议外的字段；"
    "所有 id 必须唯一且引用本产物内存在的条目。"
)

#: 阶段 → step logical_key（固定，不可改）。
STAGE_KEYS: dict[LoopStage, str] = {
    stage: f"loop.stage.{stage.value}" for stage in LoopStage
}

#: 阶段 → 产物类型。
STAGE_ARTIFACT_KINDS: dict[LoopStage, LoopArtifactKind] = {
    LoopStage.ideate: LoopArtifactKind.mvp_spec,
    LoopStage.validate: LoopArtifactKind.validation,
    LoopStage.develop: LoopArtifactKind.prototype,
    LoopStage.test: LoopArtifactKind.test_report,
    LoopStage.evaluate: LoopArtifactKind.scorecard,
}

#: 产物标题（前端展示用）。
STAGE_ARTIFACT_TITLES: dict[LoopStage, str] = {
    LoopStage.ideate: "MVP 规格",
    LoopStage.validate: "验证记录",
    LoopStage.develop: "可运行原型",
    LoopStage.test: "测试报告",
    LoopStage.evaluate: "评分卡",
}

#: 阶段 → 结构化输出模型。
STAGE_MODELS: dict[LoopStage, type] = {
    LoopStage.ideate: LoopMvpSpec,
    LoopStage.validate: LoopValidation,
    LoopStage.develop: PrototypeSpec,
    LoopStage.test: LoopTestReport,
    LoopStage.evaluate: LoopScoreCard,
}

#: 阶段中文名；与提示词共用同一套叫法。
_STAGE_LABELS: dict[LoopStage, str] = {
    LoopStage.ideate: "方案构思",
    LoopStage.validate: "假设验证",
    LoopStage.develop: "原型开发",
    LoopStage.test: "测试执行",
    LoopStage.evaluate: "评分评审",
}

_VERDICT_LABELS: dict[LoopVerdict, str] = {
    LoopVerdict.pass_: "达标",
    LoopVerdict.iterate: "需要下一轮",
    LoopVerdict.blocked: "受阻",
    LoopVerdict.pending: "待评分",
}


# --------------------------------------------------------------------------- #
# 参数 / 结果容器
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _LoopParams:
    """从 ``Run.loop_id`` 与 ``Run.budget_json`` 解析出的本轮参数。"""

    loop_id: str
    ordinal: int
    max_rounds: int
    target_score: int
    focus: str
    stage_tokens: int
    final_tokens: int


@dataclass
class _StageRun:
    """单个阶段的执行结果。"""

    stage: LoopStage
    step_id: str | None = None
    artifact_id: str | None = None
    parsed: Any = None
    blockers: list[str] = field(default_factory=list)
    summary: str = ""
    error: str | None = None
    calls: int = 0
    reused: bool = False

    @property
    def ok(self) -> bool:
        return self.parsed is not None

    @property
    def status(self) -> LoopStageStatus:
        if not self.ok:
            return LoopStageStatus.failed
        return LoopStageStatus.failed if self.blockers else LoopStageStatus.done


# --------------------------------------------------------------------------- #
# 基础辅助
# --------------------------------------------------------------------------- #


def _staging_scope():
    """短事务：runner 的 ``ctx.db`` 从不提交，staging 必须自己落盘。"""

    from repositories.database import session_scope

    return session_scope()


def _run_id(ctx: Any) -> str | None:
    return getattr(getattr(ctx, "run", None), "id", None)


def _session_id(ctx: Any, state: ProductState) -> str:
    session = getattr(ctx, "session", None)
    return getattr(session, "id", None) or state.session_id


def _session_version(ctx: Any) -> int:
    session = getattr(ctx, "session", None)
    value = getattr(session, "session_version", None)
    return int(value) if isinstance(value, int) else 1


def _current_message(ctx: Any) -> Any:
    return getattr(ctx, "current_message", None)


def _current_message_id(ctx: Any) -> list[str]:
    message = _current_message(ctx)
    message_id = getattr(message, "id", None)
    return [message_id] if isinstance(message_id, str) and message_id else []


def _loop_budget_value(ctx: Any, key: str) -> Any:
    """先读 ``ctx.budget``（契约要求），再回退到 ``Run.budget_json``。

    :class:`workflows.runner.BudgetTracker` 只保留白名单预算键，``loop_id`` 之类的
    附加参数会被丢弃，因此必须同时读原始 budget JSON。
    """

    budget = getattr(ctx, "budget", None)
    if isinstance(budget, dict) and key in budget:
        return budget[key]
    if budget is not None and hasattr(budget, key):
        return getattr(budget, key)
    raw = getattr(getattr(ctx, "run", None), "budget_json", None)
    if raw:
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            data = {}
        if isinstance(data, dict) and key in data:
            return data[key]
    return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _resolve_params(ctx: Any) -> _LoopParams | None:
    run = getattr(ctx, "run", None)
    loop_id = getattr(run, "loop_id", None) or _loop_budget_value(ctx, "loop_id")
    ordinal = _as_int(_loop_budget_value(ctx, "loop_round"))
    if not isinstance(loop_id, str) or not loop_id or ordinal is None:
        return None
    max_rounds = _as_int(_loop_budget_value(ctx, "loop_max_rounds")) or 3
    target_score = _as_int(_loop_budget_value(ctx, "loop_target_score")) or 85
    focus = str(_loop_budget_value(ctx, "loop_focus") or "")
    stage_tokens = _as_int(_loop_budget_value(ctx, "loop_stage_output_tokens")) or 1500
    final_tokens = _as_int(_loop_budget_value(ctx, "loop_final_output_tokens")) or 2200
    return _LoopParams(
        loop_id=loop_id,
        ordinal=max(1, ordinal),
        max_rounds=max(1, max_rounds),
        target_score=max(0, min(100, target_score)),
        focus=focus,
        stage_tokens=max(64, stage_tokens),
        final_tokens=max(64, final_tokens),
    )


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
        checker(required_calls, purpose="loop.round")


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


def _clip(text: Any, limit: int) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def _output_invalid_error() -> type[BaseException]:
    from core.llm_client import LLMOutputInvalidError

    return LLMOutputInvalidError


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


# --------------------------------------------------------------------------- #
# staging 读写
# --------------------------------------------------------------------------- #


def _read_loop_state(loop_id: str) -> dict[str, Any] | None:
    from repositories import loop_repo

    with _staging_scope() as db:
        row = loop_repo.get_loop(db, loop_id)
    if row is None:
        return None
    return {
        "status": row.status,
        "current_round": int(row.current_round or 1),
        "loop_version": int(row.loop_version or 1),
        "max_rounds": int(row.max_rounds or 3),
        "target_score": int(row.target_score or 85),
    }


def _begin_stage_step(
    ctx: Any,
    *,
    stage: LoopStage,
    params: _LoopParams,
    prior_stages: list[str],
) -> str:
    """幂等建阶段 step 并推到 running（独立短事务），返回 step_id。"""

    from repositories import run_repo

    run_id = _run_id(ctx)
    session_id = _session_id(ctx, getattr(ctx, "product_state"))
    now_iso = utc_now_iso()
    with _staging_scope() as db:
        run = run_repo.get_run(db, run_id)
        if run is None:
            raise AppError(
                ErrorCode.NOT_FOUND, "运行记录不存在，无法执行 loop 阶段"
            )
        step = run_repo.ensure_step(
            db,
            run,
            logical_key=STAGE_KEYS[stage],
            kind=LOOP_STEP_KIND,
            input_hash=run.input_hash,
            input_snapshot={
                "loop_id": params.loop_id,
                "round_ordinal": params.ordinal,
                "stage": stage.value,
                "prior_stages": list(prior_stages),
            },
            now_iso=now_iso,
        )
        run_repo.start_step(db, step, now_iso=now_iso)
        run_repo.append_event(
            db,
            session_id=session_id,
            run_id=run_id,
            type=EventType.step_started.value,
            payload={
                "logical_key": STAGE_KEYS[stage],
                "stage": stage.value,
                "attempt": int(step.attempt_count or 1),
            },
            now_iso=now_iso,
        )
        return step.id


def _finish_stage(
    ctx: Any,
    *,
    step_id: str,
    stage: LoopStage,
    params: _LoopParams,
    parsed: Any,
    blockers: list[str],
    summary: str,
    error_code: str | None = None,
    error_message: str | None = None,
) -> str | None:
    """收尾阶段：finish step + 记录阶段结果 + 落产物（同一短事务）。"""

    from repositories import loop_repo, run_repo

    loop_id = params.loop_id
    ordinal = params.ordinal
    now_iso = utc_now_iso()
    artifact_kind = STAGE_ARTIFACT_KINDS[stage]
    step_status = StepStatus.failed if (blockers or parsed is None) else StepStatus.succeeded
    payload = parsed.model_dump(mode="json") if parsed is not None else None
    artifact_id: str | None = None

    with _staging_scope() as db:
        step = run_repo.get_step(db, step_id)
        if step is not None:
            run_repo.finish_step(
                db,
                step,
                status=step_status,
                output={
                    "stage": stage.value,
                    "artifact_kind": artifact_kind.value,
                    "blocker_count": len(blockers),
                },
                error_code=error_code,
                error_message=(error_message or ("；".join(blockers)[:1000] if blockers else None)),
                now_iso=now_iso,
            )
        loop_repo.record_stage(
            db,
            loop_id=loop_id,
            ordinal=ordinal,
            stage=stage,
            status=(
                LoopStageStatus.done if (parsed is not None and not blockers) else LoopStageStatus.failed
            ),
            summary=summary[:600],
            payload=payload,
            error_code=error_code,
            now_iso=now_iso,
        )
        if payload is not None:
            row = loop_repo.add_artifact(
                db,
                loop_id=loop_id,
                ordinal=ordinal,
                kind=artifact_kind,
                title=STAGE_ARTIFACT_TITLES[stage],
                payload=payload,
                now_iso=now_iso,
            )
            artifact_id = row.id
    return artifact_id


def _load_completed_stages(
    loop_id: str, ordinal: int
) -> dict[LoopStage, tuple[Any, str | None]]:
    """回放：从已落盘的阶段结果还原已成功的阶段（不重复调模型）。"""

    from repositories import loop_repo

    completed: dict[LoopStage, tuple[Any, str | None]] = {}
    with _staging_scope() as db:
        stage_rows = loop_repo.list_stages(db, loop_id=loop_id, ordinal=ordinal)
        artifact_rows = loop_repo.list_artifacts(db, loop_id=loop_id, ordinal=ordinal)
    artifact_ids = {row.kind: row.id for row in artifact_rows}
    for row in stage_rows:
        if row.status != LoopStageStatus.done.value:
            continue
        try:
            stage = LoopStage(row.stage)
            payload = json.loads(row.payload_json or "{}")
            parsed = STAGE_MODELS[stage].model_validate(payload)
        except Exception:  # pragma: no cover - 数据损坏时跳过，按未完成重跑
            continue
        completed[stage] = (parsed, artifact_ids.get(STAGE_ARTIFACT_KINDS[stage].value))
    return completed


# --------------------------------------------------------------------------- #
# 模型调用与修复
# --------------------------------------------------------------------------- #


async def _complete_stage(
    ctx: Any,
    *,
    stage: LoopStage,
    messages: list[dict[str, Any]],
    params: _LoopParams,
) -> tuple[Any, Any]:
    llm = getattr(ctx, "llm", None)
    if llm is None:
        raise AppError(ErrorCode.INTERNAL, "缺少 LLM 客户端，无法执行 loop 阶段")
    complete_json = getattr(llm, "complete_json", None)
    if not callable(complete_json):
        raise AppError(
            ErrorCode.INTERNAL, "LLM 客户端不支持结构化输出（缺少 complete_json）"
        )
    model = STAGE_MODELS[stage]
    token_budget = params.final_tokens if stage is LoopStage.evaluate else params.stage_tokens
    return await complete_json(
        messages=messages,
        max_output_tokens=token_budget,
        timeout_seconds=_remaining_timeout(ctx),
        purpose=_stage_purpose(stage),
        json_schema=model.model_json_schema(),
        temperature=0.2,
    )


def _stage_purpose(stage: LoopStage) -> str:
    from core.loop_prompts import STAGE_PURPOSE

    return STAGE_PURPOSE[stage]


async def _generate_stage(
    ctx: Any,
    *,
    stage: LoopStage,
    messages: list[Any],
    params: _LoopParams,
    warnings: list[str],
    usage: dict[str, Any],
) -> tuple[Any, int]:
    """取一个阶段的结构化产物：首轮 + 至多一次修复。返回 (模型对象, calls)。"""

    model = STAGE_MODELS[stage]
    label = f"阶段「{_STAGE_LABELS[stage]}」"
    conversation: list[dict[str, Any]] = [
        {"role": message.role, "content": message.content} for message in messages
    ]
    calls = 0
    last_error: Exception | None = None

    for attempt in range(MAX_REPAIR_ROUNDS + 1):
        _ensure_budget(ctx, 1)
        calls += 1
        try:
            parsed, llm_result = await _complete_stage(
                ctx, stage=stage, messages=conversation, params=params
            )
        except _output_invalid_error() as exc:
            last_error = exc
            if attempt >= MAX_REPAIR_ROUNDS:
                break
            warnings.append(
                f"{label}首次输出不是合法 JSON，已回喂错误修复一次：{str(exc)[:200]}"
            )
            conversation = conversation + [
                {
                    "role": "user",
                    "content": REPAIR_INSTRUCTION.replace(
                        "{error}", _format_validation_error(exc)
                    ),
                }
            ]
            continue

        _merge_usage(usage, _usage_from(llm_result))
        try:
            value = model.model_validate(parsed)
        except Exception as exc:  # pydantic ValidationError
            last_error = exc
            if attempt >= MAX_REPAIR_ROUNDS:
                break
            warnings.append(
                f"{label}首次输出不符合协议，已回喂校验错误修复一次：{_format_validation_error(exc)}"
            )
            conversation = conversation + [
                {"role": "assistant", "content": canonical_json(parsed)[:4000]},
                {
                    "role": "user",
                    "content": REPAIR_INSTRUCTION.replace(
                        "{error}", _format_validation_error(exc)
                    ),
                },
            ]
            continue

        return value, calls

    error = last_error or ValueError("阶段输出不可用")
    raise ValueError(_format_validation_error(error)) from error


# --------------------------------------------------------------------------- #
# 确定性校验 / 评分
# --------------------------------------------------------------------------- #


def _deterministic_blockers(
    stage: LoopStage, parsed: Any, spec: LoopMvpSpec | None
) -> list[str]:
    try:
        if stage is LoopStage.ideate:
            return loop_harness.validate_mvp_spec(parsed)
        if stage is LoopStage.evaluate:
            return []
        if spec is None:
            return ["缺少 MVP 规格，无法完成该阶段的确定性校验"]
        if stage is LoopStage.validate:
            return loop_harness.validate_validation(parsed, spec)
        if stage is LoopStage.develop:
            return loop_harness.validate_prototype(parsed, spec)
        if stage is LoopStage.test:
            return loop_harness.validate_test_report(parsed, spec)
    except Exception as exc:  # pragma: no cover - 校验器自身异常按阻塞处理
        return [f"确定性校验失败：{_clip(str(exc), 200)}"]
    return []


def _finalize_scorecard(
    card: LoopScoreCard, *, stage_blockers: list[str], params: _LoopParams
) -> LoopScoreCard:
    """用硬规则覆盖模型自评分与结论。"""

    merged = [*stage_blockers, *[str(item) for item in (card.blockers or [])]]
    clamped = loop_harness.clamp_score(
        card,
        blockers=merged,
        p0=len(stage_blockers),
        p1=len(list(card.blockers or [])),
    )
    verdict = loop_harness.derive_verdict(
        clamped,
        target_score=params.target_score,
        round_ordinal=params.ordinal,
        max_rounds=params.max_rounds,
    )
    next_actions = [str(item) for item in (clamped.next_actions or [])]
    if verdict != LoopVerdict.pass_ and params.ordinal >= params.max_rounds:
        note = "轮次已用尽，请决定交付或停止"
        if note not in next_actions:
            next_actions.append(note)
    return clamped.model_copy(
        update={"verdict": verdict, "next_actions": next_actions[:6]}
    )


def _stage_summary(stage: LoopStage, parsed: Any, blockers: list[str]) -> str:
    if stage is LoopStage.ideate:
        features = list(getattr(parsed, "features", []) or [])
        must = sum(1 for feature in features if feature.priority == FeaturePriority.must)
        text = f"MVP：{_clip(parsed.title, 120)}，{len(features)} 个功能（must {must}）"
    elif stage is LoopStage.validate:
        checks = list(getattr(parsed, "checks", []) or [])
        passed = sum(1 for check in checks if check.result.value == "pass")
        unknown = sum(1 for check in checks if check.result.value == "unknown")
        failed = sum(1 for check in checks if check.result.value == "fail")
        text = f"验证 {len(checks)} 项：pass {passed}、unknown {unknown}、fail {failed}"
    elif stage is LoopStage.develop:
        files = list(getattr(parsed, "files", []) or [])
        text = f"原型 {len(files)} 个文件，入口 {_clip(parsed.entry_file, 120)}"
    elif stage is LoopStage.test:
        text = (
            f"用例 {int(parsed.total)} 个：passed {int(parsed.passed)}、"
            f"failed {int(parsed.failed)}、skipped {int(parsed.skipped)}"
            f"（{'模拟执行' if parsed.simulated else '实际执行'}）"
        )
    else:
        text = f"评分 {int(parsed.total)}/100，结论 {_VERDICT_LABELS.get(parsed.verdict, parsed.verdict.value)}"
    if blockers:
        text += f"；{len(blockers)} 项阻塞"
    return text


def _prior_summary(stage: LoopStage, parsed: Any) -> dict[str, Any]:
    """只整理上阶段的可读摘要，不把原始 JSON 塞回提示词。"""

    if stage is LoopStage.ideate:
        return {
            "title": parsed.title,
            "summary": parsed.summary,
            "features": [
                f"{feature.id}：{feature.title}（{feature.priority.value}）"
                for feature in list(parsed.features or [])
            ],
            "assumptions": [
                assumption.statement for assumption in list(parsed.assumptions or [])
            ],
            "not_in_scope": list(parsed.not_in_scope or []),
        }
    if stage is LoopStage.validate:
        return {
            "summary": parsed.summary,
            "checks": [
                f"{check.id}：{check.result.value}"
                f"（{(check.feature_id or '全局')}）"
                for check in list(parsed.checks or [])
            ],
            "unknowns": list(parsed.unknowns or []),
        }
    if stage is LoopStage.develop:
        return {
            "title": parsed.title,
            "summary": parsed.summary,
            "entry_file": parsed.entry_file,
            "run_command": parsed.run_command,
            "files": [proto_file.path for proto_file in list(parsed.files or [])],
            "limitations": parsed.limitations,
        }
    if stage is LoopStage.test:
        return {
            "total": int(parsed.total),
            "passed": int(parsed.passed),
            "failed": int(parsed.failed),
            "skipped": int(parsed.skipped),
            "notes": parsed.notes,
            "cases": [
                f"{case.id}：{case.status.value}" for case in list(parsed.cases or [])
            ],
        }
    return {
        "total": int(parsed.total),
        "verdict": parsed.verdict.value,
        "highlights": list(parsed.highlights or []),
        "blockers": list(parsed.blockers or []),
        "next_actions": list(parsed.next_actions or []),
    }


# --------------------------------------------------------------------------- #
# 单阶段执行
# --------------------------------------------------------------------------- #


async def _run_stage(
    ctx: Any,
    *,
    stage: LoopStage,
    params: _LoopParams,
    state: ProductState,
    prior: dict[Any, Any],
    spec: LoopMvpSpec | None,
    stage_blockers: list[str],
    warnings: list[str],
    usage: dict[str, Any],
) -> _StageRun:
    run = _StageRun(stage=stage)
    run.step_id = _begin_stage_step(
        ctx,
        stage=stage,
        params=params,
        prior_stages=[str(item) for item in prior.keys()],
    )

    from core.loop_prompts import build_stage_messages

    try:
        messages = build_stage_messages(ctx, stage=stage, prior=prior)
        parsed_model, calls = await _generate_stage(
            ctx,
            stage=stage,
            messages=messages,
            params=params,
            warnings=warnings,
            usage=usage,
        )
    except ValueError as exc:
        run.error = str(exc)[:1000]
        run.calls = 1
        _finish_stage(
            ctx,
            step_id=run.step_id,
            stage=stage,
            params=params,
            parsed=None,
            blockers=[run.error],
            summary=f"{_STAGE_LABELS[stage]}阶段输出不可用",
            error_code=ErrorCode.MODEL_OUTPUT_INVALID,
            error_message=run.error,
        )
        return run

    run.calls = calls
    if stage is LoopStage.evaluate:
        parsed_model = _finalize_scorecard(
            parsed_model, stage_blockers=list(stage_blockers), params=params
        )
        run.blockers = []
    else:
        run.blockers = _deterministic_blockers(stage, parsed_model, spec)

    run.parsed = parsed_model
    run.summary = _stage_summary(stage, parsed_model, run.blockers)
    run.artifact_id = _finish_stage(
        ctx,
        step_id=run.step_id,
        stage=stage,
        params=params,
        parsed=parsed_model,
        blockers=run.blockers,
        summary=run.summary,
    )
    if run.blockers:
        warnings.append(
            f"{_STAGE_LABELS[stage]}阶段存在 {len(run.blockers)} 项阻塞，后续阶段继续执行"
        )
    return run


# --------------------------------------------------------------------------- #
# 事件 / 产品状态
# --------------------------------------------------------------------------- #


def _loop_event(
    *,
    loop_id: str,
    loop_version: int,
    params: _LoopParams,
    stage: LoopStage,
    stage_status: LoopStageStatus,
    round_status: LoopRoundStatus,
    loop_status: LoopStatus,
    score: int | None = None,
    verdict: LoopVerdict | None = None,
    artifact_id: str | None = None,
) -> dict[str, Any]:
    payload = LoopUpdatedPayload(
        loop_id=loop_id,
        status=loop_status,
        current_round=params.ordinal,
        loop_version=loop_version,
        stage=stage,
        stage_status=stage_status,
        round_status=round_status,
        score=score,
        verdict=verdict,
        artifact_ids=[artifact_id] if artifact_id else [],
    ).model_dump(mode="json")
    return {"type": EventType.loop_updated.value, "payload": payload}


def _feature_claim_text(feature: Any) -> str:
    text = f"{feature.title}：{feature.description}（验收：{feature.acceptance}）"
    return _clip(text, 500)


def _build_product_state(
    state: ProductState,
    *,
    run_id: str,
    message_ids: list[str],
    spec: LoopMvpSpec,
    scorecard: LoopScoreCard,
    params: _LoopParams,
) -> ProductState:
    new_version = int(state.version) + 1
    claims: list[Claim] = []
    options: list[OptionItem] = []
    decisions: list[DecisionItem] = []
    issues: list[UnresolvedIssue] = []

    for feature in list(spec.features or []):
        if feature.priority != FeaturePriority.must:
            continue
        claim_id = new_id()
        claims.append(
            Claim(
                id=claim_id,
                kind=ClaimKind.feature,
                text=_feature_claim_text(feature),
                source_message_ids=list(message_ids),
                evidence_refs=[],
                origin=OriginKind.model,
                status=ClaimStatus.proposed,
                created_product_version=new_version,
                role_code=None,
            )
        )
        options.append(
            OptionItem(
                id=new_id(),
                title=_clip(feature.title, 300),
                summary=_clip(feature.description, 2000),
                claim_ids=[claim_id],
                tradeoffs=[],
                risk_ids=[],
                assumption_ids=[],
                experiment_ids=[],
                origin=OriginKind.model,
                status=ClaimStatus.proposed,
            )
        )

    for blocker in list(scorecard.blockers or []):
        issues.append(
            UnresolvedIssue(
                id=new_id(),
                kind="gap",
                text=_clip(blocker, 1000),
                related_ids=[],
                status=IssueStatus.open,
                dimension="loop",
            )
        )

    option_id = options[0].id if options else None
    rationale = (
        f"第 {params.ordinal} 轮闭环评分 {int(scorecard.total)}/100，"
        f"结论 {_VERDICT_LABELS.get(scorecard.verdict, scorecard.verdict.value)}；"
        f"亮点：{'；'.join(str(item) for item in list(scorecard.highlights or [])[:3]) or '无'}；"
        f"阻塞 {len(issues)} 项。建议由用户决定是否交付或进入下一轮。"
    )
    decisions.append(
        DecisionItem(
            id=new_id(),
            option_id=option_id,
            rationale=_clip(rationale, 2000),
            evidence_refs=[],
            open_issue_ids=[issue.id for issue in issues],
            status=DecisionStatus.proposed,
            needs_review=False,
            origin=OriginKind.model,
        )
    )

    payload = state.model_dump(mode="json")
    payload.update(
        {
            "version": new_version,
            "created_at": utc_now_iso(),
            "source_run_id": run_id,
            "schema_version": SCHEMA_VERSION,
            "claims": [claim.model_dump(mode="json") for claim in claims],
            "options": [option.model_dump(mode="json") for option in options],
            "decisions": [decision.model_dump(mode="json") for decision in decisions],
            "unresolved_issues": [issue.model_dump(mode="json") for issue in issues],
        }
    )
    return ProductState.model_validate(payload)


def _display_text(
    scorecard: LoopScoreCard,
    *,
    params: _LoopParams,
    loop_status: LoopStatus,
    partial: bool,
) -> str:
    labels = _VERDICT_LABELS.get(scorecard.verdict, scorecard.verdict.value)
    if loop_status is LoopStatus.awaiting_decision:
        suffix = "待决策"
    elif loop_status is LoopStatus.running:
        suffix = "未达目标，等待决定是否进入下一轮"
    else:  # pragma: no cover - 防御性
        suffix = labels
    blocker_count = len(list(scorecard.blockers or []))
    head = "部分完成" if partial else "已完成"
    text = f"第 {params.ordinal} 轮闭环{head}：评分 {int(scorecard.total)}/100（{suffix}）"
    if blocker_count:
        text += f"，{blocker_count} 项阻塞已记录"
    return _clip(text, 6000)


# --------------------------------------------------------------------------- #
# apply 钩子（与提交同一事务）
# --------------------------------------------------------------------------- #


def _apply_success(
    db: Any,
    *,
    loop_id: str,
    ordinal: int,
    run_id: str,
    score: int,
    verdict: LoopVerdict,
    loop_status: LoopStatus,
) -> None:
    from repositories import loop_repo

    now_iso = utc_now_iso()
    loop = loop_repo.get_loop(db, loop_id)
    if loop is None:  # pragma: no cover - 同一事务内正常存在
        return
    round_row = loop_repo.get_round(db, loop_id=loop_id, ordinal=ordinal)
    if round_row is not None:
        loop_repo.update_round(
            db,
            round_row,
            status=LoopRoundStatus.done,
            score=score,
            verdict=verdict,
            finished_at=now_iso,
        )
    loop_repo.touch_loop(
        db,
        loop,
        status=loop_status,
        current_round=ordinal,
        latest_run_id=run_id,
        loop_version=int(loop.loop_version or 1) + 1,
        now_iso=now_iso,
    )


def _apply_failure(
    db: Any, *, loop_id: str, ordinal: int, run_id: str
) -> None:
    from repositories import loop_repo

    now_iso = utc_now_iso()
    loop = loop_repo.get_loop(db, loop_id)
    if loop is None:  # pragma: no cover
        return
    round_row = loop_repo.get_round(db, loop_id=loop_id, ordinal=ordinal)
    if round_row is not None:
        loop_repo.update_round(
            db,
            round_row,
            status=LoopRoundStatus.failed,
            finished_at=now_iso,
        )
    loop_repo.touch_loop(
        db,
        loop,
        status=LoopStatus.failed,
        latest_run_id=run_id,
        loop_version=int(loop.loop_version or 1) + 1,
        now_iso=now_iso,
    )


def _failure_apply(params: _LoopParams | None, run_id: str | None):
    if params is None or not run_id:
        return None
    loop_id = params.loop_id
    ordinal = params.ordinal

    def _apply(db: Any) -> None:
        _apply_failure(db, loop_id=loop_id, ordinal=ordinal, run_id=run_id)

    return _apply


def _failed_outcome(
    *,
    code: str,
    message: str,
    params: _LoopParams | None,
    run_id: str | None,
    warnings: list[str] | None = None,
    usage: dict[str, Any] | None = None,
) -> RunOutcome:
    return RunOutcome(
        status=RunStatus.failed,
        error_code=code,
        error_message=_clip(message, 1000),
        warnings=list(warnings or []),
        usage=dict(usage or {}),
        events=[],
        session_phase=None,
        brief_status=None,
        apply=_failure_apply(params, run_id),
    )


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #


async def run_loop_round(ctx: Any) -> RunOutcome:
    """执行一轮 Loop Studio 闭环（契约 §5.3）。"""

    state = getattr(ctx, "product_state", None)
    run_id = _run_id(ctx)
    if state is None:
        return _failed_outcome(
            code=ErrorCode.INVALID_REQUEST,
            message="缺少产品状态，无法执行 loop 阶段",
            params=None,
            run_id=run_id,
        )
    params = _resolve_params(ctx)
    if params is None:
        return _failed_outcome(
            code=ErrorCode.INVALID_REQUEST,
            message="缺少 loop_id 或 loop_round 预算参数，无法执行 loop 阶段",
            params=None,
            run_id=run_id,
        )
    loop_state = _read_loop_state(params.loop_id)
    if loop_state is None:
        return _failed_outcome(
            code=ErrorCode.NOT_FOUND,
            message=f"闭环不存在：{params.loop_id}",
            params=params,
            run_id=run_id,
        )
    if not run_id:
        return _failed_outcome(
            code=ErrorCode.INVALID_REQUEST,
            message="缺少 Run 上下文，无法执行 loop 阶段",
            params=params,
            run_id=None,
        )

    # accepted loop version：排期时已经 +1，这里按契约再 +1 作为本轮提交后的版本。
    loop_version = int(loop_state["loop_version"]) + 1
    warnings: list[str] = []
    usage: dict[str, Any] = {}

    try:
        _ensure_budget(ctx, REQUIRED_UPSTREAM_CALLS)
    except AppError as exc:
        return _failed_outcome(
            code=ErrorCode.BUDGET_EXCEEDED,
            message=f"预算不足，本轮至少需要 {REQUIRED_UPSTREAM_CALLS} 次上游调用：{exc.message}",
            params=params,
            run_id=run_id,
            warnings=warnings,
            usage=usage,
        )
    except Exception as exc:  # pragma: no cover - 防御性
        return _failed_outcome(
            code=ErrorCode.INTERNAL,
            message=f"预算检查失败：{exc}",
            params=params,
            run_id=run_id,
            warnings=warnings,
            usage=usage,
        )

    completed = _load_completed_stages(params.loop_id, params.ordinal)
    stage_runs: dict[LoopStage, _StageRun] = {}
    prior: dict[Any, Any] = {}
    stage_blockers: list[str] = []
    spec: LoopMvpSpec | None = None
    validation: LoopValidation | None = None
    test_report: LoopTestReport | None = None

    for stage in list(LoopStage):
        preloaded = completed.get(stage)
        if preloaded is not None:
            parsed, artifact_id = preloaded
            blockers = _deterministic_blockers(stage, parsed, spec)
            stage_run = _StageRun(
                stage=stage,
                artifact_id=artifact_id,
                parsed=parsed,
                blockers=blockers,
                summary=_stage_summary(stage, parsed, blockers),
                reused=True,
            )
        else:
            try:
                stage_run = await _run_stage(
                    ctx,
                    stage=stage,
                    params=params,
                    state=state,
                    prior=prior,
                    spec=spec,
                    stage_blockers=stage_blockers,
                    warnings=warnings,
                    usage=usage,
                )
            except AppError as exc:
                return _failed_outcome(
                    code=exc.code,
                    message=str(exc.message)[:1000],
                    params=params,
                    run_id=run_id,
                    warnings=warnings,
                    usage=usage,
                )

        stage_runs[stage] = stage_run
        if not stage_run.ok:
            return _failed_outcome(
                code=ErrorCode.MODEL_OUTPUT_INVALID,
                message=(
                    f"{_STAGE_LABELS[stage]}阶段的输出不可用："
                    f"{stage_run.error or '结构校验失败'}"
                ),
                params=params,
                run_id=run_id,
                warnings=warnings,
                usage=usage,
            )

        if stage is LoopStage.ideate:
            spec = stage_run.parsed
        elif stage is LoopStage.validate:
            validation = stage_run.parsed
        elif stage is LoopStage.test:
            test_report = stage_run.parsed
        if stage is not LoopStage.evaluate:
            stage_blockers.extend(stage_run.blockers)
        prior[stage] = _prior_summary(stage, stage_run.parsed)

    scorecard: LoopScoreCard = stage_runs[LoopStage.evaluate].parsed
    score = int(scorecard.total)
    verdict = scorecard.verdict
    exhausted = params.ordinal >= params.max_rounds
    if verdict == LoopVerdict.pass_ or exhausted:
        loop_status = LoopStatus.awaiting_decision
    else:
        loop_status = LoopStatus.running

    events: list[dict[str, Any]] = []
    for stage in list(LoopStage):
        stage_run = stage_runs[stage]
        is_evaluate = stage is LoopStage.evaluate
        events.append(
            _loop_event(
                loop_id=params.loop_id,
                loop_version=loop_version,
                params=params,
                stage=stage,
                stage_status=LoopStageStatus.done if is_evaluate else stage_run.status,
                round_status=LoopRoundStatus.done if is_evaluate else LoopRoundStatus.running,
                loop_status=loop_status,
                score=score if is_evaluate else None,
                verdict=verdict if is_evaluate else None,
                artifact_id=stage_run.artifact_id,
            )
        )

    assert spec is not None  # ideate 已成功时必然存在；上面已保证
    new_state = _build_product_state(
        state,
        run_id=run_id,
        message_ids=_current_message_id(ctx),
        spec=spec,
        scorecard=scorecard,
        params=params,
    )
    events.append(
        {
            "type": EventType.product_state_updated.value,
            "payload": {
                "product_version": new_state.version,
                "session_version": _session_version(ctx) + 1,
            },
        }
    )

    partial = any(
        run.blockers for stage, run in stage_runs.items() if stage is not LoopStage.evaluate
    )
    if partial:
        warnings.append("部分阶段存在确定性阻塞，本轮产品状态按 partial_succeeded 提交")

    display_text = _display_text(
        scorecard, params=params, loop_status=loop_status, partial=partial
    )
    message = OutgoingMessage(
        content=display_text,
        sender_kind=SenderKind.agent,
        role_code=None,
        stage=MessageStage.brainstorm,
        status=MessageStatus.completed,
    )

    return RunOutcome(
        status=RunStatus.partial_succeeded if partial else RunStatus.succeeded,
        product_state=new_state,
        messages=[message],
        events=events,
        result_product_version=new_state.version,
        warnings=warnings,
        usage=usage,
        session_phase=None,
        brief_status=None,
        apply=lambda db: _apply_success(
            db,
            loop_id=params.loop_id,
            ordinal=params.ordinal,
            run_id=run_id,
            score=score,
            verdict=verdict,
            loop_status=loop_status,
        ),
    )


__all__ = [
    "LOOP_STEP_KIND",
    "MAX_REPAIR_ROUNDS",
    "REQUIRED_UPSTREAM_CALLS",
    "STAGE_KEYS",
    "STAGE_ARTIFACT_KINDS",
    "STAGE_ARTIFACT_TITLES",
    "STAGE_MODELS",
    "run_loop_round",
]
