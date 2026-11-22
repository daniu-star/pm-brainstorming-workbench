"""测试支撑脚本：在独立进程里跑「假 LLM + 真 RunWorker」（AC-36 崩溃/重启注入用）。

用法::

    python tests/support/control_worker.py <db_url> <worker_id> <max_runs> <mode>

* ``mode=slow``  —— ``audit.answer`` 的模型调用先 sleep 5 秒，便于父进程在生成途中
  强杀本进程（验证「意图已持久、旧生成不提交」）。
* ``mode=fast``  —— 立即返回，用于重启后的接管执行。

脚本只 import 后端包，不联网：所有 purpose 都返回结构合法的假输出。
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_ROOT))

from core.llm_client import LLMResult, LLMUsage  # noqa: E402
from repositories import database  # noqa: E402
from workers.run_worker import RunWorker  # noqa: E402

SLOW_SECONDS = 8.0


class _FakeLLM:
    """按 purpose 返回合法 payload；``slow`` 模式在 answer 调用上故意拖时间。"""

    def __init__(self, *, slow: bool) -> None:
        self.slow = slow
        self.calls: list[str] = []

    @staticmethod
    def _result() -> LLMResult:
        return LLMResult(
            text="ok",
            usage=LLMUsage(
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
                usage_source="provider",  # type: ignore[arg-type]
            ),
            model="fake-model",
            latency_ms=1,
            attempts=1,
            finish_reason="stop",
        )

    async def complete(self, **kwargs):  # 兼容 workflow 里可能走的文本调用
        return await self.complete_json(**kwargs)

    async def complete_json(
        self,
        *,
        messages,
        max_output_tokens=1200,
        timeout_seconds=90,
        purpose="",
        json_schema=None,
        temperature=0.2,
    ):
        self.calls.append(purpose)
        if purpose == "audit.answer" and self.slow:
            await asyncio.sleep(SLOW_SECONDS)
        if purpose == "audit.open":
            payload = {
                "dimension": "problem_validity",
                "question_text": "这个产品要解决的核心问题是什么？",
                "issue_refs": [],
                "evidence_refs": [],
                "rationale": "先确认问题是否成立",
            }
        elif purpose == "audit.answer":
            payload = {
                "assessment": "gap",
                "rationale": "回答缺少可核验的数据来源。",
                "evidence_refs": [],
                "next_question": {
                    "dimension": "business_loop",
                    "question_text": "付费方与使用者不是同一批人时，谁决定采购？",
                    "issue_refs": [],
                    "evidence_refs": [],
                    "rationale": "商业闭环尚未讨论",
                },
                "message": "已记录你的回答，但证据不足。",
            }
        elif purpose == "audit.report":
            payload = {
                "supported_points": [],
                "unresolved_gaps": ["缺少可核验的问题证据"],
                "recommended_experiments": ["用 5 个真实用户验证问题强度"],
                "limitations": "本轮审计样本有限",
                "summary": "审计已完成，存在未解决缺口。",
            }
        else:  # pragma: no cover - 崩溃注入用例只走 audit 三个 purpose
            payload = {}
        return payload, self._result()

    def __call__(self) -> "_FakeLLM":  # llm_factory 兼容
        return _FakeLLM(slow=self.slow)


def main(argv: list[str]) -> int:
    db_url, worker_id, max_runs_raw, mode = argv[1:5]
    database.configure(db_url, force=True)

    from workflows import runner as runner_module

    fake = _FakeLLM(slow=(mode == "slow"))
    runner_module.LLMClient = fake  # type: ignore[assignment]

    worker = RunWorker(
        worker_id=worker_id,
        interval=0.05,
        max_runs=int(max_runs_raw),
    )
    worker.run()
    print(f"WORKER_DONE calls={len(fake.calls)} purposes={','.join(fake.calls)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
