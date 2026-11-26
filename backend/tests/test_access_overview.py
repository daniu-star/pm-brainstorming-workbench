"""统一权限页（``GET /api/v2/access``）与共享可见性验收（第 39 批）。

覆盖：
1. 所有者总览：我创建的会话 + 共享给了谁 + 汇总数字；
2. 成员总览：共享给我的会话、我的角色、所有者邮箱；
3. 会话视图/旧接口都带 ``my_role``，且共享成员能通过旧接口打开会话（此前 403）；
4. 成员主动退出共享（``DELETE /api/v2/sessions/{id}/share``）后权限立即收回；
5. 所有者不能「退出」自己的共享；陌生人既看不到也退不了；
6. 成员组出现在总览里；
7. 未登录访问总览 401。
"""
from __future__ import annotations

import unittest
import uuid

from tests.test_api_v2 import V2ApiTestBase


class AccessOverviewTests(V2ApiTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.viewer = f"viewer-{uuid.uuid4().hex[:8]}@example.com"
        self.editor = f"editor-{uuid.uuid4().hex[:8]}@example.com"
        self.stranger = f"stranger-{uuid.uuid4().hex[:8]}@example.com"
        created = self.post(
            "/api/v2/sessions",
            json={"problem_statement": "权限页验收：把会话共享给同事后，双方各自看到该看到的"},
            headers={"Idempotency-Key": uuid.uuid4().hex},
        )
        self.assertEqual(created.status_code, 201, created.text)
        self.session_id = created.json()["id"]

    # -- helpers ---------------------------------------------------------
    def _share(self, email: str, role: str = "viewer"):
        resp = self.post(
            f"/api/v2/sessions/{self.session_id}/shares",
            json={"email": email, "role": role},
        )
        self.assertEqual(resp.status_code, 201, resp.text)
        return resp.json()

    def _overview(self, email: str | None = None) -> dict:
        resp = self.get("/api/v2/access", email=email)
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    # -- cases -----------------------------------------------------------
    def test_owner_overview_lists_owned_session_and_shares(self) -> None:
        self._share(self.viewer, "viewer")
        self._share(self.editor, "editor")
        data = self._overview()

        self.assertEqual(len(data["owned"]), 1)
        owned = data["owned"][0]
        self.assertEqual(owned["session_id"], self.session_id)
        self.assertIn("权限页验收", owned["title"])
        self.assertEqual(owned["phase"], "clarifying")
        self.assertEqual(
            sorted(share["role"] for share in owned["shares"]), ["editor", "viewer"]
        )
        self.assertEqual(
            sorted(share["email"] for share in owned["shares"]),
            sorted([self.viewer, self.editor]),
        )
        self.assertEqual(data["incoming"], [])
        # shared_out 统计「会话数」而不是成员数：一个会话共享给两人仍是 1。
        self.assertEqual(data["totals"]["owned"], 1)
        self.assertEqual(data["totals"]["shared_out"], 1)
        self.assertEqual(data["totals"]["incoming"], 0)

    def test_member_overview_lists_incoming_with_role(self) -> None:
        self._share(self.viewer, "viewer")
        data = self._overview(self.viewer)

        self.assertEqual(data["owned"], [])
        self.assertEqual(len(data["incoming"]), 1)
        incoming = data["incoming"][0]
        self.assertEqual(incoming["session_id"], self.session_id)
        self.assertEqual(incoming["owner_email"], self.email)
        self.assertEqual(incoming["role"], "viewer")
        self.assertIn("权限页验收", incoming["title"])
        self.assertEqual(data["totals"]["incoming"], 1)
        self.assertEqual(data["totals"]["owned"], 0)

    def test_session_view_carries_my_role_for_each_role(self) -> None:
        self._share(self.viewer, "viewer")
        self._share(self.editor, "editor")

        owner_view = self.get(f"/api/v2/sessions/{self.session_id}")
        self.assertEqual(owner_view.status_code, 200, owner_view.text)
        self.assertEqual(owner_view.json()["my_role"], "owner")

        viewer_view = self.get(f"/api/v2/sessions/{self.session_id}", email=self.viewer)
        self.assertEqual(viewer_view.status_code, 200, viewer_view.text)
        self.assertEqual(viewer_view.json()["my_role"], "viewer")

        editor_view = self.get(f"/api/v2/sessions/{self.session_id}", email=self.editor)
        self.assertEqual(editor_view.json()["my_role"], "editor")

        # 列表同样带角色：成员列表里也只有这一条共享会话。
        viewer_list = self.get("/api/v2/sessions", email=self.viewer)
        self.assertEqual(viewer_list.status_code, 200, viewer_list.text)
        items = viewer_list.json()["items"]
        self.assertEqual([item["session_id"] if "session_id" in item else item["id"] for item in items], [self.session_id])
        self.assertEqual(items[0]["my_role"], "viewer")

    def test_legacy_endpoints_allow_shared_member(self) -> None:
        self._share(self.viewer, "viewer")

        # 旧详情接口：第 39 批起改用 require_session_read，成员不再 403。
        detail = self.get(f"/api/session/{self.session_id}", email=self.viewer)
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["my_role"], "viewer")
        self.assertEqual(detail.json()["owner_email"], self.email)

        # 旧列表：包含共享会话，并标出我的角色。
        listed = self.get("/api/session", email=self.viewer)
        self.assertEqual(listed.status_code, 200, listed.text)
        rows = listed.json()
        self.assertEqual([row["id"] for row in rows], [self.session_id])
        self.assertEqual(rows[0]["my_role"], "viewer")

        # 陌生人仍然拿不到。
        denied = self.get(f"/api/session/{self.session_id}", email=self.stranger)
        self.assertEqual(denied.status_code, 403, denied.text)

    def test_leave_shared_session_revokes_access_immediately(self) -> None:
        self._share(self.viewer, "viewer")
        self.assertEqual(self._overview(self.viewer)["totals"]["incoming"], 1)

        left = self.delete(f"/api/v2/sessions/{self.session_id}/share", email=self.viewer)
        self.assertEqual(left.status_code, 200, left.text)
        self.assertEqual(left.json()["status"], "left")

        self.assertEqual(self._overview(self.viewer)["incoming"], [])
        blocked = self.get(f"/api/v2/sessions/{self.session_id}", email=self.viewer)
        self.assertEqual(blocked.status_code, 403, blocked.text)
        # 所有者侧不再有这条共享行。
        self.assertEqual(self._overview()["owned"][0]["shares"], [])
        # 退出后再次退出：已无访问权，仍是 403（不是 200 假成功）。
        again = self.delete(f"/api/v2/sessions/{self.session_id}/share", email=self.viewer)
        self.assertEqual(again.status_code, 403, again.text)

    def test_owner_cannot_leave_own_share_and_stranger_is_forbidden(self) -> None:
        owner_leave = self.delete(f"/api/v2/sessions/{self.session_id}/share")
        self.assertEqual(owner_leave.status_code, 422, owner_leave.text)
        self.assertEqual(owner_leave.json()["error"]["code"], "INVALID_REQUEST")
        self.assertIn("所有者", owner_leave.json()["error"]["message"])

        stranger_leave = self.delete(
            f"/api/v2/sessions/{self.session_id}/share", email=self.stranger
        )
        self.assertEqual(stranger_leave.status_code, 403, stranger_leave.text)

    def test_groups_are_listed_in_overview(self) -> None:
        created = self.post("/api/v2/groups", json={"name": "权限页验收组"})
        self.assertEqual(created.status_code, 201, created.text)
        group_id = created.json()["id"]
        added = self.post(
            f"/api/v2/groups/{group_id}/members",
            json={"email": self.viewer, "role": "editor"},
        )
        self.assertEqual(added.status_code, 201, added.text)

        data = self._overview()
        self.assertEqual(len(data["groups"]), 1)
        group = data["groups"][0]
        self.assertEqual(group["name"], "权限页验收组")
        self.assertEqual([member["email"] for member in group["members"]], [self.viewer])
        self.assertEqual(data["totals"]["groups"], 1)

    def test_access_requires_login(self) -> None:
        anonymous = self.client.get("/api/v2/access")
        self.assertEqual(anonymous.status_code, 401, anonymous.text)


if __name__ == "__main__":
    unittest.main()
