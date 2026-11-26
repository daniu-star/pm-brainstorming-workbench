"""团队工作区（成员组）的 API 级验收。

覆盖产品化第 37 批的最小闭环：
1. 所有者可以建组、加人（viewer / editor）、改角色、删人、删组；
2. 名称在同一所有者下唯一，空名/超长名被拒；
3. 非所有者看不到、改不了别人的组；
4. 「把组应用到会话」会把成员逐条写进 session_shares，重复应用幂等；
5. 组本身不授予权限：删组不影响已共享的访问，删共享行才撤销访问。
"""
from __future__ import annotations

import unittest
import uuid

from domain import contracts as C
from repositories import database
from repositories.database import SessionRow
from tests.test_api_v2 import V2ApiTestBase


def _groups_url() -> str:
    return "/api/v2/groups"


def _members_url(group_id: str) -> str:
    return f"/api/v2/groups/{group_id}/members"


def _from_group_url(session_id: str) -> str:
    return f"/api/v2/sessions/{session_id}/shares/from-group"


class MemberGroupTests(V2ApiTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.teammate = f"teammate-{uuid.uuid4().hex[:8]}@example.com"
        self.editor = f"editor-{uuid.uuid4().hex[:8]}@example.com"
        self.stranger = f"stranger-{uuid.uuid4().hex[:8]}@example.com"
        created = self.post(
            "/api/v2/sessions",
            json={"problem_statement": "团队工作区验收：多人一起推同一个产品脑暴"},
            headers={"Idempotency-Key": uuid.uuid4().hex},
        )
        self.assertEqual(created.status_code, 201, created.text)
        self.session_id = created.json()["id"]

    # -- helpers ---------------------------------------------------------
    def _create_group(self, name: str, *, as_email: str | None = None):
        return self.post(_groups_url(), json={"name": name}, email=as_email or self.email)

    def _add(self, group_id: str, email: str, role: str = "viewer", *, as_email: str | None = None):
        return self.post(
            _members_url(group_id),
            json={"email": email, "role": role},
            email=as_email or self.email,
        )

    def _apply(self, group_id: str, *, as_email: str | None = None):
        return self.post(
            _from_group_url(self.session_id),
            json={"group_id": group_id},
            email=as_email or self.email,
        )

    # -- 组的生命周期 ------------------------------------------------------
    def test_create_group_and_list_with_members(self):
        created = self._create_group("核心评审团")
        self.assertEqual(created.status_code, 201, created.text)
        group = created.json()
        self.assertEqual(group["name"], "核心评审团")
        self.assertEqual(group["members"], [])

        added = self._add(group["id"], self.teammate, "viewer")
        self.assertEqual(added.status_code, 201, added.text)
        self.assertEqual(added.json(), {**added.json(), "email": self.teammate, "role": "viewer"})

        listed = self.get(_groups_url())
        self.assertEqual(listed.status_code, 200, listed.text)
        items = listed.json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], group["id"])
        self.assertEqual([m["email"] for m in items[0]["members"]], [self.teammate])

    def test_duplicate_name_and_blank_name_rejected(self):
        first = self._create_group("评审团")
        self.assertEqual(first.status_code, 201, first.text)
        again = self._create_group(" 评审团 ")
        self.assertEqual(again.status_code, 422, again.text)
        self.assertEqual(again.json()["error"]["code"], "INVALID_REQUEST")

        blank = self._create_group("   ")
        self.assertEqual(blank.status_code, 422, blank.text)

    def test_add_member_updates_role_and_rejects_owner_and_bad_role(self):
        group = self._create_group("评审团").json()
        self._add(group["id"], self.teammate, "viewer")
        changed = self._add(group["id"], self.teammate, "editor")
        self.assertEqual(changed.status_code, 201, changed.text)
        self.assertEqual(changed.json()["role"], "editor")

        self_join = self._add(group["id"], self.email, "editor")
        self.assertEqual(self_join.status_code, 422, self_join.text)
        self.assertEqual(self_join.json()["error"]["code"], "INVALID_REQUEST")

        bad_role = self._add(group["id"], self.teammate, "owner")
        self.assertEqual(bad_role.status_code, 422, bad_role.text)

    def test_remove_member_and_delete_group(self):
        group = self._create_group("评审团").json()
        self._add(group["id"], self.teammate)
        removed = self.delete(_members_url(group["id"]) + f"/{self.teammate}")
        self.assertIn(removed.status_code, (200, 204), removed.text)
        listed = self.get(_groups_url()).json()["items"]
        self.assertEqual(listed[0]["members"], [])

        deleted = self.delete(f"/api/v2/groups/{group['id']}")
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(self.get(_groups_url()).json()["items"], [])

    def test_stranger_cannot_see_or_touch_group(self):
        group = self._create_group("评审团").json()
        stranger_list = self.get(_groups_url(), email=self.stranger)
        self.assertEqual(stranger_list.status_code, 200, stranger_list.text)
        self.assertEqual(stranger_list.json()["items"], [])

        self.assertEqual(
            self.post(
                _members_url(group["id"]),
                json={"email": self.stranger},
                email=self.stranger,
            ).status_code,
            403,
        )
        self.assertEqual(self.delete(f"/api/v2/groups/{group['id']}", email=self.stranger).status_code, 403)

    # -- 应用到会话 --------------------------------------------------------
    def test_apply_group_shares_with_group_roles_and_is_idempotent(self):
        group = self._create_group("评审团").json()
        self._add(group["id"], self.teammate, "viewer")
        self._add(group["id"], self.editor, "editor")

        first = self._apply(group["id"])
        self.assertEqual(first.status_code, 201, first.text)
        receipt = first.json()
        self.assertEqual(receipt["added"], 2)
        self.assertEqual(receipt["already_shared"], 0)
        self.assertEqual(receipt["members_total"], 2)

        again = self._apply(group["id"])
        self.assertEqual(again.status_code, 201, again.text)
        self.assertEqual(again.json()["added"], 0)
        self.assertEqual(again.json()["already_shared"], 2)

        shares = self.get(f"/api/v2/sessions/{self.session_id}/shares").json()["items"]
        roles = {row["email"]: row["role"] for row in shares}
        self.assertEqual(roles[self.teammate], "viewer")
        self.assertEqual(roles[self.editor], "editor")

        # viewer 能读不能写；editor 能提交命令
        self.assertEqual(self.get(f"/api/v2/sessions/{self.session_id}", email=self.teammate).status_code, 200)
        denied = self.post(
            f"/api/v2/sessions/{self.session_id}/commands",
            json={"action": "skip_clarification", "payload": {}},
            email=self.teammate,
            headers={"Idempotency-Key": uuid.uuid4().hex},
        )
        self.assertEqual(denied.status_code, 403, denied.text)

    def test_apply_group_requires_owner_and_skips_owner(self):
        group = self._create_group("评审团").json()
        self._add(group["id"], self.teammate)
        # 把所有者自己塞进组不可能（add 会拦），这里验证应用时也只写他人
        receipt = self._apply(group["id"]).json()
        self.assertEqual(receipt["added"], 1)

        self.post(f"/api/v2/sessions/{self.session_id}/shares", json={"email": self.editor})
        stranger_apply = self._apply(group["id"], as_email=self.teammate)
        self.assertEqual(stranger_apply.status_code, 403, stranger_apply.text)

    def test_delete_group_keeps_granted_shares(self):
        group = self._create_group("评审团").json()
        self._add(group["id"], self.teammate)
        self._apply(group["id"])
        self.assertEqual(self.get(f"/api/v2/sessions/{self.session_id}", email=self.teammate).status_code, 200)

        self.delete(f"/api/v2/groups/{group['id']}")
        # 组删了，已授予的访问仍在（权限只看 session_shares）
        self.assertEqual(self.get(f"/api/v2/sessions/{self.session_id}", email=self.teammate).status_code, 200)

        self.delete(f"/api/v2/sessions/{self.session_id}/shares/{self.teammate}")
        self.assertEqual(
            self.get(f"/api/v2/sessions/{self.session_id}", email=self.teammate).status_code, 403
        )


if __name__ == "__main__":
    unittest.main()
