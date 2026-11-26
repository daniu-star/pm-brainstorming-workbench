"""`/health/detail` 的运维区块（第 38 批 /ops 页）与沙箱状态视图测试。

覆盖：

1. ``storage``：库大小来自 ``PRAGMA page_count * page_size``，行数随写入增长；
2. ``queues``：排队中 / 运行中计数与最久排队时长；
3. ``sandbox``：开关字段与 fail-closed 文案（与 ``/api/v2/sandbox`` 同源）；
4. 未登录访问 ``/health/detail`` 仍然 401（不因新增区块放宽鉴权）。
"""
from __future__ import annotations

import unittest

from core.config import settings
from repositories import database, run_repo, session_repo
from services import command_service
from tests.test_api_v2 import V2ApiTestBase


class OpsHealthTests(V2ApiTestBase):
    def _detail(self) -> dict:
        resp = self.get("/health/detail")
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def test_detail_exposes_storage_counts_and_db_bytes(self) -> None:
        body = self._detail()
        storage = body["storage"]
        self.assertTrue(storage["db"])
        self.assertGreater(storage["db_bytes"], 0)
        for key in ("sessions", "messages", "runs", "steps", "events", "evidence", "loops", "loop_artifacts"):
            self.assertIn(key, storage["counts"])

        self.create_session()
        after = self._detail()["storage"]["counts"]
        self.assertGreaterEqual(after["sessions"], storage["counts"]["sessions"] + 1)
        self.assertGreaterEqual(after["runs"], storage["counts"]["runs"] + 1)

    def test_detail_exposes_queue_depth(self) -> None:
        body = self._detail()
        queues = body["queues"]
        self.assertIn("queued", queues)
        self.assertIn("running", queues)
        self.assertIn("oldest_queued_age_seconds", queues)
        self.assertGreaterEqual(queues["queued"], 0)
        # 建会话会排入首个 clarify Run：排队数至少 +1
        base = queues["queued"]
        self.create_session()
        self.assertGreaterEqual(self._detail()["queues"]["queued"], base + 1)

    def test_detail_exposes_sandbox_status(self) -> None:
        settings.loop_sandbox_enabled = False
        settings.loop_sandbox_allowed_commands = ""
        body = self._detail()
        sandbox = body["sandbox"]
        self.assertFalse(sandbox["enabled"])
        self.assertEqual(sandbox["allowed_commands"], [])
        self.assertIn("沙箱未开启", sandbox["note"])

        settings.loop_sandbox_enabled = True
        settings.loop_sandbox_allowed_commands = "python,node"
        body = self._detail()
        self.assertTrue(body["sandbox"]["enabled"])
        self.assertEqual(body["sandbox"]["allowed_commands"], ["python", "node"])

    def test_detail_requires_login(self) -> None:
        resp = self.client.get("/health/detail")
        self.assertEqual(resp.status_code, 401, resp.text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
