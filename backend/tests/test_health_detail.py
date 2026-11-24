"""`/health/detail` 的诊断字段（第 18 批模型层 + 第 20 批 Worker 卡死识别）。

为什么单独抽一测：
1) 联调时最常见的问题是「用的是哪个模型」「为什么结构化输出是空的」，
   答案必须在运维接口上可直接读到，而不是翻 .env；
2) 该响应是登录后可读的，**不能**把 API Key/完整 base_url 带出去，
   所以这里断言的不仅是字段存在，还有「不泄露」。

依赖 `V2ApiTestBase` 的临时库与签名 Cookie（不触发 lifespan 的 RAG 构建）。
"""
from __future__ import annotations

import json
import pathlib
import sys
import unittest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:  # pragma: no cover
    sys.path.insert(0, str(BACKEND))

import main  # noqa: E402
from core.config import settings  # noqa: E402
from tests.test_api_v2 import V2ApiTestBase  # noqa: E402


class HealthDetailTests(V2ApiTestBase):
    def test_detail_exposes_llm_diagnostics_without_secrets(self) -> None:
        resp = self.get("/health/detail")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()

        llm = body.get("llm")
        self.assertIsInstance(llm, dict, "必须暴露 llm 诊断块")
        self.assertEqual(llm.get("model"), settings.llm_model)
        self.assertIsInstance(llm.get("disable_reasoning"), bool)
        self.assertGreater(float(llm.get("timeout_seconds") or 0), 0)
        self.assertGreaterEqual(int(llm.get("max_retries") or 0), 0)
        # base_url 只留主机名：不能出现路径、查询串或凭据
        self.assertNotIn("/", str(llm.get("base_url_host") or ""))
        self.assertNotIn("api_key", json.dumps(llm, ensure_ascii=False).lower())
        self.assertNotIn("sk-", json.dumps(llm, ensure_ascii=False).lower())

        # 预算档位诊断仍保留（FR-08）
        self.assertEqual(body.get("budget_profile"), settings.llm_budget_profile or "standard")
        self.assertIn("max_upstream_calls", body.get("budget_limits") or {})

    def test_detail_requires_login(self) -> None:
        resp = self.client.get("/health/detail")
        self.assertEqual(resp.status_code, 401)

    def test_worker_block_reports_stall_consistently(self) -> None:
        resp = self.get("/health/detail")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()

        worker = body.get("worker")
        self.assertIsInstance(worker, dict)
        for key in ("running_runs", "queued_runs", "last_heartbeat_at", "stalled", "hint"):
            self.assertIn(key, worker, f"worker 诊断缺字段 {key}")
        self.assertIsInstance(worker["stalled"], bool)
        # stalled 与 hint 必须一致：有提示即 stalled，无提示即不 stalled
        self.assertEqual(worker["stalled"], worker["hint"] is not None)
        # 顶层 status 也随 stalled 降级（监控可以直接看 status）
        self.assertEqual(body["status"], "degraded" if worker["stalled"] else "ok")

    def test_worker_stall_hint_requires_queue_and_heartbeat(self) -> None:
        hint = main._worker_stall_hint
        # 没有排队任务：不报警
        self.assertIsNone(hint(queued_runs=0, running_runs=0, heartbeat_age_seconds=None))
        # 排队 + 没有 Worker 领取：必须给可执行提示
        stalled = hint(queued_runs=3, running_runs=0, heartbeat_age_seconds=None)
        self.assertIsNotNone(stalled)
        self.assertIn("3 个任务在排队", stalled or "")
        self.assertIn("start.bat", stalled or "")
        self.assertIn("workers.run_worker", stalled or "")
        # 排队但 Worker 心跳新鲜：正常
        self.assertIsNone(hint(queued_runs=3, running_runs=1, heartbeat_age_seconds=12.0))
        # 排队但心跳过旧：判 stalled
        stale = hint(queued_runs=1, running_runs=1, heartbeat_age_seconds=600.0)
        self.assertIn("最近心跳在 600 秒前", stale or "")
        # 有 running 却读不到心跳：也要报
        self.assertIn("读不到心跳时间", hint(queued_runs=1, running_runs=1, heartbeat_age_seconds=None) or "")

    def test_parse_iso_seconds_tolerates_bad_input(self) -> None:
        self.assertIsNone(main._parse_iso_seconds(None))
        self.assertIsNone(main._parse_iso_seconds(""))
        self.assertIsNone(main._parse_iso_seconds("2026-11-24 10:00:00"))
        value = main._parse_iso_seconds("2026-11-24T10:00:00Z")
        self.assertIsNotNone(value)
        self.assertGreater(value or 0, 0)

    def test_url_host_only_returns_hostname(self) -> None:
        self.assertEqual(main._url_host("https://api.deepseek.com/v1"), "api.deepseek.com")
        self.assertEqual(main._url_host("http://127.0.0.1:8080/v1?x=1"), "127.0.0.1")
        self.assertEqual(main._url_host(""), "")
        self.assertEqual(main._url_host("not a url"), "")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
