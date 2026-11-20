"""会话任务列表端点（`GET /api/v2/sessions/{sid}/runs`）契约测试。

用途：前端刷新后需要从后端恢复「上一次任务失败」的原因（store 里的
``lastFailedRunId`` 只活在内存里）。这里锁定三件事：
1. 只返回本人会话的任务，且按 `created_at DESC` 排序；
2. `status` 过滤与 `limit` 夹取生效（前端默认只取失败项、最多 5 条）；
3. 失败任务的 `error_code` / `error_message` 原样返回，供通知条渲染。

测试沿用 ``tests/test_api_v2.py`` 的临时库 + 签名 Cookie 基建，避免重复实现。
"""
from __future__ import annotations

import unittest

from sqlalchemy import select

from domain import contracts as C
from repositories import database, run_repo
from tests.test_api_v2 import V2ApiTestBase


class SessionRunsEndpointTests(V2ApiTestBase):
    def _create_session(self) -> str:
        resp = self.post(
            "/api/v2/sessions",
            json={"problem_statement": "会话任务列表契约" * 3},
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        return resp.json()["id"]

    def _mark_failed(self, run_id: str, *, code: str, message: str) -> None:
        with database.session_scope() as db:
            row = run_repo.get_run(db, run_id)
            self.assertIsNotNone(row)
            row.status = C.RunStatus.failed.value
            row.error_code = code
            row.error_message = message
            row.started_at = C.utc_now_iso()
            row.finished_at = C.utc_now_iso()

    def _runs(self, session_id: str, **params) -> dict:
        resp = self.get(f"/api/v2/sessions/{session_id}/runs", params=params)
        return {"status": resp.status_code, "body": resp.json() if resp.content else {}}

    def test_returns_failed_run_with_error_message(self) -> None:
        session_id = self._create_session()
        run_id = self._runs(session_id)["body"]["items"][0]["id"]
        self._mark_failed(
            run_id,
            code="LLM_AUTH_ERROR",
            message="模型服务鉴权失败（API Key 无效、已过期或无权限）",
        )

        payload = self._runs(session_id, status="failed", limit=5)["body"]
        self.assertEqual(len(payload["items"]), 1)
        run = payload["items"][0]
        self.assertEqual(run["id"], run_id)
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error_code"], "LLM_AUTH_ERROR")
        self.assertIn("鉴权失败", run["error_message"])
        self.assertEqual(run["session_id"], session_id)
        self.assertIsNone(payload["next_cursor"])

    def test_status_filter_and_limit_clamp(self) -> None:
        session_id = self._create_session()
        with database.session_scope() as db:
            for index in range(3):
                run_repo.create_run(
                    db,
                    session_id=session_id,
                    owner_email=self.email,
                    request_id=f"req-{index}",
                    action=C.Action.clarify.value,
                    input_hash="0" * 64,
                    base_session_version=1,
                    base_product_version=1,
                    now_iso=C.utc_now_iso(),
                )
        # 只有最近两条被取回，且顺序是 created_at DESC（后插入的在前面）
        payload = self._runs(session_id, limit=2)["body"]
        self.assertEqual(len(payload["items"]), 2)
        created = [item["created_at"] for item in payload["items"]]
        self.assertEqual(created, sorted(created, reverse=True))

        # 未匹配的状态过滤返回空列表，而不是 404
        empty = self._runs(session_id, status="succeeded")["body"]
        self.assertEqual(empty["items"], [])

        # limit 超上限被夹到 50、非法值 400
        clamped = self._runs(session_id, limit=999)["body"]
        self.assertEqual(len(clamped["items"]), 4)
        invalid = self._runs(session_id, limit=0)
        self.assertEqual(invalid["status"], 422)

    def test_is_owner_scoped_and_rejects_missing_session(self) -> None:
        session_id = self._create_session()
        other = self.get(
            f"/api/v2/sessions/{session_id}/runs",
            email="someone-else@example.com",
        )
        self.assertEqual(other.status_code, 403)
        self.assertEqual(other.json()["error"]["code"], "FORBIDDEN")

        missing = self.get("/api/v2/sessions/" + "f" * 32 + "/runs")
        self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
