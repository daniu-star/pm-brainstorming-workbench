"""会话共享（只读/可提交命令两种角色）的 API 级验收。

覆盖产品化第 35 批的最小闭环：
1. 所有者可以把会话共享给另一个账号（viewer / editor），重复共享只改角色；
2. viewer 能读会话、消息、产品状态，但不能提交命令；
3. editor 能提交命令，Run 仍记在会话所有者名下（模型 Key 与幂等键不变）；
4. 无关账号 403；非所有者不能增删共享；不能共享给自己；
5. 撤销共享后立刻失去访问；`GET /sessions` 对被共享方可见该会话。
"""
from __future__ import annotations

import unittest
import uuid

from sqlalchemy import update

from domain import contracts as C
from repositories import database, run_repo
from repositories.database import RunRow, SessionRow
from tests.test_api_v2 import V2ApiTestBase


def _share_url(session_id: str) -> str:
    return f"/api/v2/sessions/{session_id}/shares"


def _commands_url(session_id: str) -> str:
    return f"/api/v2/sessions/{session_id}/commands"


class SessionSharingTests(V2ApiTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.viewer = f"viewer-{uuid.uuid4().hex[:8]}@example.com"
        self.editor = f"editor-{uuid.uuid4().hex[:8]}@example.com"
        self.stranger = f"stranger-{uuid.uuid4().hex[:8]}@example.com"
        created = self.post(
            "/api/v2/sessions",
            json={"problem_statement": "共享协作验收：给中小企业做对账助手"},
            headers={"Idempotency-Key": uuid.uuid4().hex},
        )
        self.assertEqual(created.status_code, 201, created.text)
        self.session_id = created.json()["id"]

    # -- helpers ---------------------------------------------------------
    def _share(self, email: str, role: str = "viewer", *, as_email: str | None = None):
        return self.post(
            _share_url(self.session_id),
            json={"email": email, "role": role},
            email=as_email or self.email,
        )

    def _command(self, *, as_email: str, action: str = "skip_clarification"):
        return self.post(
            _commands_url(self.session_id),
            json={"action": action, "payload": {}},
            email=as_email,
            headers={"Idempotency-Key": uuid.uuid4().hex},
        )

    def _release_active_run(self) -> None:
        """把建会话时排入的 clarify Run 收口，避免后续命令撞 SESSION_BUSY。"""

        with database.session_scope() as db:
            session = db.get(SessionRow, self.session_id)
            if session is not None:
                session.active_run_id = None
            db.execute(
                update(RunRow)
                .where(RunRow.session_id == self.session_id)
                .values(status=C.RunStatus.succeeded.value)
            )

    # -- 共享管理 ---------------------------------------------------------
    def test_owner_can_share_and_repeat_updates_role(self):
        first = self._share(self.viewer, "viewer")
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(first.json()["email"], self.viewer)
        self.assertEqual(first.json()["role"], "viewer")

        again = self._share(self.viewer, "editor")
        self.assertEqual(again.status_code, 201, again.text)
        self.assertEqual(again.json()["role"], "editor")

        listed = self.get(_share_url(self.session_id))
        self.assertEqual(listed.status_code, 200, listed.text)
        items = listed.json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0], {**items[0], "email": self.viewer, "role": "editor"})

    def test_non_owner_cannot_manage_shares(self):
        self.assertEqual(self._share(self.viewer).status_code, 201)
        denied = self._share(self.stranger, as_email=self.viewer)
        self.assertEqual(denied.status_code, 403, denied.text)
        removed = self.client.delete(
            f"{_share_url(self.session_id)}/{self.viewer}",
            headers=self.headers(self.viewer),
        )
        self.assertEqual(removed.status_code, 403, removed.text)

    def test_cannot_share_with_self(self):
        self.assertEqual(self._share(self.email).status_code, 201) if False else None
        resp = self._share(self.email)
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertEqual(resp.json()["error"]["code"], "INVALID_REQUEST")

    def test_unknown_share_cannot_be_removed(self):
        resp = self.client.delete(
            f"{_share_url(self.session_id)}/{self.stranger}",
            headers=self.headers(self.email),
        )
        self.assertEqual(resp.status_code, 404, resp.text)

    # -- 读权限 -----------------------------------------------------------
    def test_viewer_reads_but_cannot_command(self):
        self.assertEqual(self._share(self.viewer, "viewer").status_code, 201)

        detail = self.get(f"/api/v2/sessions/{self.session_id}", email=self.viewer)
        self.assertEqual(detail.status_code, 200, detail.text)

        messages = self.get(f"/api/v2/sessions/{self.session_id}/messages", email=self.viewer)
        self.assertEqual(messages.status_code, 200, messages.text)

        state = self.get(f"/api/v2/sessions/{self.session_id}/product-state", email=self.viewer)
        self.assertEqual(state.status_code, 200, state.text)

        self._release_active_run()
        denied = self._command(as_email=self.viewer)
        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual(denied.json()["error"]["code"], "FORBIDDEN")

    def test_editor_can_command_as_session_owner(self):
        self.assertEqual(self._share(self.editor, "editor").status_code, 201)
        self._release_active_run()
        receipt = self._command(as_email=self.editor)
        self.assertEqual(receipt.status_code, 202, receipt.text)
        self.assertEqual(receipt.json()["status"], "succeeded")

        # Run 记在所有者名下（协作者借用所有者的账户与额度）
        with database.session_scope() as db:
            runs = run_repo.list_session_runs(db, self.session_id, limit=10)
            self.assertTrue(runs)
            self.assertTrue(all(run.owner_email == self.email for run in runs))

    def test_stranger_is_forbidden(self):
        denied = self.get(f"/api/v2/sessions/{self.session_id}", email=self.stranger)
        self.assertEqual(denied.status_code, 403, denied.text)

    # -- 撤销与列表 -------------------------------------------------------
    def test_removing_share_revokes_access(self):
        self.assertEqual(self._share(self.viewer).status_code, 201)
        self.assertEqual(
            self.get(f"/api/v2/sessions/{self.session_id}", email=self.viewer).status_code, 200
        )
        removed = self.client.delete(
            f"{_share_url(self.session_id)}/{self.viewer}",
            headers=self.headers(self.email),
        )
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertEqual(
            self.get(f"/api/v2/sessions/{self.session_id}", email=self.viewer).status_code, 403
        )

    def test_shared_session_appears_in_list(self):
        self.assertEqual(self._share(self.viewer).status_code, 201)
        mine = self.get("/api/v2/sessions", email=self.viewer)
        self.assertEqual(mine.status_code, 200, mine.text)
        ids = [item["id"] for item in mine.json()["items"]]
        self.assertIn(self.session_id, ids)

        owner_ids = [item["id"] for item in self.get("/api/v2/sessions").json()["items"]]
        self.assertIn(self.session_id, owner_ids)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
