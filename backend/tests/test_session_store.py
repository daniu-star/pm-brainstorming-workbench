"""SessionStore 单元测试：路径穿越拒绝、并发写、原子写、消息截断、owner 过滤。"""
import os
import re
import tempfile
import threading
import unittest

from db.session_store import MAX_MESSAGES, SessionStore


class SessionStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = SessionStore(data_dir=self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_create_returns_32_hex_id_with_owner(self):
        session = self.store.create("做一个自动写周报的工具", owner_email="a@example.com")
        self.assertTrue(re.fullmatch(r"[0-9a-f]{32}", session["id"]))
        self.assertEqual(session["owner_email"], "a@example.com")
        self.assertEqual(session["canvas_last_msg_index"], 0)

    def test_rejects_path_traversal_ids(self):
        for bad_id in ["../../etc/passwd", "../app/.env", "abc", "x" * 32, "UPPERCASE1234567890"]:
            with self.assertRaises(ValueError, msg=bad_id):
                self.store.get(bad_id)
            with self.assertRaises(ValueError, msg=bad_id):
                self.store.update(bad_id, {})
            with self.assertRaises(ValueError, msg=bad_id):
                self.store.add_message(bad_id, "user", "hi")
            with self.assertRaises(ValueError, msg=bad_id):
                self.store.delete(bad_id)
        # 确认没有在数据目录之外产生任何文件
        self.assertEqual(os.listdir(self._tmp.name), [])

    def test_concurrent_add_message_loses_nothing(self):
        session = self.store.create("并发测试")
        threads = [
            threading.Thread(
                target=lambda i=i: self.store.add_message(session["id"], "user", f"消息-{i}")
            )
            for i in range(40)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        stored = self.store.get(session["id"])
        self.assertEqual(len(stored["messages"]), 40)

    def test_atomic_write_leaves_no_temp_files(self):
        session = self.store.create("原子写测试")
        self.store.add_message(session["id"], "user", "hello")
        files = sorted(os.listdir(self._tmp.name))
        self.assertEqual(files, [f"{session['id']}.json"])
        # 内容完整可解析
        stored = self.store.get(session["id"])
        self.assertEqual(stored["messages"][0]["content"], "hello")

    def test_add_message_truncates_to_max(self):
        session = self.store.create("截断测试")
        for i in range(MAX_MESSAGES + 30):
            self.store.add_message(session["id"], "user", f"m{i}")
        stored = self.store.get(session["id"])
        self.assertEqual(len(stored["messages"]), MAX_MESSAGES)
        # 保留的是最近 N 条
        self.assertEqual(stored["messages"][0]["content"], "m30")
        self.assertEqual(stored["messages"][-1]["content"], f"m{MAX_MESSAGES + 29}")

    def test_list_sessions_filters_by_owner(self):
        self.store.create("A 的会话", owner_email="a@example.com")
        self.store.create("B 的会话", owner_email="b@example.com")
        only_a = self.store.list_sessions(owner_email="a@example.com")
        self.assertEqual(len(only_a), 1)
        only_b = self.store.list_sessions(owner_email="b@example.com")
        self.assertEqual(len(only_b), 1)
        all_sessions = self.store.list_sessions()
        self.assertEqual(len(all_sessions), 2)

    def test_update_and_delete_missing_session_raises(self):
        fake_id = "0" * 32
        with self.assertRaises(ValueError):
            self.store.update(fake_id, {"phase": "coach"})
        with self.assertRaises(ValueError):
            self.store.add_message(fake_id, "user", "hi")
        # delete 对不存在的合法 ID 静默成功
        self.store.delete(fake_id)

    def test_atomic_write_retries_when_target_is_temporarily_locked(self):
        """Windows 上 os.replace 可能被占用文件的进程短暂拒绝，必须有界重试而不是丢消息。"""
        from unittest import mock

        session = self.store.create("重试测试")
        real_replace = os.replace
        attempts = {"count": 0}

        def flaky_replace(src, dst):
            attempts["count"] += 1
            if attempts["count"] <= 2:
                raise PermissionError(13, "拒绝访问")
            return real_replace(src, dst)

        with mock.patch("db.session_store.os.replace", side_effect=flaky_replace):
            self.store.add_message(session["id"], "user", "第一次就应当写成功")

        self.assertEqual(attempts["count"], 3)
        stored = self.store.get(session["id"])
        self.assertEqual([m["content"] for m in stored["messages"]], ["第一次就应当写成功"])
        self.assertEqual(sorted(os.listdir(self._tmp.name)), [f"{session['id']}.json"])

    def test_atomic_write_raises_after_retries_are_exhausted(self):
        """重试耗尽后必须把错误抛出去，不能静默返回成功。"""
        from unittest import mock

        session = self.store.create("重试耗尽")

        with mock.patch("db.session_store.os.replace", side_effect=PermissionError(13, "拒绝访问")):
            with self.assertRaises(PermissionError):
                self.store.add_message(session["id"], "user", "这条写不进去")
        # 临时文件必须被清理，数据目录只留原始会话文件
        self.assertEqual(sorted(os.listdir(self._tmp.name)), [f"{session['id']}.json"])


if __name__ == "__main__":
    unittest.main()
