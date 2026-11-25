"""Loop Studio 假 LLM Worker（E2E 用）：真 RunWorker + 假 LLM，不联网。

父进程通过环境变量传入 ``DATABASE_URL`` / ``MAX_RUNS`` / ``WORKER``。
用法见 ``scripts/loop_e2e/run_loop_e2e.py``。
"""
from __future__ import annotations

import json
import os
import sys

from core.llm_client import LLMResult, LLMUsage
from repositories import database
from core import config as core_config

# 内部联调脚本共用 backend/.env 里的 Key，不走 BYOK 闸门。
core_config.settings.llm_key_mode = "system"


class FakeLLM:
    """按 purpose 返回五阶段合法 JSON。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def complete(self, **kwargs):
        return await self.complete_json(**kwargs)

    async def complete_json(self, *, messages, max_output_tokens=1200, timeout_seconds=90,
                            purpose="", json_schema=None, temperature=0.2):
        self.calls.append(purpose)
        if purpose == "loop.ideate":
            payload = {
                "title": "预算协同 MVP",
                "summary": "面向 3-10 人财务团队的多表预算协同原型。",
                "features": [
                    {
                        "id": "f1",
                        "title": "多人在线编制预算",
                        "description": "两人同一表格内编制，变更秒级同步。",
                        "acceptance": "两个账号同时编辑同一预算行，5 秒内双方可见。",
                        "priority": "must",
                        "effort": "M",
                    },
                    {
                        "id": "f2",
                        "title": "预算偏差提醒",
                        "description": "实际支出超预算 10% 时提醒负责人。",
                        "acceptance": "录入超支数据后 1 分钟内收到站内提醒。",
                        "priority": "should",
                        "effort": "S",
                    },
                ],
                "assumptions": [
                    {
                        "id": "a1",
                        "statement": "财务团队愿意放弃 Excel 迁移到在线协同",
                        "risk": "迁移成本导致放弃",
                        "verification": "访谈 5 个团队并观察一周真实使用",
                    }
                ],
                "not_in_scope": ["多币种", "审批流"],
            }
        elif purpose == "loop.validate":
            payload = {
                "checks": [
                    {
                        "id": "v1",
                        "feature_id": "f1",
                        "question": "并发编辑冲突如何解决？",
                        "result": "pass",
                        "evidence_refs": [],
                        "notes": "采用行级锁 + 服务端广播。",
                    },
                    {
                        "id": "v2",
                        "feature_id": "f2",
                        "question": "提醒渠道是否可达？",
                        "result": "unknown",
                        "evidence_refs": [],
                        "notes": "暂只有站内信，邮件通道未验证。",
                    },
                ],
                "summary": "核心协同能力可行，提醒通道存在风险。",
                "unknowns": ["邮件通道可用性"],
            }
        elif purpose == "loop.develop":
            payload = {
                "title": "预算协同原型",
                "summary": "单文件 Web 原型：预算表 + 偏差提醒。",
                "entry_file": "index.html",
                "run_command": "python -m http.server 8080",
                "files": [
                    {
                        "path": "index.html",
                        "language": "html",
                        "content": "<!doctype html><meta charset=utf-8><h1>预算协同</h1>",
                        "purpose": "原型入口",
                    }
                ],
                "limitations": "未接真实后端，协同为本地模拟。",
                "requires_human_build": False,
            }
        elif purpose == "loop.test":
            payload = {
                "total": 2,
                "passed": 2,
                "failed": 0,
                "skipped": 0,
                "simulated": True,
                "cases": [
                    {
                        "id": "t1",
                        "feature_id": "f1",
                        "title": "两人同时编辑同一行",
                        "steps": ["A 打开预算", "B 修改第 3 行", "A 观察 5 秒内更新"],
                        "expected": "A 看到 B 的修改",
                        "status": "passed",
                        "evidence": "本地模拟日志 sim-001",
                    },
                    {
                        "id": "t2",
                        "feature_id": "f2",
                        "title": "超支 10% 触发提醒",
                        "steps": ["录入超支数据", "等待站内提醒"],
                        "expected": "1 分钟内出现提醒",
                        "status": "passed",
                        "evidence": "本地模拟日志 sim-002",
                    },
                ],
                "notes": "本报告为模拟执行（未实际执行浏览器端用例），仅验证结构与流程。",
            }
        elif purpose == "loop.evaluate":
            payload = {
                "total": 88,
                "dimensions": [
                    {"key": "functionality", "score": 22, "max": 25, "reason": "核心协同可用"},
                    {"key": "feasibility", "score": 13, "max": 15, "reason": "技术路径清晰"},
                    {"key": "user_value", "score": 18, "max": 20, "reason": "痛点明确"},
                    {"key": "risk", "score": 13, "max": 15, "reason": "提醒通道待验证"},
                    {"key": "delivery", "score": 9, "max": 10, "reason": "原型与规格齐全"},
                    {"key": "testability", "score": 8, "max": 10, "reason": "用例为模拟执行"},
                    {"key": "docs", "score": 5, "max": 5, "reason": "文档完整"},
                ],
                "verdict": "pass",
                "blockers": [],
                "highlights": ["多人协同核心路径跑通"],
                "next_actions": ["补邮件提醒通道验证"],
                "generated_by": "model",
                "hard_capped": False,
            }
        else:
            payload = {
                "display_text": "（未使用）",
                "claims": [],
                "options": [],
                "critical_issues": [],
                "experiments": [],
                "uncertainties": [],
            }
        text = json.dumps(payload, ensure_ascii=False)
        usage = LLMUsage(prompt_tokens=240, completion_tokens=160, total_tokens=400, usage_source="provider")
        return payload, LLMResult(text=text, usage=usage, model="fake-loop", latency_ms=5, attempts=1)


def main() -> int:
    database.configure(os.environ["DATABASE_URL"], force=True)
    from workflows import runner

    runner.LLMClient = FakeLLM  # type: ignore[assignment]
    from workers.run_worker import RunWorker

    max_runs = int(os.environ.get("MAX_RUNS", "1"))
    worker = RunWorker(worker_id=os.environ.get("WORKER", "loop-e2e-worker"), interval=0.05, max_runs=max_runs)
    worker.run(once=True)
    print(json.dumps({"processed": worker.processed, "db": True}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
