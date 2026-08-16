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


if __name__ == "__main__":
    unittest.main()
