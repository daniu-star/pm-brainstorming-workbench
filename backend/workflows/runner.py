"""Run 执行器（PRD FR-03 / FR-08）。

职责边界
--------
* **执行**：领取由 :mod:`workers.run_worker` 完成；本模块只负责“已持有租约”的
  Run 的步骤调度、预算记账、上游重试、期限检查与结果提交。
* **事务**：所有数据库写入都经 ``repositories.database.session_scope()`` 短事务；
  **模型/网络调用永远不在事务内进行**。步骤工作流拿到的是独立只读会话
  （``StepContext.db``），结果提交在另一笔事务中完成。
* **提交**：唯一写路径是 :func:`services.command_service.apply_outcome`；它负责在
  同一事务写产品状态 / 消息 / 事件 / 步骤 / Run 终结。租约丢失时它抛
  ``LEASE_LOST``，本模块只记录日志、不再重试提交。
* **幂等**：步骤已有终态时不再重复调用模型；已终结的 Run 直接返回。
* **不变量**：任何异常路径都保证 Run 不残留 ``running``，且恰好一个终结事件。
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from core.llm_client import LLMClient, LLMError, LLMUsage
from domain import contracts as C
from domain import state_machine as SM
from domain.contracts import (
    Action,
    EventType,
    RoleCode,
    RunStatus,
    SenderKind,
    StepStatus,
    TaskKind,
    canonical_json,
    utc_now_iso,
)
from domain.errors import AppError, ErrorCode

logger = logging.getLogger("workflows.runner")

# 单步骤最多允许的上游尝试次数（PRD FR-03）。
MAX_STEP_ATTEMPTS = 2

#: action → 任务类型（与 command_service.ACTION_TASK_KIND 保持一致）。
ACTION_TASK_KIND: dict[Action, TaskKind] = {
    Action.clarify: TaskKind.clarify,
    Action.brainstorm: TaskKind.brainstorm,
    Action.synthesis: TaskKind.synthesis,
    Action.start_audit: TaskKind.audit_open,
    Action.answer_audit: TaskKind.audit_answer,
    Action.end_audit: TaskKind.audit_report,
    Action.cancel_audit: TaskKind.audit_report,
    Action.rebuild_legacy: TaskKind.rebuild_legacy,
    Action.start_loop: TaskKind.loop_round,
    Action.loop_decision: TaskKind.loop_round,
}

#: 任务类型 → 幂等 logical_key（steps 表唯一键）。
LOGICAL_KEYS: dict[TaskKind, str] = {
    TaskKind.clarify: "coach.clarify",
    TaskKind.brainstorm: "brainstorm.generate",
    TaskKind.synthesis: "synthesis.lead",
    TaskKind.peer_review: "peer_review.review",
    TaskKind.audit_open: "audit.question",
    TaskKind.audit_answer: "audit.assess",
    TaskKind.audit_report: "audit.report",
    TaskKind.rebuild_legacy: "legacy.rebuild",
    TaskKind.loop_round: "loop.round",
}

#: 任务类型 → 步骤角色（消息 role_code / 预算归属）。
STEP_ROLE: dict[TaskKind, RoleCode] = {
    TaskKind.clarify: RoleCode.coach,
    TaskKind.brainstorm: RoleCode.cto,
    TaskKind.synthesis: RoleCode.coach,
    TaskKind.peer_review: RoleCode.coach,
    TaskKind.audit_open: RoleCode.coach,
    TaskKind.audit_answer: RoleCode.coach,
    TaskKind.audit_report: RoleCode.coach,
    TaskKind.rebuild_legacy: RoleCode.coach,
    TaskKind.loop_round: RoleCode.coach,
}

#: 工作流的惰性描述：``(模块名, 函数名)``。调用时才 import，
#: 因此模块文件尚未落地（团队并行开发期）也不会拖垮 ``import workflows.runner``。
WorkflowRef = tuple[str, str]

#: 任务类型 → 工作流入口（M2 起注册 brainstorm 与审计三项）。
WORKFLOWS: dict[TaskKind, WorkflowRef] = {
    TaskKind.clarify: ("workflows.coach", "run_clarify"),
    TaskKind.brainstorm: ("workflows.brainstorm", "run_brainstorm"),
    TaskKind.synthesis: ("workflows.synthesis", "run_synthesis"),
    TaskKind.audit_open: ("workflows.audit", "run_audit_open"),
    TaskKind.audit_answer: ("workflows.audit", "run_audit_answer"),
    TaskKind.audit_report: ("workflows.audit", "run_audit_report"),
    TaskKind.loop_round: ("workflows.loop", "run_loop_round"),
}

#: 可注入的工作流分发表（优先级高于 :data:`WORKFLOWS`）；测试可整体替换。
#: 未注册的类型返回 ``None``，由执行器落 ``FEATURE_NOT_READY``。
WORKFLOW_DISPATCH: dict[TaskKind, Callable[[Any], Any]] = {}


# --------------------------------------------------------------------------- #
# 预算
# --------------------------------------------------------------------------- #


class BudgetExceededError(AppError):
    """预算/上下文超限；``code`` 直接作为 Run 的 ``error_code``。

    继承 :class:`domain.errors.AppError`，使工作流内部按 ``AppError`` 捕获时
    也能正确识别预算错误（AC-29：超限时拒绝发请求，而不是继续调用）。
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message)


@dataclass(slots=True)
class _Reservation:
    """一次上游调用的预算预留（输入按估算、输出按上限预留）。"""

    input_tokens: int = 0
    output_tokens: int = 0


def _count(value: Any) -> int:
    """把账本里的计数安全转成非负整数（损坏/缺失一律按 0）。"""

    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


@dataclass(slots=True)
class BudgetTracker:
    """单 Run 预算账本（PRD FR-08）。

    * limits 来自 :mod:`domain.contracts` 常量，可被 ``runs.budget_json`` **收紧**
      （不允许放宽，``min(常量, 覆盖值)``）。
    * 每次上游调用前 ``begin_call`` 预留；调用结束 ``finish_call`` 用真实 usage
      结算。失败/崩溃的调用按预留上限记账（不释放后无限重试）。
    * 缺必需预算（至少一次上游调用）时显式失败，不静默减步骤。
    """

    max_run_input_tokens: int = C.MAX_RUN_INPUT_TOKENS
    max_run_output_tokens: int = C.MAX_RUN_OUTPUT_TOKENS
    max_upstream_calls: int = C.MAX_UPSTREAM_CALLS_PER_RUN
    max_context_input_tokens: int = C.MAX_CONTEXT_INPUT_TOKENS
    max_output_tokens: int = C.MAX_OUTPUT_TOKENS
    max_step_output_tokens: int = C.MAX_SYNTHESIS_OUTPUT_TOKENS
    step_timeout_seconds: int = C.STEP_TIMEOUT_SECONDS
    run_timeout_seconds: int = C.RUN_TIMEOUT_SECONDS
    input_tokens: int = 0
    output_tokens: int = 0
    upstream_calls: int = 0
    provider_calls: int = 0
    estimated_calls: int = 0
    reserved_input_tokens: int = 0
    reserved_output_tokens: int = 0
    exceeded: bool = False
    exceeded_code: str | None = None
    exceeded_reason: str | None = None
    warnings: list[str] = field(default_factory=list)

    # -- 构造 -------------------------------------------------------------- #

    @classmethod
    def from_budget_json(
        cls, budget_json: str | None, usage_json: str | None = None
    ) -> "BudgetTracker":
        tracker = cls()
        raw: dict[str, Any]
        try:
            parsed = json.loads(budget_json) if budget_json else {}
            raw = parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            logger.warning("budget_json 无法解析，使用默认预算: %r", budget_json)
            raw = {}

        overrides: dict[str, Any] = {}
        for key, value in raw.items():
            if str(key).startswith("loop_"):
                # 闭环参数（loop_id / 轮次 / 阶段预算）由工作流自取，不是预算覆盖项。
                continue
            normalized = _BUDGET_ALIASES.get(str(key))
            if normalized is None:
                tracker.warnings.append(f"UNKNOWN_BUDGET_KEY:{key}")
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                tracker.warnings.append(f"INVALID_BUDGET_VALUE:{key}")
                continue
            overrides[normalized] = int(value)

        for name in (
            "max_run_input_tokens",
            "max_run_output_tokens",
            "max_upstream_calls",
            "max_context_input_tokens",
            "max_output_tokens",
            "max_step_output_tokens",
            "step_timeout_seconds",
            "run_timeout_seconds",
        ):
            if name not in overrides:
                continue
            value = overrides[name]
            default = getattr(tracker, name)
            if value < 0:
                tracker.warnings.append(f"INVALID_BUDGET_VALUE:{name}")
                continue
            # 预算只能收紧不能放宽；0 表示该维度无预算（严格失败）。
            setattr(tracker, name, min(default, value))
        tracker._restore_usage(usage_json)
        return tracker

    def _restore_usage(self, usage_json: str | None) -> None:
        """恢复上一 epoch 已结算的账本（租约接管/重试后预算不清零）。

        只恢复计数，不恢复 ``exceeded``：上一 epoch 的超限由当时的
        ``RunOutcome`` 收口，新 epoch 应重新判断是否还能继续。
        """

        if not usage_json:
            return
        try:
            parsed = json.loads(usage_json)
        except (TypeError, ValueError):
            logger.warning("usage_json 无法解析，预算账本从零开始: %r", usage_json)
            return
        if not isinstance(parsed, dict):
            return
        self.input_tokens = _count(parsed.get("prompt_tokens"))
        self.output_tokens = _count(parsed.get("completion_tokens"))
        self.upstream_calls = _count(parsed.get("upstream_calls", parsed.get("calls")))
        self.provider_calls = _count(parsed.get("provider_calls"))
        self.estimated_calls = _count(parsed.get("estimated_calls"))

    # -- 查询 -------------------------------------------------------------- #

    @property
    def remaining_upstream_calls(self) -> int:
        return max(0, self.max_upstream_calls - self.upstream_calls)

    @property
    def remaining_input_tokens(self) -> int:
        return max(0, self.max_run_input_tokens - self.input_tokens - self.reserved_input_tokens)

    @property
    def remaining_output_tokens(self) -> int:
        return max(0, self.max_run_output_tokens - self.output_tokens - self.reserved_output_tokens)

    # -- 失败标记 ---------------------------------------------------------- #

    def _fail(self, code: str, reason: str) -> None:
        if not self.exceeded:
            self.exceeded = True
            self.exceeded_code = code
            self.exceeded_reason = reason
            logger.warning("预算超限 run_budget code=%s reason=%s", code, reason)

    def raise_if_exceeded(self) -> None:
        if self.exceeded:
            raise BudgetExceededError(
                self.exceeded_code or ErrorCode.BUDGET_EXCEEDED,
                self.exceeded_reason or "预算已用尽",
            )

    def ensure_required_calls(self, required: int, *, purpose: str) -> None:
        """开始一个必需步骤前的预检：调用次数/输入/输出预算都必须够。"""
        if self.exceeded:
            self.raise_if_exceeded()
        if self.upstream_calls + required > self.max_upstream_calls:
            reason = (
                f"上游调用次数预算不足（已用 {self.upstream_calls}/"
                f"{self.max_upstream_calls}，还需 {required}，purpose={purpose}）"
            )
            self._fail(ErrorCode.BUDGET_EXCEEDED, reason)
            raise BudgetExceededError(ErrorCode.BUDGET_EXCEEDED, reason)
        if self.remaining_input_tokens <= 0:
            reason = f"输入 token 预算不足（purpose={purpose}）"
            self._fail(ErrorCode.BUDGET_EXCEEDED, reason)
            raise BudgetExceededError(ErrorCode.BUDGET_EXCEEDED, reason)
        if self.remaining_output_tokens <= 0:
            reason = f"输出 token 预算不足（purpose={purpose}）"
            self._fail(ErrorCode.BUDGET_EXCEEDED, reason)
            raise BudgetExceededError(ErrorCode.BUDGET_EXCEEDED, reason)

    # -- 预留 / 结算 ------------------------------------------------------- #

    def begin_call(self, *, input_tokens: int, output_tokens: int) -> _Reservation:
        """预留一次上游调用；超限时抛 :class:`BudgetExceededError` 且不发请求。"""
        if self.exceeded:
            self.raise_if_exceeded()
        if input_tokens > self.max_context_input_tokens:
            reason = (
                f"单次上下文输入超过上限（{input_tokens} > {self.max_context_input_tokens}）"
            )
            self._fail(ErrorCode.CONTEXT_TOO_LARGE, reason)
            raise BudgetExceededError(ErrorCode.CONTEXT_TOO_LARGE, reason)
        if self.upstream_calls + 1 > self.max_upstream_calls:
            reason = (
                f"上游调用次数超限（{self.upstream_calls + 1} > {self.max_upstream_calls}）"
            )
            self._fail(ErrorCode.BUDGET_EXCEEDED, reason)
            raise BudgetExceededError(ErrorCode.BUDGET_EXCEEDED, reason)
        if input_tokens > self.remaining_input_tokens:
            reason = (
                f"输入 token 预算不足（需预留 {input_tokens}，剩余 {self.remaining_input_tokens}）"
            )
            self._fail(ErrorCode.BUDGET_EXCEEDED, reason)
            raise BudgetExceededError(ErrorCode.BUDGET_EXCEEDED, reason)
        if output_tokens > self.remaining_output_tokens:
            reason = (
                f"输出 token 预算不足（需预留 {output_tokens}，剩余 {self.remaining_output_tokens}）"
            )
            self._fail(ErrorCode.BUDGET_EXCEEDED, reason)
            raise BudgetExceededError(ErrorCode.BUDGET_EXCEEDED, reason)

        self.upstream_calls += 1
        self.reserved_input_tokens += input_tokens
        self.reserved_output_tokens += output_tokens
        return _Reservation(input_tokens=input_tokens, output_tokens=output_tokens)

    def finish_call(
        self,
        reservation: _Reservation,
        usage: LLMUsage | Any | None,
        *,
        extra_calls: int = 0,
        estimated_completion_tokens: int = 0,
    ) -> None:
        """结算一次调用：有原生 usage 用原生，否则按预留/估算保守记账。"""
        self.reserved_input_tokens = max(0, self.reserved_input_tokens - reservation.input_tokens)
        self.reserved_output_tokens = max(0, self.reserved_output_tokens - reservation.output_tokens)

        if usage is not None:
            prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
            completion = int(getattr(usage, "completion_tokens", 0) or 0)
            source = str(getattr(usage, "usage_source", "provider") or "provider")
            if source == "estimated":
                self.estimated_calls += 1
            else:
                self.provider_calls += 1
        else:
            # 失败/流式无 usage：按预留上限记账，避免崩溃后无限重试。
            prompt = reservation.input_tokens
            completion = max(reservation.output_tokens, estimated_completion_tokens)
            self.estimated_calls += 1

        self.input_tokens += prompt
        self.output_tokens += completion

        if extra_calls > 0:
            self.upstream_calls += extra_calls

        if self.upstream_calls > self.max_upstream_calls:
            self._fail(
                ErrorCode.BUDGET_EXCEEDED,
                f"上游调用次数超限（{self.upstream_calls} > {self.max_upstream_calls}）",
            )
        if self.input_tokens > self.max_run_input_tokens:
            self._fail(
                ErrorCode.BUDGET_EXCEEDED,
                f"输入 token 超限（{self.input_tokens} > {self.max_run_input_tokens}）",
            )
        if self.output_tokens > self.max_run_output_tokens:
            self._fail(
                ErrorCode.BUDGET_EXCEEDED,
                f"输出 token 超限（{self.output_tokens} > {self.max_run_output_tokens}）",
            )

    # -- 序列化 ------------------------------------------------------------ #

    def as_dict(self) -> dict[str, Any]:
        if self.estimated_calls == 0 and self.provider_calls > 0:
            usage_source = "provider"
        elif self.provider_calls == 0 and self.estimated_calls > 0:
            usage_source = "estimated"
        elif self.provider_calls and self.estimated_calls:
            usage_source = "mixed"
        else:
            usage_source = "provider"
        return {
            "prompt_tokens": self.input_tokens,
            "completion_tokens": self.output_tokens,
            "total_tokens": self.input_tokens + self.output_tokens,
            "calls": self.upstream_calls,
            "upstream_calls": self.upstream_calls,
            "provider_calls": self.provider_calls,
            "estimated_calls": self.estimated_calls,
            "usage_source": usage_source,
            "limits": {
                "max_run_input_tokens": self.max_run_input_tokens,
                "max_run_output_tokens": self.max_run_output_tokens,
                "max_upstream_calls": self.max_upstream_calls,
                "max_context_input_tokens": self.max_context_input_tokens,
                "max_output_tokens": self.max_output_tokens,
                "max_step_output_tokens": self.max_step_output_tokens,
                "step_timeout_seconds": self.step_timeout_seconds,
                "run_timeout_seconds": self.run_timeout_seconds,
            },
            "exceeded": self.exceeded,
            "warnings": list(self.warnings),
        }


_BUDGET_ALIASES: dict[str, str] = {
    "max_run_input_tokens": "max_run_input_tokens",
    "max_input_tokens": "max_run_input_tokens",
    "maxRunInputTokens": "max_run_input_tokens",
    "max_run_output_tokens": "max_run_output_tokens",
    "max_output_tokens_run": "max_run_output_tokens",
    "maxRunOutputTokens": "max_run_output_tokens",
    "max_upstream_calls": "max_upstream_calls",
    "maxUpstreamCalls": "max_upstream_calls",
    "max_context_input_tokens": "max_context_input_tokens",
    "maxContextInputTokens": "max_context_input_tokens",
    "max_output_tokens": "max_output_tokens",
    "maxOutputTokens": "max_output_tokens",
    "max_step_output_tokens": "max_step_output_tokens",
    "maxStepOutputTokens": "max_step_output_tokens",
    "step_timeout_seconds": "step_timeout_seconds",
    "stepTimeoutSeconds": "step_timeout_seconds",
    "run_timeout_seconds": "run_timeout_seconds",
    "runTimeoutSeconds": "run_timeout_seconds",
}


class BudgetedLLM:
    """``StepContext.llm`` 的预算包装：每次 ``complete*`` 都预留并结算 usage。"""

    def __init__(self, client: Any, budget: BudgetTracker) -> None:
        self._client = client
        self._budget = budget

    # 透传属性（model 等），便于工作流按需读取。
    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    async def complete(
        self,
        *,
        messages: list[dict],
        max_output_tokens: int | None = None,
        timeout_seconds: float = C.STEP_TIMEOUT_SECONDS,
        purpose: str = "",
        json_schema: dict | None = None,
        temperature: float = 0.3,
    ) -> Any:
        reserved_output = int(max_output_tokens or self._budget.max_output_tokens)
        reserved_output = min(reserved_output, self._budget.max_step_output_tokens)
        reservation = self._budget.begin_call(
            input_tokens=_estimate_prompt_tokens(messages),
            output_tokens=max(0, reserved_output),
        )
        try:
            result = await self._client.complete(
                messages=messages,
                max_output_tokens=max_output_tokens,
                timeout_seconds=timeout_seconds,
                purpose=purpose,
                json_schema=json_schema,
                temperature=temperature,
            )
        except BaseException:
            self._budget.finish_call(reservation, None)
            raise
        self._budget.finish_call(
            reservation,
            getattr(result, "usage", None),
            extra_calls=max(0, int(getattr(result, "attempts", 1) or 1) - 1),
        )
        self._budget.raise_if_exceeded()
        return result

    async def complete_text(self, **kwargs: Any) -> Any:
        return await self.complete(**kwargs)

    async def complete_json(
        self,
        *,
        messages: list[dict],
        max_output_tokens: int | None = None,
        timeout_seconds: float = C.STEP_TIMEOUT_SECONDS,
        purpose: str = "",
        json_schema: dict | None = None,
        temperature: float = 0.2,
    ) -> Any:
        reserved_output = int(max_output_tokens or self._budget.max_output_tokens)
        reserved_output = min(reserved_output, self._budget.max_step_output_tokens)
        reservation = self._budget.begin_call(
            input_tokens=_estimate_prompt_tokens(messages),
            output_tokens=max(0, reserved_output),
        )
        try:
            result = await self._client.complete_json(
                messages=messages,
                max_output_tokens=max_output_tokens,
                timeout_seconds=timeout_seconds,
                purpose=purpose,
                json_schema=json_schema,
                temperature=temperature,
            )
        except BaseException:
            self._budget.finish_call(reservation, None)
            raise
        parsed, llm_result = result
        self._budget.finish_call(
            reservation,
            getattr(llm_result, "usage", None),
            extra_calls=max(0, int(getattr(llm_result, "attempts", 1) or 1) - 1),
        )
        self._budget.raise_if_exceeded()
        return parsed, llm_result

    async def stream(self, messages: list[dict], *, temperature: float = 0.7, **kwargs: Any):
        reservation = self._budget.begin_call(
            input_tokens=_estimate_prompt_tokens(messages),
            output_tokens=self._budget.max_output_tokens,
        )
        produced = 0
        try:
            async for token in self._client.stream(messages, temperature=temperature, **kwargs):
                produced += len(token)
                yield token
        finally:
            # 流式无 usage：按实际字符数估算 completion。
            self._budget.finish_call(
                reservation, None, estimated_completion_tokens=produced // 3
            )


def _estimate_prompt_tokens(messages: list[dict]) -> int:
    parts: list[str] = []
    for message in messages or []:
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
    return len("\n".join(parts)) // 3


# --------------------------------------------------------------------------- #
# 步骤上下文（契约 §4.1）
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class StepContext:
    """传给工作流的最小上下文；工作流不得在 ``db`` 上写库、不得提交事务。"""

    db: Any
    run: Any
    session: Any
    product_state: C.ProductState
    current_message: Any | None
    task_kind: TaskKind
    llm: Any
    budget: BudgetTracker
    worker_id: str
    lease_epoch: int
    deadline_epoch: int
    #: 重试子 Run 的调度范围（父 Run 失败步骤 + 可复用的已成功步骤），普通 Run 为 None。
    retry_scope: dict[str, Any] | None = None
    #: 本次步骤执行是第几次上游尝试（1 起）。工作流据此判断：可重试的传输失败
    #: 是否还有额度交给 runner 重试，还是应当落成审计自身的失败状态。
    attempt_no: int = 1


# --------------------------------------------------------------------------- #
# 延迟导入 & 小工具
# --------------------------------------------------------------------------- #


def _repositories():
    from repositories import run_repo, session_repo

    return run_repo, session_repo


def _database():
    from repositories import database

    return database


def _make_outcome(**kwargs: Any) -> Any:
    """延迟导入 command_service.RunOutcome，避免与并行开发期互相阻塞。"""
    from services.command_service import RunOutcome

    return RunOutcome(**kwargs)


def _truncate(text: str, limit: int = 500) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _now_iso() -> str:
    return utc_now_iso()


def _task_kind_for(action: str) -> TaskKind | None:
    try:
        parsed = Action(str(action))
    except ValueError:
        return None
    return ACTION_TASK_KIND.get(parsed)


def _logical_key_for(task_kind: TaskKind) -> str:
    return LOGICAL_KEYS.get(task_kind, f"task.{task_kind.value}")


def _pick_current_message(messages: list[Any]) -> Any | None:
    """当前输入消息：优先最后一条人类消息，否则最后一条，无则 None。"""
    if not messages:
        return None
    for message in reversed(messages):
        if str(getattr(message, "sender_kind", "")) == SenderKind.human.value:
            return message
    return messages[-1]


def _latest_human_message(db: Any, session_id: str) -> Any | None:
    """最近一条人类消息（跨页最多回溯 300 条），没有则返回最后一条消息/None。

    触发异步命令的人类消息由 ``command_service`` 写入，其 ``run_id`` 为空，
    因此 ``run_repo.message_snapshot_for_run``（只返回该 Run 产生的消息）取不到；
    这里直接按会话消息倒序查找，供 ``StepContext.current_message`` 使用。
    """

    try:
        from repositories import session_repo
    except Exception:  # pragma: no cover - 仓储不可用时降级
        logger.exception("导入 session_repo 失败，无法读取当前消息")
        return None

    before_seq: int | None = None
    latest: Any | None = None
    for _ in range(3):
        rows = session_repo.list_messages(db, session_id, before_seq=before_seq, limit=100)
        if not rows:
            return latest
        for row in reversed(rows):
            if str(getattr(row, "sender_kind", "")) == SenderKind.human.value:
                return row
            if latest is None:
                latest = row
        before_seq = int(rows[0].seq)
    return latest


def _lease_owned(run: Any, worker_id: str, lease_epoch: int) -> bool:
    return run.lease_owner == worker_id and int(run.lease_epoch or 0) == int(lease_epoch or 0)


def _has_terminal_event(db, run_repo, run_id: str) -> bool:
    terminal = {event.value for event in C.TERMINAL_EVENT_TYPES}
    try:
        events = run_repo.events_after(db, run_id, after_seq=0, limit=500)
    except Exception:  # pragma: no cover - 查询失败按“未终结”处理，宁可重复也不漏
        logger.exception("查询终结事件失败 run=%s", run_id)
        return False
    return any(str(getattr(event, "type", "")) in terminal for event in events)


def _resolve_dispatch(task_kind: TaskKind) -> Callable[[StepContext], Any] | None:
    """解析工作流函数：显式注册优先，其次按惰性描述导入，最后返回 ``None``。"""
    registered = WORKFLOW_DISPATCH.get(task_kind)
    if registered is not None:
        return registered
    ref = WORKFLOWS.get(task_kind)
    if ref is None:
        return None
    module_name, attr_name = ref
    try:
        module = importlib.import_module(module_name)
        dispatch = getattr(module, attr_name)
    except (ImportError, AttributeError) as exc:
        logger.error(
            "工作流尚不可用 task_kind=%s target=%s.%s：%s",
            task_kind.value,
            module_name,
            attr_name,
            exc,
        )
        return None
    if not callable(dispatch):
        logger.error("工作流入口不可调用 target=%s.%s", module_name, attr_name)
        return None
    WORKFLOW_DISPATCH[task_kind] = dispatch
    return dispatch


# --------------------------------------------------------------------------- #
# 异步调用兼容（工作流可能 sync 或 async）
# --------------------------------------------------------------------------- #


def _run_coroutine(coro: Awaitable[Any], timeout_seconds: float) -> Any:
    async def _wrapped() -> Any:
        return await asyncio.wait_for(coro, timeout=timeout_seconds)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_wrapped())

    # 已在事件循环中（如 FastAPI 测试环境）：换一个线程跑独立循环。
    box: dict[str, Any] = {}

    def _target() -> None:
        try:
            box["result"] = asyncio.run(_wrapped())
        except BaseException as exc:  # noqa: BLE001 - 原样回传
            box["error"] = exc

    thread = threading.Thread(target=_target, name="run-dispatch", daemon=True)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box.get("result")


def _invoke_dispatch(
    dispatch: Callable[[StepContext], Any], ctx: StepContext, timeout_seconds: float
) -> Any:
    result = dispatch(ctx)
    if inspect.isawaitable(result):
        if timeout_seconds <= 0:
            raise asyncio.TimeoutError("步骤已超过期限")
        return _run_coroutine(result, timeout_seconds)
    return result


# --------------------------------------------------------------------------- #
# Run 快照
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _RunSnapshot:
    id: str
    session_id: str
    action: str
    status: str
    lease_epoch: int
    deadline_at: int | None
    base_product_version: int
    budget_json: str | None
    usage_json: str | None
    session_deleted: bool


def _read_run_snapshot(run_id: str, *, worker_id: str) -> _RunSnapshot | None:
    """初始读取（短事务）；已终结 / 不存在 / 会话已删 都会在返回前判定。"""
    run_repo, session_repo = _repositories()
    with _database().session_scope() as db:
        run = run_repo.get_run(db, run_id)
        if run is None:
            return None
        if SM.is_terminal_run(RunStatus(run.status)):
            return None
        session = session_repo.get_session(db, run.session_id, include_deleted=True)
        session_deleted = session is None or bool(session.deleted_at)

        # 记录首个 run.started 事件（重放不重复写）。已删除会话 / 未领取的 Run
        # 一律不写，避免“未知或已终结 Run 不产生新写入”。
        claimed_by_us = run.lease_owner == worker_id
        if not session_deleted and claimed_by_us:
            try:
                events = run_repo.events_after(db, run_id, after_seq=0, limit=500)
                if not any(
                    str(getattr(e, "type", "")) == EventType.run_started.value for e in events
                ):
                    run_repo.append_event(
                        db,
                        session_id=run.session_id,
                        run_id=run_id,
                        type=EventType.run_started.value,
                        payload={"worker_id": worker_id},
                        now_iso=_now_iso(),
                    )
            except Exception:  # pragma: no cover - 事件写入失败不阻塞执行
                logger.exception("写 run.started 事件失败 run=%s", run_id)

        return _RunSnapshot(
            id=run.id,
            session_id=run.session_id,
            action=str(run.action),
            status=str(run.status),
            lease_epoch=int(run.lease_epoch or 0),
            deadline_at=int(run.deadline_at) if run.deadline_at else None,
            base_product_version=int(run.base_product_version or 1),
            budget_json=run.budget_json,
            usage_json=run.usage_json,
            session_deleted=session_deleted,
        )


# --------------------------------------------------------------------------- #
# 提交 / 终结
# --------------------------------------------------------------------------- #


def _submit_outcome(
    *,
    run_id: str,
    worker_id: str,
    lease_epoch: int,
    outcome: Any,
    now: Callable[[], float],
) -> str:
    """调用 command_service.apply_outcome（同一事务写全部结果）。

    返回：``applied`` / ``already_terminal`` / ``lease_lost`` / ``session_deleted`` /
    ``missing`` / ``error``。任何非 applied 都不代表结果已落库。
    """
    run_repo, session_repo = _repositories()
    from services.command_service import apply_outcome

    try:
        with _database().session_scope() as db:
            run = run_repo.get_run(db, run_id)
            if run is None:
                return "missing"
            if SM.is_terminal_run(RunStatus(run.status)):
                return "already_terminal"
            session = session_repo.get_session(db, run.session_id, include_deleted=True)
            if session is None or session.deleted_at:
                return "session_deleted"
            if not _lease_owned(run, worker_id, lease_epoch):
                logger.warning(
                    "拒绝提交：租约已丢失 run=%s owner=%s/%s epoch=%s/%s",
                    run_id,
                    run.lease_owner,
                    worker_id,
                    run.lease_epoch,
                    lease_epoch,
                )
                return "lease_lost"

            # 期限兜底：成功结果在截止后返回，按 PRD 提交 RUN_DEADLINE_EXCEEDED。
            if (
                run.deadline_at
                and int(now()) >= int(run.deadline_at)
                and RunStatus(outcome.status) in (RunStatus.succeeded, RunStatus.partial_succeeded)
            ):
                warnings = list(getattr(outcome, "warnings", []) or [])
                warnings.append("RESULT_DISCARDED:DEADLINE_EXCEEDED")
                outcome = _make_outcome(
                    status=RunStatus.failed,
                    error_code=ErrorCode.RUN_DEADLINE_EXCEEDED,
                    error_message="Run 已超过截止时间，迟到的结果被丢弃",
                    warnings=warnings,
                    usage=getattr(outcome, "usage", None),
                )

            apply_outcome(
                db,
                run_id=run_id,
                worker_id=worker_id,
                lease_epoch=int(lease_epoch),
                outcome=outcome,
                now_iso=_now_iso(),
            )
        return "applied"
    except AppError as exc:
        if exc.code == ErrorCode.LEASE_LOST:
            logger.warning("提交时租约已丢失 run=%s：%s", run_id, exc.message)
            return "lease_lost"
        logger.exception("结果提交被拒绝 run=%s code=%s", run_id, exc.code)
        return "error"
    except Exception:  # noqa: BLE001 - 提交失败必须在调用方兜底终结
        logger.exception("结果提交异常 run=%s", run_id)
        return "error"


def _force_terminal(
    *,
    run_id: str,
    worker_id: str,
    lease_epoch: int | None,
    status: RunStatus,
    error_code: str | None,
    error_message: str | None,
    bump_session_version: bool,
    usage: dict[str, Any] | None = None,
) -> bool:
    """兜底终结：绕过正常提交路径直接写 Run 终态（仅在 apply_outcome 不可用时）。

    仅在确认租约仍属于本 worker（或租约已清空）时执行，保证“恰好一个终结事件”。
    """
    from sqlalchemy import update as sa_update

    from repositories.database import RunRow, SessionRow

    run_repo, _session_repo = _repositories()
    try:
        with _database().session_scope() as db:
            run = db.get(RunRow, run_id)
            if run is None:
                return False
            if SM.is_terminal_run(RunStatus(run.status)):
                return False
            if run.lease_owner not in (None, worker_id):
                logger.warning(
                    "兜底终结放弃：租约属于 %s（本地 %s） run=%s",
                    run.lease_owner,
                    worker_id,
                    run_id,
                )
                return False
            if (
                lease_epoch is not None
                and run.lease_owner == worker_id
                and int(run.lease_epoch or 0) != int(lease_epoch)
            ):
                logger.warning("兜底终结放弃：lease_epoch 不匹配 run=%s", run_id)
                return False

            now_iso = _now_iso()
            db.execute(
                sa_update(RunRow)
                .where(RunRow.id == run_id)
                .values(
                    status=status.value,
                    finished_at=now_iso,
                    error_code=error_code,
                    error_message=error_message,
                    lease_owner=None,
                    lease_expires_at=None,
                    **({"usage_json": canonical_json(usage)} if usage is not None else {}),
                )
            )
            if not _has_terminal_event(db, run_repo, run_id):
                event_type = SM.terminal_event_for(status) or EventType.run_failed
                run_repo.append_event(
                    db,
                    session_id=run.session_id,
                    run_id=run_id,
                    type=event_type.value,
                    payload={
                        "status": status.value,
                        "error_code": error_code,
                        "message": error_message,
                        "recovery": "force_terminal",
                    },
                    now_iso=now_iso,
                )
            # 闭环收口（SRV-03）：兜底路径绕过了 apply_outcome，必须显式结算闭环，
            # 否则 loops/loop_rounds 永久停在 running，start_loop 撞 LOOP_ACTIVE。
            from services.command_service import settle_loop_for_run_failure

            settle_loop_for_run_failure(
                db, run_id=run_id, now_iso=now_iso, note=error_message or None
            )
            if run.session_id:
                values: dict[str, Any] = {}
                session = db.get(SessionRow, run.session_id)
                if session is not None and session.active_run_id == run_id:
                    values["active_run_id"] = None
                if bump_session_version:
                    values["session_version"] = SessionRow.session_version + 1
                if values:
                    db.execute(
                        sa_update(SessionRow)
                        .where(SessionRow.id == run.session_id)
                        .values(**values)
                    )
        logger.warning("已兜底终结 run=%s status=%s code=%s", run_id, status.value, error_code)
        return True
    except Exception:  # noqa: BLE001 - 最后一道防线，只能记录
        logger.exception("兜底终结失败 run=%s", run_id)
        return False


def _terminate_session_deleted(
    *, run_id: str, worker_id: str, lease_epoch: int
) -> None:
    """会话已删除：Run 直接 cancelled(NOT_FOUND)，不写任何结果。"""
    logger.warning("会话已删除，终止 run=%s", run_id)
    _force_terminal(
        run_id=run_id,
        worker_id=worker_id,
        lease_epoch=lease_epoch,
        status=RunStatus.cancelled,
        error_code=ErrorCode.NOT_FOUND,
        error_message="会话已删除，任务终止",
        bump_session_version=False,
    )


def _finalize_failure(
    *,
    run_id: str,
    worker_id: str,
    lease_epoch: int,
    code: str,
    message: str,
    warnings: list[str] | None = None,
    usage: dict[str, Any] | None = None,
    now: Callable[[], float],
) -> None:
    """把 Run 终结为 failed；apply_outcome 不可用时走兜底。

    ``usage`` 为截至当前的累计账本（:meth:`BudgetTracker.as_dict`），随终结
    事务写入 ``runs.usage_json``；预算超限路径同样携带，便于审计已花费的调用。
    """
    outcome = _make_outcome(
        status=RunStatus.failed,
        error_code=code,
        error_message=_truncate(message),
        warnings=list(warnings or []),
        usage=usage,
    )
    result = _submit_outcome(
        run_id=run_id,
        worker_id=worker_id,
        lease_epoch=lease_epoch,
        outcome=outcome,
        now=now,
    )
    logger.info("Run 失败终结 run=%s code=%s commit=%s", run_id, code, result)
    if result in ("applied", "already_terminal", "lease_lost", "missing"):
        return
    if result == "session_deleted":
        _terminate_session_deleted(run_id=run_id, worker_id=worker_id, lease_epoch=lease_epoch)
        return
    _force_terminal(
        run_id=run_id,
        worker_id=worker_id,
        lease_epoch=lease_epoch,
        status=RunStatus.failed,
        error_code=ErrorCode.INTERNAL,
        error_message="结果提交失败，任务已终止",
        bump_session_version=True,
        usage=usage,
    )


# --------------------------------------------------------------------------- #
# 步骤小事务
# --------------------------------------------------------------------------- #


def _finish_step(
    *,
    run_id: str,
    logical_key: str,
    status: StepStatus,
    output: dict | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    worker_id: str,
    lease_epoch: int,
) -> bool:
    """写步骤结果；租约已丢时静默跳过（不写回任何内容）。"""
    run_repo, _ = _repositories()
    try:
        with _database().session_scope() as db:
            run = run_repo.get_run(db, run_id)
            if run is None or SM.is_terminal_run(RunStatus(run.status)):
                return False
            if not _lease_owned(run, worker_id, lease_epoch):
                logger.warning("步骤结果放弃：租约已丢失 run=%s key=%s", run_id, logical_key)
                return False
            step = run_repo.get_step_by_key(db, run_id, logical_key)
            if step is None:
                return False
            run_repo.finish_step(
                db,
                step,
                status=status,
                output=output,
                error_code=error_code,
                error_message=_truncate(error_message) if error_message else None,
                now_iso=_now_iso(),
            )
            return True
    except Exception:  # noqa: BLE001 - 步骤状态写失败不应掩盖原始错误
        logger.exception("写步骤状态失败 run=%s key=%s status=%s", run_id, logical_key, status)
        return False


def _persist_budget(
    *,
    run_id: str,
    worker_id: str,
    lease_epoch: int,
    budget: BudgetTracker,
) -> bool:
    """把当前预算账本写入 ``runs.usage_json``（不改 Run 状态）。

    只在步骤已经落库（每步至多一次）之后调用，使接管者能从上一 epoch 的
    计数继续；写失败不阻塞执行——账本最坏退化为「从本 epoch 重新计数」，
    终结时 ``release_run`` 仍会用最终账本覆盖 ``usage_json``。
    """

    run_repo, _ = _repositories()
    try:
        with _database().session_scope() as db:
            run = run_repo.get_run(db, run_id)
            if run is None or SM.is_terminal_run(RunStatus(run.status)):
                return False
            return run_repo.save_usage(
                db,
                run,
                usage=budget.as_dict(),
                worker_id=worker_id,
                lease_epoch=lease_epoch,
            )
    except Exception:  # noqa: BLE001 - 账本落盘失败不应中断执行
        logger.exception("写预算账本失败 run=%s", run_id)
        return False


def _start_step_attempt(
    *,
    run_id: str,
    logical_key: str,
    worker_id: str,
    lease_epoch: int,
) -> int | None:
    """start_step + step.started 事件；返回本次尝试序号，失败返回 None。"""
    run_repo, _ = _repositories()
    try:
        with _database().session_scope() as db:
            run = run_repo.get_run(db, run_id)
            if run is None or SM.is_terminal_run(RunStatus(run.status)):
                return None
            if not _lease_owned(run, worker_id, lease_epoch):
                return None
            step = run_repo.get_step_by_key(db, run_id, logical_key)
            if step is None:
                return None
            try:
                # 仓储层内部会用 validate_step_transition 校验合法性
                # （failed/cancelled → running 允许重试）。
                run_repo.start_step(db, step, now_iso=_now_iso())
            except AppError as exc:
                logger.warning(
                    "步骤状态不允许重试 run=%s key=%s status=%s code=%s",
                    run_id,
                    logical_key,
                    step.status,
                    exc.code,
                )
                return None
            attempt_no = int(step.attempt_count or 0)
            run_repo.append_event(
                db,
                session_id=run.session_id,
                run_id=run_id,
                type=EventType.step_started.value,
                payload={"logical_key": logical_key, "attempt": attempt_no},
                now_iso=_now_iso(),
            )
            return attempt_no
    except Exception:  # noqa: BLE001
        logger.exception("启动步骤失败 run=%s key=%s", run_id, logical_key)
        return None


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #


def execute_run(
    run_id: str,
    *,
    worker_id: str,
    llm_factory: Callable[[], Any] | None = None,
    now: Callable[[], float] = time.time,
    renew_thread: bool = True,
) -> None:
    """执行一个已被本 worker 领取（持有租约）的 Run。"""
    try:
        snapshot = _read_run_snapshot(run_id, worker_id=worker_id)
    except Exception:
        logger.exception("读取 Run 失败，放弃执行 run=%s", run_id)
        return

    if snapshot is None:
        logger.info("Run 不存在或已终结，跳过 run=%s", run_id)
        return
    if snapshot.session_deleted:
        _terminate_session_deleted(
            run_id=run_id, worker_id=worker_id, lease_epoch=snapshot.lease_epoch
        )
        return

    # 预算账本跨 epoch 连续：上一 worker 已结算的计数从 runs.usage_json 恢复，
    # 避免租约接管/重试后 upstream_calls 清零、突破单 Run 上限。
    budget = BudgetTracker.from_budget_json(snapshot.budget_json, snapshot.usage_json)
    client = llm_factory() if llm_factory is not None else LLMClient()

    stop_event = threading.Event()
    lease_lost = threading.Event()
    renewer: threading.Thread | None = None
    if renew_thread:
        renewer = _start_lease_renewer(
            run_id=run_id,
            worker_id=worker_id,
            lease_epoch=snapshot.lease_epoch,
            stop_event=stop_event,
            lease_lost=lease_lost,
        )

    try:
        _drive_run(
            run_id=run_id,
            worker_id=worker_id,
            snapshot=snapshot,
            budget=budget,
            client=client,
            now=now,
            lease_lost=lease_lost,
        )
    except Exception as exc:  # noqa: BLE001 - 最后一道防线：绝不留下 running
        logger.exception("Run 执行异常 run=%s", run_id)
        if lease_lost.is_set():
            logger.warning("租约已丢失，跳过异常终结写回 run=%s", run_id)
            return
        _finalize_failure(
            run_id=run_id,
            worker_id=worker_id,
            lease_epoch=snapshot.lease_epoch,
            code=ErrorCode.INTERNAL,
            message=f"{type(exc).__name__}: {exc}",
            warnings=list(budget.warnings),
            usage=budget.as_dict(),
            now=now,
        )
    finally:
        stop_event.set()
        if renewer is not None:
            renewer.join(timeout=float(C.LEASE_RENEW_SECONDS) + 1.0)


def _start_lease_renewer(
    *,
    run_id: str,
    worker_id: str,
    lease_epoch: int,
    stop_event: threading.Event,
    lease_lost: threading.Event,
) -> threading.Thread:
    """后台续约线程；``renew_lease`` 返回 False 即视为租约丢失。"""

    def _loop() -> None:
        run_repo, _ = _repositories()
        while not stop_event.wait(float(C.LEASE_RENEW_SECONDS)):
            try:
                with _database().session_scope() as db:
                    run = run_repo.get_run(db, run_id)
                    if run is None:
                        logger.warning("续约时 Run 已消失，停止续约 run=%s", run_id)
                        lease_lost.set()
                        return
                    ok = run_repo.renew_lease(
                        db,
                        run,
                        worker_id=worker_id,
                        lease_epoch=int(lease_epoch),
                        lease_seconds=C.LEASE_SECONDS,
                        now_epoch=int(time.time()),
                    )
            except Exception:  # noqa: BLE001 - 续约异常不致命
                logger.exception("租约续约异常 run=%s", run_id)
                continue
            if not ok:
                logger.warning("租约续约失败，标记丢失 run=%s worker=%s", run_id, worker_id)
                lease_lost.set()
                return

    thread = threading.Thread(target=_loop, name=f"lease-{run_id[:8]}", daemon=True)
    thread.start()
    return thread


def _retry_scope_for(db: Any, run: Any, run_repo: Any) -> dict[str, Any] | None:
    """构建重试子 Run 的调度范围。

    父 Run 里未成功的 ``logical_key`` 由 ``retry_run`` 复制到 ``failed_steps_json``；
    已成功步骤的结构化产物（brainstorm 的 RoleResult）随 step 落库，这里读出后交给
    工作流复用，避免重复调用已成功的专家。非重试 Run / 父 Run 已不可用时返回 None。
    """

    try:
        failed_keys = [
            str(key) for key in json.loads(getattr(run, "failed_steps_json", None) or "[]")
        ]
    except (TypeError, ValueError):
        failed_keys = []
    parent_id = getattr(run, "parent_run_id", None)
    if not failed_keys or not parent_id:
        return None
    parent = run_repo.get_run(db, parent_id)
    # 只有“同一 action 的重试子 Run”才继承步骤；brainstorm→synthesis 的后续任务不继承。
    if parent is None or str(parent.action) != str(run.action):
        return None
    if RunStatus(parent.status) not in (RunStatus.failed, RunStatus.partial_succeeded):
        return None

    reused: dict[str, dict[str, Any]] = {}
    for step in run_repo.list_steps(db, parent_id):
        if StepStatus(step.status) is not StepStatus.succeeded:
            continue
        try:
            output = json.loads(step.output_json or "{}")
        except (TypeError, ValueError):
            continue
        result = output.get("result")
        if not isinstance(result, dict):
            continue
        reused[str(step.logical_key)] = {
            "step_id": step.id,
            "role_code": output.get("role_code"),
            "candidates": output.get("candidates") or {},
            "result": result,
        }
    return {"parent_run_id": parent_id, "failed_keys": failed_keys, "reused": reused}


def _drive_run(
    *,
    run_id: str,
    worker_id: str,
    snapshot: _RunSnapshot,
    budget: BudgetTracker,
    client: Any,
    now: Callable[[], float],
    lease_lost: threading.Event,
) -> None:
    """步骤循环：每次迭代只做“读状态 → 一步执行 → 提交”，不跨事务持有写锁。"""
    run_repo, session_repo = _repositories()

    while True:
        if lease_lost.is_set():
            logger.warning("租约已丢失，停止执行 run=%s", run_id)
            return

        # ---------- 1) 读取 Run/步骤状态（短事务，无模型调用） ---------- #
        with _database().session_scope() as db:
            run = run_repo.get_run(db, run_id)
            if run is None:
                return
            if SM.is_terminal_run(RunStatus(run.status)):
                return
            session = session_repo.get_session(db, run.session_id, include_deleted=True)
            if session is None or session.deleted_at:
                deleted = True
                run_deadline = None
                action = str(run.action)
            else:
                deleted = False
                run_deadline = int(run.deadline_at) if run.deadline_at else None
                action = str(run.action)
                current_status = RunStatus(run.status)

                # 取消请求优先：不执行/不提交任何新结果。
                if current_status is RunStatus.cancel_requested:
                    outcome = _make_outcome(
                        status=RunStatus.cancelled,
                        error_code=None,
                        error_message="收到取消请求，任务已终止",
                        warnings=list(budget.warnings),
                        usage=budget.as_dict(),
                    )
                    lease_epoch = int(run.lease_epoch or 0)
                    break_commit = True
                else:
                    break_commit = False
                    lease_epoch = int(run.lease_epoch or 0)

        if deleted:
            _terminate_session_deleted(
                run_id=run_id, worker_id=worker_id, lease_epoch=snapshot.lease_epoch
            )
            return
        if break_commit:
            result = _submit_outcome(
                run_id=run_id,
                worker_id=worker_id,
                lease_epoch=lease_epoch,
                outcome=outcome,
                now=now,
            )
            logger.info("取消提交 run=%s commit=%s", run_id, result)
            if result == "session_deleted":
                _terminate_session_deleted(
                    run_id=run_id, worker_id=worker_id, lease_epoch=lease_epoch
                )
            return

        # ---------- 2) 期限检查 ---------- #
        now_epoch = int(now())
        if run_deadline is not None and now_epoch >= run_deadline:
            _finalize_failure(
                run_id=run_id,
                worker_id=worker_id,
                lease_epoch=lease_epoch,
                code=ErrorCode.RUN_DEADLINE_EXCEEDED,
                message=f"Run 已超过截止时间（deadline_at={run_deadline}）",
                warnings=list(budget.warnings),
                usage=budget.as_dict(),
                now=now,
            )
            return

        # ---------- 3) 解析任务类型与步骤 ---------- #
        task_kind = _task_kind_for(action)
        if task_kind is None:
            _finalize_failure(
                run_id=run_id,
                worker_id=worker_id,
                lease_epoch=lease_epoch,
                code=ErrorCode.FEATURE_NOT_READY,
                message=f"暂不支持的 action: {action}",
                warnings=list(budget.warnings),
                usage=budget.as_dict(),
                now=now,
            )
            return
        logical_key = _logical_key_for(task_kind)

        try:
            budget.ensure_required_calls(1, purpose=logical_key)
        except BudgetExceededError as exc:
            _finalize_failure(
                run_id=run_id,
                worker_id=worker_id,
                lease_epoch=lease_epoch,
                code=exc.code,
                message=exc.message,
                warnings=list(budget.warnings),
                usage=budget.as_dict(),
                now=now,
            )
            return

        # 创建/读取步骤 + 组装上下文所需的快照（同一短事务）
        retry_scope: dict[str, Any] | None = None
        with _database().session_scope() as db:
            run = run_repo.get_run(db, run_id)
            if run is None or SM.is_terminal_run(RunStatus(run.status)):
                return
            if not _lease_owned(run, worker_id, lease_epoch):
                logger.warning("租约已丢失，停止 run=%s", run_id)
                return
            session = session_repo.get_session(db, run.session_id, include_deleted=True)
            if session is None or session.deleted_at:
                deleted = True
                run_row = None
            else:
                deleted = False
                run_row = run
                session_row = session
                product_state = session_repo.get_product_state(
                    db, run.session_id, run.base_product_version
                )
                try:
                    current_message = _latest_human_message(db, run.session_id)
                except Exception:  # pragma: no cover - 读取失败降级为无当前消息
                    logger.exception("读取当前消息失败 run=%s", run_id)
                    current_message = None

                retry_scope = _retry_scope_for(db, run, run_repo)
                role_code = STEP_ROLE.get(task_kind)
                step = run_repo.ensure_step(
                    db,
                    run,
                    logical_key=logical_key,
                    kind=task_kind.value,
                    role_code=role_code,
                    input_hash=str(run.input_hash or "") or None,
                    input_snapshot={
                        "run_id": run_id,
                        "action": action,
                        "base_product_version": int(run.base_product_version or 1),
                        "base_session_version": int(run.base_session_version or 0),
                    },
                    now_iso=_now_iso(),
                )
                step_status = StepStatus(step.status)
                attempt_count = int(step.attempt_count or 0)
                step_error_code = step.error_code

        if deleted:
            _terminate_session_deleted(
                run_id=run_id, worker_id=worker_id, lease_epoch=lease_epoch
            )
            return

        # 已成功的步骤：幂等重放，不重复调用模型/写消息。
        if step_status is StepStatus.succeeded:
            logger.info("步骤已成功，幂等重放 run=%s key=%s", run_id, logical_key)
            outcome = _make_outcome(
                status=RunStatus.succeeded,
                warnings=list(budget.warnings) + ["RECOVERED_FROM_DURABLE_STEP"],
                usage=budget.as_dict(),
            )
            result = _submit_outcome(
                run_id=run_id,
                worker_id=worker_id,
                lease_epoch=lease_epoch,
                outcome=outcome,
                now=now,
            )
            logger.info("幂等重放终结 run=%s commit=%s", run_id, result)
            if result == "session_deleted":
                _terminate_session_deleted(
                    run_id=run_id, worker_id=worker_id, lease_epoch=lease_epoch
                )
            elif result == "error":
                _force_terminal(
                    run_id=run_id,
                    worker_id=worker_id,
                    lease_epoch=lease_epoch,
                    status=RunStatus.failed,
                    error_code=ErrorCode.INTERNAL,
                    error_message="幂等重放终结失败",
                    bump_session_version=True,
                )
            return

        # 尝试次数已耗尽（含崩溃边界已启动过的尝试）→ 直接失败。
        if attempt_count >= MAX_STEP_ATTEMPTS:
            _finalize_failure(
                run_id=run_id,
                worker_id=worker_id,
                lease_epoch=lease_epoch,
                code=str(step_error_code or ErrorCode.INTERNAL),
                message=f"步骤 {logical_key} 已用尽 {MAX_STEP_ATTEMPTS} 次尝试",
                warnings=list(budget.warnings),
                usage=budget.as_dict(),
                now=now,
            )
            return

        dispatch = _resolve_dispatch(task_kind)
        if dispatch is None:
            _start_step_attempt(
                run_id=run_id,
                logical_key=logical_key,
                worker_id=worker_id,
                lease_epoch=lease_epoch,
            )
            _finish_step(
                run_id=run_id,
                logical_key=logical_key,
                status=StepStatus.failed,
                error_code=ErrorCode.FEATURE_NOT_READY,
                error_message=f"任务类型 {task_kind.value} 尚未实现",
                worker_id=worker_id,
                lease_epoch=lease_epoch,
            )
            _finalize_failure(
                run_id=run_id,
                worker_id=worker_id,
                lease_epoch=lease_epoch,
                code=ErrorCode.FEATURE_NOT_READY,
                message=f"任务类型 {task_kind.value} 尚未实现（M1 仅支持 clarify）",
                warnings=list(budget.warnings),
                usage=budget.as_dict(),
                now=now,
            )
            return

        # ---------- 4) 启动一次步骤尝试（短事务） ---------- #
        attempt_no = _start_step_attempt(
            run_id=run_id,
            logical_key=logical_key,
            worker_id=worker_id,
            lease_epoch=lease_epoch,
        )
        if attempt_no is None:
            logger.warning("无法启动步骤尝试，结束 run=%s key=%s", run_id, logical_key)
            return

        # ---------- 5) 调工作流（事务外；只读会话） ---------- #
        workflow_db = _database().new_session()
        step_started_at = now()
        deadline_epoch = run_deadline or int(step_started_at + budget.run_timeout_seconds)
        remaining = float(int(deadline_epoch) - int(step_started_at))
        workflow_timeout = min(float(budget.step_timeout_seconds), max(0.0, remaining))

        ctx = StepContext(
            db=workflow_db,
            run=run_row,
            session=session_row,
            product_state=product_state,
            current_message=current_message,
            task_kind=task_kind,
            llm=BudgetedLLM(client, budget),
            budget=budget,
            worker_id=worker_id,
            lease_epoch=lease_epoch,
            deadline_epoch=int(deadline_epoch),
            retry_scope=retry_scope,
            attempt_no=int(attempt_no),
        )
        try:
            outcome = _invoke_dispatch(dispatch, ctx, workflow_timeout)
        except BudgetExceededError as exc:
            workflow_db.close()
            _finish_step(
                run_id=run_id,
                logical_key=logical_key,
                status=StepStatus.failed,
                error_code=exc.code,
                error_message=exc.message,
                worker_id=worker_id,
                lease_epoch=lease_epoch,
            )
            _finalize_failure(
                run_id=run_id,
                worker_id=worker_id,
                lease_epoch=lease_epoch,
                code=exc.code,
                message=exc.message,
                warnings=list(budget.warnings),
                usage=budget.as_dict(),
                now=now,
            )
            return
        except LLMError as exc:
            workflow_db.close()
            logger.warning(
                "上游调用失败 run=%s key=%s attempt=%s code=%s retryable=%s",
                run_id,
                logical_key,
                attempt_no,
                exc.code,
                exc.retryable,
            )
            _finish_step(
                run_id=run_id,
                logical_key=logical_key,
                status=StepStatus.failed,
                error_code=exc.code,
                error_message=str(exc),
                worker_id=worker_id,
                lease_epoch=lease_epoch,
            )
            # 失败也要落盘：可重试失败后接管者必须看到已消耗的调用次数。
            _persist_budget(
                run_id=run_id,
                worker_id=worker_id,
                lease_epoch=lease_epoch,
                budget=budget,
            )
            if exc.retryable and attempt_no < MAX_STEP_ATTEMPTS and not lease_lost.is_set():
                continue  # 下一步循环重新 start_step（attempt 2）
            _finalize_failure(
                run_id=run_id,
                worker_id=worker_id,
                lease_epoch=lease_epoch,
                code=exc.code,
                message=str(exc),
                warnings=list(budget.warnings),
                usage=budget.as_dict(),
                now=now,
            )
            return
        except (asyncio.TimeoutError, TimeoutError) as exc:
            workflow_db.close()
            _finish_step(
                run_id=run_id,
                logical_key=logical_key,
                status=StepStatus.failed,
                error_code=ErrorCode.RUN_DEADLINE_EXCEEDED,
                error_message=str(exc) or "步骤执行超时",
                worker_id=worker_id,
                lease_epoch=lease_epoch,
            )
            # 步骤级超时统一映射到已注册的 RUN_DEADLINE_EXCEEDED（前端按 code 分支）。
            code = ErrorCode.RUN_DEADLINE_EXCEEDED
            _finalize_failure(
                run_id=run_id,
                worker_id=worker_id,
                lease_epoch=lease_epoch,
                code=code,
                message=f"步骤 {logical_key} 超时",
                warnings=list(budget.warnings),
                usage=budget.as_dict(),
                now=now,
            )
            return
        except Exception as exc:  # noqa: BLE001
            workflow_db.close()
            _finish_step(
                run_id=run_id,
                logical_key=logical_key,
                status=StepStatus.failed,
                error_code=ErrorCode.INTERNAL,
                error_message=f"{type(exc).__name__}: {exc}",
                worker_id=worker_id,
                lease_epoch=lease_epoch,
            )
            raise
        finally:
            try:
                workflow_db.close()
            except Exception:  # pragma: no cover
                pass

        # 工作流返回了非 RunOutcome（多半是契约误用）
        if not hasattr(outcome, "status"):
            _finish_step(
                run_id=run_id,
                logical_key=logical_key,
                status=StepStatus.failed,
                error_code=ErrorCode.INTERNAL,
                error_message="工作流返回值不是 RunOutcome",
                worker_id=worker_id,
                lease_epoch=lease_epoch,
            )
            raise TypeError(f"工作流返回值不是 RunOutcome: {type(outcome)!r}")

        # ---------- 6) 预算兜底：模型调用把预算打穿则丢弃结果 ---------- #
        if budget.exceeded:
            _finish_step(
                run_id=run_id,
                logical_key=logical_key,
                status=StepStatus.failed,
                error_code=budget.exceeded_code or ErrorCode.BUDGET_EXCEEDED,
                error_message=budget.exceeded_reason,
                worker_id=worker_id,
                lease_epoch=lease_epoch,
            )
            _finalize_failure(
                run_id=run_id,
                worker_id=worker_id,
                lease_epoch=lease_epoch,
                code=budget.exceeded_code or ErrorCode.BUDGET_EXCEEDED,
                message=budget.exceeded_reason or "预算超限",
                warnings=list(budget.warnings),
                usage=budget.as_dict(),
                now=now,
            )
            return

        # ---------- 7) 写步骤成功 + 提交结果 ---------- #
        _finish_step(
            run_id=run_id,
            logical_key=logical_key,
            status=StepStatus.succeeded,
            output={
                "event_types": [
                    str(event.get("type")) for event in getattr(outcome, "events", []) or []
                    if isinstance(event, dict)
                ],
                "message_count": len(getattr(outcome, "messages", []) or []),
            },
            worker_id=worker_id,
            lease_epoch=lease_epoch,
        )

        # 步骤成功即落盘账本：接管者据此继续累计（跨 epoch 预算）。
        _persist_budget(
            run_id=run_id,
            worker_id=worker_id,
            lease_epoch=lease_epoch,
            budget=budget,
        )

        if lease_lost.is_set():
            logger.warning("提交前租约已丢失，丢弃结果 run=%s", run_id)
            return

        # 结果 usage 统一由 runner 的账本覆盖（工作流可留空）。
        try:
            outcome.usage = budget.as_dict()
        except Exception:  # pragma: no cover - RunOutcome 为 dataclass，正常可写
            pass
        warnings = list(getattr(outcome, "warnings", []) or [])
        warnings.extend(w for w in budget.warnings if w not in warnings)
        try:
            outcome.warnings = warnings
        except Exception:  # pragma: no cover
            pass

        result = _submit_outcome(
            run_id=run_id,
            worker_id=worker_id,
            lease_epoch=lease_epoch,
            outcome=outcome,
            now=now,
        )
        logger.info("运行结果提交 run=%s status=%s commit=%s", run_id, outcome.status, result)
        if result in ("applied", "already_terminal", "lease_lost", "missing"):
            return
        if result == "session_deleted":
            _terminate_session_deleted(
                run_id=run_id, worker_id=worker_id, lease_epoch=lease_epoch
            )
            return
        # apply_outcome 异常：保留步骤成功但无法提交 → 兜底失败终结（不留下 running）
        _force_terminal(
            run_id=run_id,
            worker_id=worker_id,
            lease_epoch=lease_epoch,
            status=RunStatus.failed,
            error_code=ErrorCode.INTERNAL,
            error_message="结果提交失败，任务已终止",
            bump_session_version=True,
        )
        return


def handle_run_exception(
    run_id: str,
    *,
    worker_id: str,
    lease_epoch: int,
    exc: BaseException,
    now: Callable[[], float] = time.time,
) -> None:
    """execute_run 之外的兜底入口（worker 捕获未预期异常时使用）。"""
    _finalize_failure(
        run_id=run_id,
        worker_id=worker_id,
        lease_epoch=lease_epoch,
        code=ErrorCode.INTERNAL,
        message=f"{type(exc).__name__}: {exc}",
        now=now,
    )


__all__ = [
    "ACTION_TASK_KIND",
    "BudgetExceededError",
    "BudgetTracker",
    "BudgetedLLM",
    "LOGICAL_KEYS",
    "MAX_STEP_ATTEMPTS",
    "STEP_ROLE",
    "StepContext",
    "WORKFLOW_DISPATCH",
    "WORKFLOWS",
    "execute_run",
    "handle_run_exception",
]