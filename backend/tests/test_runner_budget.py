"""Runner 多次模型调用下的预算累计与 usage 落库测试（task-4）。

覆盖：
* 同一 Run 内连续多次 ``ctx.llm.complete()`` 的 token / 调用次数累计；
* Run 级 ``max_upstream_calls`` / ``max_run_input_tokens`` 超限时拒绝下一次调用，
  Run 终结为 ``failed(BUDGET_EXCEEDED)`` 且不写产品状态；
* 终结时把累计 usage（含 ``usage_source``）写入 ``runs.usage_json``，失败路径同样落库；
* 默认上限 ``MAX_UPSTREAM_CALLS_PER_RUN`` 下第 11 次调用被拒绝。

真实 sqlite、无网络；假 LLM 由 ``llm_factory`` 注入。
"""
from __future__ import annotations

import json
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select  # noqa: E402

from core.llm_client import LLMResult, LLMUsage  # noqa: E402
from domain import contracts as C  # noqa: E402
from domain.contracts import RunStatus, StepStatus, TaskKind  # noqa: E402
from domain.errors import ErrorCode  # noqa: E402
from repositories import database, run_repo  # noqa: E402
from repositories.database import ProductStateRow  # noqa: E402
from tests.test_runner import WORKER, RunnerTestCase, _success_outcome  # noqa: E402
from workflows import runner  # noqa: E402
from workflows.runner import BudgetExceededError, BudgetTracker  # noqa: E402

PROMPT_TOKENS = 11
COMPLETION_TOKENS = 7


def _result(*, usage_source: str = "provider") -> LLMResult:
    return LLMResult(
        text="ok",
        usage=LLMUsage(
            prompt_tokens=PROMPT_TOKENS,
            completion_tokens=COMPLETION_TOKENS,
            total_tokens=PROMPT_TOKENS + COMPLETION_TOKENS,
            usage_source=usage_source,  # type: ignore[arg-type]
        ),
        model="fake-model",
        latency_ms=1,
        attempts=1,
        finish_reason="stop",
    )


class _CountingLLM:
    """记录调用次数并返回固定 usage 的假 LLM。"""

    def __init__(self, *, usage_source: str = "provider") -> None:
        self.usage_source = usage_source
        self.calls: list[dict] = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        return _result(usage_source=self.usage_source)

    async def complete_json(self, **kwargs):
        self.calls.append(kwargs)
        return {"ok": True}, _result(usage_source=self.usage_source)


class BudgetAccumulationTests(RunnerTestCase):
    def _product_state_versions(self, session_id: str) -> list[int]:
        with database.session_scope() as db:
            rows = db.execute(
                select(ProductStateRow.version).where(ProductStateRow.session_id == session_id)
            ).scalars()
            return sorted(int(v) for v in rows)

    def _call_llm(self, ctx, *, index: int = 0):
        return ctx.llm.complete(
            messages=[{"role": "user", "content": f"hi-{index}"}],
            max_output_tokens=50,
            timeout_seconds=5,
            purpose=f"test.budget.{index}",
        )

    def test_three_calls_accumulate_usage(self) -> None:
        session_id, run_id = self.create_run()
        self.claim(run_id)
        llm = _CountingLLM()
        seen: dict[str, object] = {}

        async def dispatch(ctx):
            for i in range(3):
                await self._call_llm(ctx, index=i)
            seen["budget"] = ctx.budget
            seen["ctx"] = ctx
            return _success_outcome("澄清问题一")

        runner.WORKFLOW_DISPATCH[TaskKind.clarify] = dispatch
        self.run_once(run_id, llm=llm)

        ctx = seen["ctx"]
        for attr in ("db", "run", "session", "product_state", "current_message", "task_kind",
                     "llm", "budget", "worker_id", "lease_epoch", "deadline_epoch"):
            self.assertTrue(hasattr(ctx, attr), f"StepContext 缺少 {attr}")
        self.assertTrue(callable(getattr(ctx.llm, "complete_json", None)))
        self.assertEqual(ctx.worker_id, WORKER)  # type: ignore[attr-defined]
        self.assertGreater(ctx.deadline_epoch, 0)  # type: ignore[attr-defined]
        self.assertEqual(ctx.product_state.version, 1)  # type: ignore[attr-defined]
        self.assertIsNotNone(ctx.current_message)  # type: ignore[attr-defined]

        run = self.get_run(run_id)
        self.assertEqual(run.status, RunStatus.succeeded.value)
        self.assertEqual(len(llm.calls), 3)

        budget = seen["budget"]
        self.assertEqual(budget.input_tokens, PROMPT_TOKENS * 3)  # type: ignore[attr-defined]
        self.assertEqual(budget.output_tokens, COMPLETION_TOKENS * 3)  # type: ignore[attr-defined]
        self.assertEqual(budget.upstream_calls, 3)  # type: ignore[attr-defined]

        usage = json.loads(run.usage_json or "{}")
        self.assertEqual(usage["prompt_tokens"], 33)
        self.assertEqual(usage["completion_tokens"], 21)
        self.assertEqual(usage["total_tokens"], 54)
        self.assertEqual(usage["calls"], 3)
        self.assertEqual(usage["upstream_calls"], 3)
        self.assertEqual(usage["usage_source"], "provider")
        self.assertEqual(usage["limits"]["max_upstream_calls"], C.MAX_UPSTREAM_CALLS_PER_RUN)

    def test_estimated_usage_is_marked_in_run_usage(self) -> None:
        _, run_id = self.create_run()
        self.claim(run_id)
        llm = _CountingLLM(usage_source="estimated")

        async def dispatch(ctx):
            await self._call_llm(ctx)
            return _success_outcome("澄清问题一")

        runner.WORKFLOW_DISPATCH[TaskKind.clarify] = dispatch
        self.run_once(run_id, llm=llm)

        run = self.get_run(run_id)
        self.assertEqual(run.status, RunStatus.succeeded.value)
        usage = json.loads(run.usage_json or "{}")
        self.assertEqual(usage["usage_source"], "estimated")
        self.assertEqual(usage["estimated_calls"], 1)
        self.assertEqual(usage["provider_calls"], 0)

    def test_upstream_call_cap_rejects_next_call_and_skips_product_state(self) -> None:
        session_id, run_id = self.create_run(budget={"max_upstream_calls": 2})
        self.claim(run_id)
        llm = _CountingLLM()

        async def dispatch(ctx):
            for i in range(5):
                await self._call_llm(ctx, index=i)
            return _success_outcome("不应到达")

        runner.WORKFLOW_DISPATCH[TaskKind.clarify] = dispatch
        self.run_once(run_id, llm=llm)

        # 第三次 begin_call 被拒绝，模型只被真实调用两次。
        self.assertEqual(len(llm.calls), 2)
        run = self.get_run(run_id)
        self.assertEqual(run.status, RunStatus.failed.value)
        self.assertEqual(run.error_code, ErrorCode.BUDGET_EXCEEDED)

        step = self.get_step(run_id)
        self.assertIsNotNone(step)
        self.assertEqual(step.status, StepStatus.failed.value)
        self.assertEqual(step.error_code, ErrorCode.BUDGET_EXCEEDED)

        # 预算超限不得写产品状态：只有会话初始的 v1。
        self.assertEqual(self._product_state_versions(session_id), [1])

        # 失败终结也必须携带累计账本。
        usage = json.loads(run.usage_json or "{}")
        self.assertEqual(usage["upstream_calls"], 2)
        self.assertEqual(usage["prompt_tokens"], PROMPT_TOKENS * 2)
        self.assertEqual(usage["completion_tokens"], COMPLETION_TOKENS * 2)
        self.assertEqual(usage["usage_source"], "provider")

    def test_run_input_budget_rejects_first_call_without_upstream_request(self) -> None:
        session_id, run_id = self.create_run(budget={"max_run_input_tokens": 5})
        self.claim(run_id)
        llm = _CountingLLM()

        async def dispatch(ctx):
            # 90 字符 ≈ 30 tokens > 5，首次预留即被拒绝。
            await ctx.llm.complete(
                messages=[{"role": "user", "content": "x" * 90}],
                max_output_tokens=50,
                timeout_seconds=5,
                purpose="test.budget.too-large",
            )
            return _success_outcome("不应到达")

        runner.WORKFLOW_DISPATCH[TaskKind.clarify] = dispatch
        self.run_once(run_id, llm=llm)

        self.assertEqual(len(llm.calls), 0, "预算不足时不得发起模型请求")
        run = self.get_run(run_id)
        self.assertEqual(run.status, RunStatus.failed.value)
        self.assertEqual(run.error_code, ErrorCode.BUDGET_EXCEEDED)
        self.assertEqual(self._product_state_versions(session_id), [1])


class BudgetContinuityTests(RunnerTestCase):
    """租约接管后预算账本不清零（单 Run 上游调用上限跨 epoch 成立）。"""

    def test_takeover_restores_usage_and_rejects_eleventh_call(self) -> None:
        """接管后从 usage_json 恢复计数：第 11 次上游调用被拒且不发请求。"""

        _, run_id = self.create_run()
        self.claim(run_id)

        # 模拟上一 epoch 已结算 9 次上游调用（usage_json 是唯一跨进程账本）。
        with database.session_scope() as db:
            run = run_repo.get_run(db, run_id)
            self.assertIsNotNone(run)
            run.usage_json = json.dumps(
                {
                    "prompt_tokens": PROMPT_TOKENS * 9,
                    "completion_tokens": COMPLETION_TOKENS * 9,
                    "total_tokens": (PROMPT_TOKENS + COMPLETION_TOKENS) * 9,
                    "calls": 9,
                    "upstream_calls": 9,
                    "provider_calls": 9,
                    "estimated_calls": 0,
                    "usage_source": "provider",
                }
            )
            # 租约过期：允许 claim_next_run 以新 epoch 接管。
            run.lease_expires_at = 0

        with database.session_scope() as db:
            claimed = run_repo.claim_next_run(
                db, worker_id=WORKER, now_epoch=int(time.time()), lease_seconds=30
            )
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.id, run_id)

        llm = _CountingLLM()
        seen: dict[str, int] = {}

        async def dispatch(ctx):
            seen["restored"] = int(ctx.budget.upstream_calls)
            # 第 10 次仍在上限内：真正发上游请求。
            await ctx.llm.complete(
                messages=[{"role": "user", "content": "第 10 次调用"}],
                max_output_tokens=50,
                timeout_seconds=5,
                purpose="test.budget.takeover.tenth",
            )
            seen["after_tenth"] = int(ctx.budget.upstream_calls)
            # 第 11 次必须被账本拒绝（异常向上抛，由 runner 终结 Run）。
            await ctx.llm.complete(
                messages=[{"role": "user", "content": "第 11 次调用"}],
                max_output_tokens=50,
                timeout_seconds=5,
                purpose="test.budget.takeover.eleventh",
            )
            return _success_outcome("不应到达")

        runner.WORKFLOW_DISPATCH[TaskKind.clarify] = dispatch
        self.run_once(run_id, llm=llm)

        self.assertEqual(seen["restored"], 9, "接管后账本必须延续")
        self.assertEqual(seen["after_tenth"], 10, "第 10 次调用应被允许")
        self.assertEqual(len(llm.calls), 1, "第 11 次上游调用不得发出")
        run = self.get_run(run_id)
        self.assertEqual(run.status, RunStatus.failed.value)
        self.assertEqual(run.error_code, ErrorCode.BUDGET_EXCEEDED)
        usage = json.loads(run.usage_json or "{}")
        self.assertEqual(usage["upstream_calls"], 10, "跨 epoch 账本不得清零")


class DefaultCapTests(unittest.TestCase):
    """不依赖数据库：直接验证默认上限与第 11 次拒绝。"""

    def test_default_cap_is_ten_and_eleventh_call_rejected(self) -> None:
        tracker = BudgetTracker()
        self.assertEqual(C.MAX_UPSTREAM_CALLS_PER_RUN, 10)
        self.assertEqual(tracker.max_upstream_calls, C.MAX_UPSTREAM_CALLS_PER_RUN)

        for _ in range(10):
            tracker.begin_call(input_tokens=0, output_tokens=0)
        self.assertEqual(tracker.upstream_calls, 10)

        with self.assertRaises(BudgetExceededError) as caught:
            tracker.begin_call(input_tokens=0, output_tokens=0)
        self.assertEqual(caught.exception.code, ErrorCode.BUDGET_EXCEEDED)
        self.assertTrue(tracker.exceeded)
        self.assertEqual(tracker.upstream_calls, 10, "被拒绝的调用不得计数")


if __name__ == "__main__":
    unittest.main()
