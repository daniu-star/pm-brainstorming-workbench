"""M2 主持人归并工作流（FR-04D / AC-16）。

一次 ``TaskKind.synthesis`` 运行会在四角色 brainstorm 提交之后被
:class:`services.command_service._commit_outcome` **自动排入**（见 ``RunOutcome.follow_up``），
本模块负责把"四份并列观点"收敛成可执行的结论：

1. **主持人一次归并**：用 ``RoleCode.moderator`` 版本化提示词
   （``core.role_prompts.SYNTHESIS_PROMPT``）读当前 ``ProductState``（主张/方案/未决事项），
   输出结构化 :class:`~domain.contracts.SynthesisResult`（决策建议、最多 2 个分歧、
   未解决缺口、验证建议）；JSON 不合法时**至多回喂修复一次**。
2. **两位专家复核**：从本轮实际产出的角色里挑两个，各自只做一件事——判断决策建议
   是否与主张一致并指出最关键反对理由（``ReviewVerdict``）。复核失败只降级为 warning，
   不影响已归并的结论，但会把 ``DecisionItem.needs_review`` 置为 true。
3. **确定性降级**：模型不可用/持续输出非法 JSON 时，不伪造"已完成综合判断"，而是用
   :func:`_fallback_synthesis` 生成 ``degraded=True`` 的确定性整理（按方案引用主张数排序），
   并写一条 ``gap`` 未决事项说明缺口。
4. **写回产品状态**：``decisions``（一律 ``proposed``）、``conflict``/``gap`` 未决事项，
   ``version = state.version + 1``；画布/报告由既有 ``canvas_projector`` 自动投影
   ``decision`` 与 ``question`` 节点，无需改投影器。

失败语义（与 brainstorm 一致）：

* 预算不足 → ``BudgetExceededError``（``AppError``）直接返回 ``failed(BUDGET_EXCEEDED)``；
* 主持人 LLM 调用失败 → 确定性降级（``degraded=True``），Run 仍然 ``succeeded``；
* 本模块**不提交**产品状态/消息/事件，一律交给 ``RunOutcome`` 与 runner 的事务提交；
  只有 step staging 走短事务（``repositories.database.session_scope``）。
"""
from __future__ import annotations

import time
from typing import Any

from domain.contracts import (
    MAX_REFS_PER_ITEM,
    MAX_SYNTHESIS_OUTPUT_TOKENS,
    STEP_TIMEOUT_SECONDS,
    SCHEMA_VERSION,
    DecisionItem,
    DecisionProposal,
    DecisionStatus,
    EventType,
    IssueStatus,
    MessageStage,
    MessageStatus,
    OriginKind,
    ProductState,
    RoleCode,
    RunStatus,
    SenderKind,
    StepStatus,
    StrictModel,
    SynthesisConflict,
    SynthesisResult,
    TaskKind,
    UnresolvedIssue,
    new_id,
    utc_now_iso,
)
from domain.errors import AppError, ErrorCode
from services.context_builder import build_context
from core.llm_client import LLMError, LLMOutputInvalidError

#: 与 ``workflows.runner.LOGICAL_KEYS[TaskKind.synthesis]`` 保持一致（避免模块级循环导入）。
SYNTHESIS_LOGICAL_KEY = "synthesis.lead"
SYNTHESIS_STEP_KIND = "synthesis_lead"
SYNTHESIS_PURPOSE = "synthesis.moderator"

#: 主持人 1 次 + 修复 1 次 + 复核 2 次 = 最多 4 次上游调用。
MAX_REPAIR_ROUNDS = 1
MAX_REVIEWERS = 2
REQUIRED_CALLS = 1 + MAX_REPAIR_ROUNDS + MAX_REVIEWERS

REPAIR_INSTRUCTION = (
    "上一条输出没有通过 SynthesisResult 的严格校验：{error}。"
    "请只输出一个合法 JSON 对象，字段与协议完全一致，不要包含解释或 Markdown 代码块。"
)

REVIEW_SYSTEM_PROMPT = """## 角色：交叉复核人

你只做两件事：判断主持人的决策建议是否与当前产品状态中的主张一致，并指出最关键的反对理由。
只引用输入中真实出现过的 claim_id / option_id / issue_id；不得编造证据。
不要输出模型私有推理过程；不要输出 API Key、内网地址或外部链接。

## 输出协议（严格 JSON，禁止额外字段）
{"agree": true, "concerns": ["<=300 字"]}
"""


class ReviewVerdict(StrictModel):
    """复核人输出：是否同意决策建议 + 最多两条反对理由。"""

    agree: bool = True
    concerns: list[str] = []


# --------------------------------------------------------------------------- #
# 上下文 / 预算辅助（与 brainstorm 保持同一套约定）
# --------------------------------------------------------------------------- #


def _run_id(ctx: Any) -> str | None:
    return getattr(getattr(ctx, "run", None), "id", None)


def _session_id(ctx: Any, state: ProductState) -> str:
    return getattr(getattr(ctx, "session", None), "id", None) or state.session_id


def _remaining_timeout(ctx: Any) -> float:
    deadline = getattr(ctx, "deadline_epoch", None)
    if isinstance(deadline, (int, float)):
        remaining = float(deadline) - time.time()
    else:
        remaining = float(STEP_TIMEOUT_SECONDS)
    return max(5.0, min(float(STEP_TIMEOUT_SECONDS), remaining))


def _max_context_tokens(ctx: Any) -> int:
    from domain.contracts import MAX_CONTEXT_INPUT_TOKENS

    value = getattr(getattr(ctx, "budget", None), "max_context_input_tokens", None)
    return int(value) if isinstance(value, int) and value > 0 else MAX_CONTEXT_INPUT_TOKENS


def _ensure_budget(ctx: Any, required_calls: int) -> None:
    checker = getattr(getattr(ctx, "budget", None), "ensure_required_calls", None)
    if callable(checker):
        checker(required_calls, purpose="synthesis")


# --------------------------------------------------------------------------- #
# 结果归一化与降级
# --------------------------------------------------------------------------- #


def _normalize(result: SynthesisResult, warnings: list[str]) -> SynthesisResult:
    """模型漏标 schema_version/超量引用时做最小归正（不因此丢掉整个结论）。"""

    updates: dict[str, Any] = {}
    if result.schema_version != SCHEMA_VERSION:
        warnings.append(
            f"主持人产物 schema_version={result.schema_version} 与当前版本不一致，已按 v{SCHEMA_VERSION} 接受"
        )
        updates["schema_version"] = SCHEMA_VERSION
    if len(result.conflicts) > 2:
        warnings.append("主持人给出的冲突超过 2 个，已截断为前 2 个最关键议题")
        updates["conflicts"] = result.conflicts[:2]
    consensus = result.consensus_claim_ids[: MAX_REFS_PER_ITEM * 2]
    if len(consensus) != len(result.consensus_claim_ids):
        updates["consensus_claim_ids"] = consensus
    return result.model_copy(update=updates) if updates else result


def _fallback_synthesis(state: ProductState, warnings: list[str]) -> SynthesisResult:
    """确定性整理：只排序已有材料，不做任何模型判断，并显式标记 ``degraded``。"""

    warnings.append("主持人模型不可用，已使用确定性整理作为降级结果（degraded=true）")

    ranked = sorted(state.options, key=lambda item: (-len(item.claim_ids), item.id))
    recommended = ranked[0].id if ranked else None
    alternatives = [item.id for item in ranked[1:]][:MAX_REFS_PER_ITEM]
    proposals = []
    if ranked:
        recommended_option = ranked[0]
        proposals.append(
            {
                "option_id": recommended_option.id,
                "rationale": (
                    f"确定性整理：该方案引用了最多的已归并主张（{len(recommended_option.claim_ids)} 条），"
                    "在主持人不可用时作为临时首选，需人工确认。"
                ),
                "evidence_refs": recommended_option.claim_ids[:MAX_REFS_PER_ITEM],
            }
        )
    return SynthesisResult(
        consensus_claim_ids=[claim.id for claim in state.claims][: MAX_REFS_PER_ITEM * 2],
        recommended_option_id=recommended,
        alternative_option_ids=alternatives,
        decision_proposals=proposals,
        limitations=(
            "主持人不可用：本结论由确定性整理生成，只对已有方案排序，未经模型归并与专家复核；"
            "不得当作已完成综合判断。"
        ),
        degraded=True,
    )


# --------------------------------------------------------------------------- #
# 提示词组装
# --------------------------------------------------------------------------- #


def _claims_digest(state: ProductState) -> str:
    """``context_builder`` 不渲染 claims，这里补一节给主持人引用真实 ID。"""

    lines = ["### 各专家主张（claim_id / 角色 / 文本）"]
    if state.claims:
        for claim in state.claims[:40]:
            role = claim.role_code.value if claim.role_code is not None else "unknown"
            lines.append(f"- [{claim.id}]（{role}，{claim.kind.value}）{claim.text}")
    else:
        lines.append("- （本轮没有归并到任何主张）")
    if state.options:
        lines.append("### 方案与主张的引用关系")
        for option in state.options:
            refs = "、".join(option.claim_ids) if option.claim_ids else "（未引用主张）"
            lines.append(f"- [{option.id}] {option.title}：{refs}")
    return "\n".join(lines)


def _moderator_messages(ctx: Any, state: ProductState) -> list[dict[str, Any]]:
    from core.role_prompts import PROMPT_VERSION, build_role_messages

    bundle = build_context(
        getattr(ctx, "db", None),
        session_id=_session_id(ctx, state),
        task_kind=TaskKind.synthesis,
        role_code=RoleCode.moderator,
        product_version=state.version,
        prompt_version=PROMPT_VERSION,
        max_input_tokens=_max_context_tokens(ctx),
    )
    messages = build_role_messages(RoleCode.moderator, bundle, PROMPT_VERSION)
    messages.append({"role": "user", "content": _claims_digest(state)})
    return messages


def _review_messages(state: ProductState, synthesis: SynthesisResult, reviewer: RoleCode) -> list[dict[str, Any]]:
    payload = synthesis.model_dump(mode="json")
    text = (
        f"### 待复核的决策建议（主持人输出）\n{payload}\n\n"
        f"### 当前产品状态摘要\n{_claims_digest(state)}\n\n"
        f"你是 {reviewer.value} 视角的复核人。"
    )
    return [
        {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
        {"role": "user", "content": text},
    ]


# --------------------------------------------------------------------------- #
# 模型调用
# --------------------------------------------------------------------------- #


async def _complete_json(ctx: Any, *, messages: list[dict[str, Any]], purpose: str, schema: dict) -> Any:
    llm = getattr(ctx, "llm", None)
    if llm is None:
        raise AppError(ErrorCode.INTERNAL, "缺少 LLM 客户端，无法执行主持人归并")
    complete_json = getattr(llm, "complete_json", None)
    if not callable(complete_json):
        raise AppError(ErrorCode.INTERNAL, "LLM 客户端不支持结构化输出（缺少 complete_json）")
    return await complete_json(
        messages=messages,
        max_output_tokens=MAX_SYNTHESIS_OUTPUT_TOKENS,
        timeout_seconds=_remaining_timeout(ctx),
        purpose=purpose,
        json_schema=schema,
        temperature=0.2,
    )


async def _moderate(ctx: Any, state: ProductState, warnings: list[str]) -> tuple[SynthesisResult, Any | None, bool]:
    """主持人归并：返回 ``(result, llm_result, degraded)``；模型失败走确定性降级。"""

    conversation = _moderator_messages(ctx, state)
    llm_result: Any | None = None
    last_error: Exception | None = None

    for attempt in range(MAX_REPAIR_ROUNDS + 1):
        try:
            parsed, llm_result = await _complete_json(
                ctx,
                messages=conversation,
                purpose=SYNTHESIS_PURPOSE,
                schema=SynthesisResult.model_json_schema(),
            )
        except LLMOutputInvalidError as exc:
            last_error = exc
            if attempt >= MAX_REPAIR_ROUNDS:
                break
            warnings.append(f"主持人首次输出不是合法 JSON，已回喂错误修复一次：{str(exc)[:200]}")
            conversation = conversation + [
                {"role": "user", "content": REPAIR_INSTRUCTION.replace("{error}", str(exc)[:600])}
            ]
            continue
        except LLMError as exc:
            last_error = exc
            break

        try:
            result = SynthesisResult.model_validate(parsed)
        except Exception as exc:  # pydantic ValidationError：字段协议不符
            last_error = exc
            if attempt >= MAX_REPAIR_ROUNDS:
                break
            warnings.append(f"主持人首次输出未通过结构校验，已回喂错误修复一次：{str(exc)[:200]}")
            conversation = conversation + [
                {"role": "user", "content": REPAIR_INSTRUCTION.replace("{error}", str(exc)[:600])}
            ]
            continue
        return _normalize(result, warnings), llm_result, False

    warnings.append(f"主持人归并失败，已降级为确定性整理：{str(last_error or '未知错误')[:200]}")
    return _fallback_synthesis(state, warnings), llm_result, True


async def _review(
    ctx: Any,
    state: ProductState,
    synthesis: SynthesisResult,
    reviewer: RoleCode,
    warnings: list[str],
) -> tuple[ReviewVerdict | None, Any | None]:
    """一次专家复核；任何模型/校验失败只是 warning，不打断归并。"""

    try:
        _ensure_budget(ctx, 1)
        parsed, llm_result = await _complete_json(
            ctx,
            messages=_review_messages(state, synthesis, reviewer),
            purpose=f"synthesis.review.{reviewer.value}",
            schema=ReviewVerdict.model_json_schema(),
        )
        return ReviewVerdict.model_validate(parsed), llm_result
    except LLMError as exc:
        warnings.append(f"{reviewer.value} 复核失败（不影响结论）：{str(exc)[:200]}")
        return None, None
    except AppError:
        raise
    except Exception as exc:  # 校验失败
        warnings.append(f"{reviewer.value} 复核输出不合法（不影响结论）：{str(exc)[:200]}")
        return None, None


def _reviewer_roles(state: ProductState) -> list[RoleCode]:
    """从本轮实际产出的角色中挑两位复核人；不足两位时按固定顺序补齐。"""

    preferred = (RoleCode.cto, RoleCode.designer, RoleCode.ops, RoleCode.target_user)
    present = [
        role
        for role in preferred
        if any(claim.role_code is role for claim in state.claims)
    ]
    chosen = present[:MAX_REVIEWERS]
    for role in preferred:
        if len(chosen) >= MAX_REVIEWERS:
            break
        if role not in chosen:
            chosen.append(role)
    return chosen


# --------------------------------------------------------------------------- #
# 写回产品状态
# --------------------------------------------------------------------------- #


def _normalize_text(text: str) -> str:
    return " ".join((text or "").split()).strip().lower()


def _decisions_from(
    state: ProductState,
    synthesis: SynthesisResult,
    *,
    needs_review: bool,
    warnings: list[str],
) -> list[DecisionItem]:
    option_ids = state.option_ids()
    claim_ids = state.claim_ids()
    decisions = list(state.decisions)
    existing = {(item.option_id, _normalize_text(item.rationale)) for item in decisions}

    proposals = list(synthesis.decision_proposals)
    if not proposals and synthesis.recommended_option_id is not None:
        proposals.append(
            DecisionProposal(
                option_id=synthesis.recommended_option_id,
                rationale="主持人推荐该方案，但未给出结构化理由。",
                evidence_refs=[],
            )
        )

    for proposal in proposals:
        option_id = proposal.option_id if proposal.option_id in option_ids else None
        if proposal.option_id is not None and option_id is None:
            warnings.append(f"主持人引用了不存在的方案 {proposal.option_id}，已按「未指定方案」记录")
        rationale = (proposal.rationale or "").strip() or "主持人未提供理由。"
        evidence = [ref for ref in proposal.evidence_refs if ref in claim_ids][:MAX_REFS_PER_ITEM]
        key = (option_id, _normalize_text(rationale))
        if key in existing:
            continue
        existing.add(key)
        decisions.append(
            DecisionItem(
                id=new_id(),
                option_id=option_id,
                rationale=rationale,
                evidence_refs=evidence,
                open_issue_ids=list(synthesis.unresolved_issue_ids)[:MAX_REFS_PER_ITEM],
                status=DecisionStatus.proposed,
                needs_review=needs_review,
                origin=OriginKind.model,
            )
        )
    return decisions


def _issues_from(
    state: ProductState,
    synthesis: SynthesisResult,
    *,
    degraded: bool,
    warnings: list[str],
) -> list[UnresolvedIssue]:
    claim_ids = set(state.claim_ids())
    issues = list(state.unresolved_issues)
    existing = {_normalize_text(item.text) for item in issues}

    def add(kind: str, text: str, related: list[str]) -> None:
        key = _normalize_text(text)
        if key in existing:
            return
        existing.add(key)
        issues.append(
            UnresolvedIssue(
                id=new_id(),
                kind=kind,
                text=text[:1000],
                related_ids=[ref for ref in related if ref in claim_ids][:MAX_REFS_PER_ITEM],
                status=IssueStatus.open,
                dimension="synthesis",
            )
        )

    for conflict in synthesis.conflicts:
        topic = (conflict.topic or "").strip() or "未命名分歧"
        impact = (conflict.impact or "").strip() or "待评估影响"
        add("conflict", f"分歧：{topic}｜影响：{impact}", list(conflict.claim_ids))
        evidence = (conflict.needed_evidence or "").strip()
        if evidence:
            add("gap", f"待补证据（{topic}）：{evidence}", list(conflict.claim_ids))

    if degraded:
        add("gap", "主持人不可用：四角色观点尚未归并为决策，需人工确认方向后继续。", [])
    if not state.claims:
        add("gap", "本轮没有归并到任何专家主张，主持人无法给出可靠决策建议。", [])
    if not state.options:
        add("gap", "产品状态里没有可选方案，无法形成决策建议。", [])

    warnings.extend(f"未采用主持人引用的未决事项 {ref}" for ref in synthesis.unresolved_issue_ids if ref not in {item.id for item in state.unresolved_issues})
    return issues


def _report_message(
    state: ProductState,
    synthesis: SynthesisResult,
    verdicts: list[tuple[RoleCode, ReviewVerdict]],
    new_issues: list[UnresolvedIssue],
) -> Any:
    """主持人对外消息：只讲结论与缺口，绝不带模型原始 JSON。"""

    from services.command_service import OutgoingMessage

    lines: list[str] = []
    if synthesis.degraded:
        lines.append("【降级】主持人不可用，以下为基于现有材料的确定性整理：")

    option_title = {option.id: option.title for option in state.options}
    if synthesis.recommended_option_id is not None:
        title = option_title.get(synthesis.recommended_option_id, synthesis.recommended_option_id)
        lines.append(f"决策建议：{title}（状态：proposed，需你确认）")
    else:
        lines.append("决策建议：本轮未形成推荐方案。")

    if synthesis.conflicts:
        topics = "；".join(conflict.topic for conflict in synthesis.conflicts if conflict.topic)
        if topics:
            lines.append(f"主要分歧：{topics}")

    conflict_gaps = [
        item.text
        for item in new_issues
        if item.kind in ("conflict", "gap")
    ]
    if conflict_gaps:
        lines.append("未解决缺口：" + "；".join(conflict_gaps[:3]))

    concerns = [text for _, verdict in verdicts for text in verdict.concerns]
    if concerns:
        lines.append("复核提示：" + "；".join(concerns[:3]))

    return OutgoingMessage(
        content="\n".join(lines),
        sender_kind=SenderKind.agent,
        role_code=RoleCode.moderator,
        stage=MessageStage.brainstorm,
        status=MessageStatus.completed,
    )


# --------------------------------------------------------------------------- #
# staging（step 短事务）
# --------------------------------------------------------------------------- #


def _begin_step(run_id: str, state: ProductState) -> str:
    from repositories import run_repo
    from repositories.database import session_scope

    with session_scope() as db:
        run = run_repo.get_run(db, run_id)
        if run is None:
            raise AppError(ErrorCode.NOT_FOUND, "运行记录不存在，无法执行 synthesis", details={"run_id": run_id})
        step = run_repo.ensure_step(
            db,
            run,
            logical_key=SYNTHESIS_LOGICAL_KEY,
            kind=SYNTHESIS_STEP_KIND,
            role_code=RoleCode.moderator,
            input_hash=run.input_hash,
            input_snapshot={"base_product_version": state.version},
        )
        if StepStatus(step.status) in (StepStatus.pending, StepStatus.interrupted):
            run_repo.start_step(db, step)
        return step.id


def _finish_step(step_id: str, *, status: Any, output: dict[str, Any] | None = None, error: str | None = None) -> None:
    from repositories import run_repo
    from repositories.database import session_scope

    with session_scope() as db:
        step = run_repo.get_step(db, step_id)
        if step is None:  # pragma: no cover
            return
        run_repo.finish_step(
            db,
            step,
            status=status,
            output=output,
            error_code=ErrorCode.MODEL_OUTPUT_INVALID if error else None,
            error_message=error,
        )


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #


async def run_synthesis(ctx: Any) -> Any:
    """执行一次主持人归并，返回 :class:`RunOutcome`（调用方负责事务提交）。"""

    from services.command_service import RunOutcome

    warnings: list[str] = []
    usage: dict[str, Any] = {}
    state: ProductState | None = getattr(ctx, "product_state", None)
    run_id = _run_id(ctx)

    if state is None or not run_id:
        return RunOutcome(
            status=RunStatus.failed,
            error_code=ErrorCode.INTERNAL,
            error_message="缺少产品状态或运行记录，无法执行主持人归并",
            warnings=warnings,
            session_phase=None,
            brief_status=None,
        )

    try:
        _ensure_budget(ctx, REQUIRED_CALLS)
    except AppError as exc:
        return RunOutcome(
            status=RunStatus.failed,
            error_code=str(exc.code),
            error_message=str(exc.message or exc),
            warnings=warnings,
            session_phase=None,
            brief_status=None,
        )

    step_id: str | None = None
    try:
        step_id = _begin_step(run_id, state)
        synthesis, llm_result, degraded = await _moderate(ctx, state, warnings)
        usage = _merge_usage(usage, llm_result)

        verdicts: list[tuple[RoleCode, ReviewVerdict]] = []
        if not degraded:
            for reviewer in _reviewer_roles(state)[:MAX_REVIEWERS]:
                verdict, review_result = await _review(ctx, state, synthesis, reviewer, warnings)
                usage = _merge_usage(usage, review_result)
                if verdict is not None:
                    verdicts.append((reviewer, verdict))
        needs_review = any(not verdict.agree for _, verdict in verdicts) or degraded

        existing_issue_ids = {item.id for item in state.unresolved_issues}
        decisions = _decisions_from(state, synthesis, needs_review=needs_review, warnings=warnings)
        issues = _issues_from(state, synthesis, degraded=degraded, warnings=warnings)
        new_issues = [item for item in issues if item.id not in existing_issue_ids]

        payload = state.model_dump(mode="json")
        payload.update(
            {
                "version": state.version + 1,
                "created_at": utc_now_iso(),
                "source_run_id": run_id,
                "schema_version": SCHEMA_VERSION,
                "decisions": [item.model_dump(mode="json") for item in decisions],
                "unresolved_issues": [item.model_dump(mode="json") for item in issues],
            }
        )
        new_state = ProductState.model_validate(payload)

        events: list[dict[str, Any]] = [
            {
                "type": EventType.role_completed.value,
                "payload": {
                    "role_code": RoleCode.moderator.value,
                    "step_id": step_id,
                    "status": "degraded" if degraded else "succeeded",
                    "claim_count": len(synthesis.consensus_claim_ids),
                    "conflict_count": len(synthesis.conflicts),
                },
            },
            {
                "type": EventType.product_state_updated.value,
                "payload": {"product_version": new_state.version},
            },
        ]

        message = _report_message(state, synthesis, verdicts, new_issues)
        _finish_step(
            step_id,
            status="succeeded",
            output={
                "degraded": degraded,
                "needs_review": needs_review,
                "decision_ids": [item.id for item in decisions],
                "issue_ids": [item.id for item in issues],
            },
        )

        return RunOutcome(
            status=RunStatus.succeeded,
            product_state=new_state,
            messages=[message],
            events=events,
            result_product_version=new_state.version,
            warnings=warnings,
            usage=usage,
            session_phase=None,
            brief_status=None,
        )
    except AppError as exc:
        if step_id is not None:
            _finish_step(step_id, status="failed", error=str(exc.message or exc))
        return RunOutcome(
            status=RunStatus.failed,
            error_code=str(exc.code),
            error_message=str(exc.message or exc),
            warnings=warnings,
            session_phase=None,
            brief_status=None,
        )


def _merge_usage(total: dict[str, Any], result: Any | None) -> dict[str, Any]:
    usage = getattr(result, "usage", None)
    as_dict = getattr(usage, "as_dict", None)
    if callable(as_dict):
        part = dict(as_dict())
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = part.get(key)
            if isinstance(value, int):
                total[key] = int(total.get(key, 0)) + value
        if part.get("usage_source") and not total.get("usage_source"):
            total["usage_source"] = part["usage_source"]
    return total


__all__ = [
    "SYNTHESIS_LOGICAL_KEY",
    "SYNTHESIS_STEP_KIND",
    "SYNTHESIS_PURPOSE",
    "MAX_REPAIR_ROUNDS",
    "MAX_REVIEWERS",
    "REQUIRED_CALLS",
    "REPAIR_INSTRUCTION",
    "REVIEW_SYSTEM_PROMPT",
    "ReviewVerdict",
    "run_synthesis",
]
