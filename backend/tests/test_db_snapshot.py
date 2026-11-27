"""数据快照持久化的单元测试（第 46 批）。

覆盖目标（都是线上会踩的路径）：

* 配置解析：缺任何一项必须退化成 no-op，绝不因为「线上没配」而阻塞本地开发；
* 编码/解码：HF 拒收二进制，所以走 gzip+base64 文本；下载到错误页时必须能被挡住；
* 一致性拷贝：必须走 sqlite3 backup，否则 WAL 未 checkpoint 会拷贝出残缺库；
* 恢复：本地库已存在不覆盖、远端 404 视为正常、任何失败都不上抛（不能拖垮启动）；
* 上传：内容未变化时跳过（否则 dataset 仓库会被无意义的提交灌满）。
"""

from __future__ import annotations

import gzip
import io
import json
import os
import sqlite3
import tempfile
import urllib.error
import unittest
from pathlib import Path
from unittest import mock

from core import db_snapshot


def _fake_urlopen(payload: bytes, *, status: int = 200):
    """返回一个像 urlopen 的可调用对象，记录收到的 Request。"""

    calls: list[object] = []

    def _open(request, timeout=None):  # noqa: ANN001 - 与 urllib 签名一致即可
        calls.append(request)
        if status >= 400:
            raise urllib.error.HTTPError(
                request.full_url, status, "boom", hdrs=None, fp=io.BytesIO(b'{"error":"nope"}')
            )
        return io.BytesIO(payload)

    _open.calls = calls  # type: ignore[attr-defined]
    return _open


class SnapshotConfigTests(unittest.TestCase):
    def test_disabled_without_repo(self) -> None:
        config = db_snapshot.snapshot_config({"DATABASE_URL": "sqlite+pysqlite:///C:/tmp/app.db"})
        self.assertFalse(config["enabled"])
        self.assertEqual(config["repo"], "")

    def test_disabled_without_token(self) -> None:
        config = db_snapshot.snapshot_config(
            {"DB_SNAPSHOT_REPO": "owner/db", "DATABASE_URL": "sqlite+pysqlite:///C:/tmp/app.db"}
        )
        self.assertFalse(config["enabled"])

    def test_enabled_with_repo_token_and_sqlite_path(self) -> None:
        config = db_snapshot.snapshot_config(
            {
                "DB_SNAPSHOT_REPO": "owner/db",
                "DB_SNAPSHOT_TOKEN": "hf_x",
                "DATABASE_URL": "sqlite+pysqlite:///C:/tmp/app.db",
            }
        )
        self.assertTrue(config["enabled"])
        self.assertEqual(config["local_path"], "C:/tmp/app.db")
        self.assertEqual(config["interval_seconds"], db_snapshot.DEFAULT_INTERVAL_SECONDS)

    def test_token_falls_back_to_space_secret_name(self) -> None:
        # 第 46 批先用 DB_SYNC_TOKEN 写进 Space secret，不能因为改名就被晾着。
        config = db_snapshot.snapshot_config(
            {
                "DB_SNAPSHOT_REPO": "owner/db",
                "DB_SYNC_TOKEN": "hf_sync",
                "DATABASE_URL": "sqlite+pysqlite:///C:/tmp/app.db",
            }
        )
        self.assertEqual(config["token"], "hf_sync")
        self.assertTrue(config["enabled"])

    def test_interval_is_clamped(self) -> None:
        config = db_snapshot.snapshot_config(
            {
                "DB_SNAPSHOT_REPO": "owner/db",
                "DB_SNAPSHOT_TOKEN": "hf_x",
                "DB_SNAPSHOT_INTERVAL_SECONDS": "1",
                "DATABASE_URL": "sqlite+pysqlite:///C:/tmp/app.db",
            }
        )
        self.assertEqual(config["interval_seconds"], db_snapshot.MIN_INTERVAL_SECONDS)

    def test_non_sqlite_database_url_has_no_local_path(self) -> None:
        self.assertEqual(db_snapshot.sqlite_path_from_database_url("postgresql://x/y"), "")
        config = db_snapshot.snapshot_config(
            {"DB_SNAPSHOT_REPO": "owner/db", "DB_SNAPSHOT_TOKEN": "hf_x", "DATABASE_URL": "postgresql://x/y"}
        )
        self.assertFalse(config["enabled"])


class SnapshotCodecTests(unittest.TestCase):
    def test_encode_decode_roundtrip(self) -> None:
        raw = b"SQLite format 3\x00" + os.urandom(2048)
        text = db_snapshot.encode_snapshot(raw)
        self.assertTrue(text.isascii())
        self.assertEqual(db_snapshot.decode_snapshot(text), raw)

    def test_decode_rejects_non_sqlite_payload(self) -> None:
        # 下载到 HTML 错误页/JSON 报错时，绝不能把垃圾当成库写进磁盘。
        bogus = db_snapshot.encode_snapshot(b"<html>502 Bad Gateway</html>")
        with self.assertRaises(ValueError):
            db_snapshot.decode_snapshot(bogus)

    def test_commit_body_is_two_ndjson_lines(self) -> None:
        body = db_snapshot.build_commit_body("abc", summary="snap")
        lines = body.decode("utf-8").strip().split("\n")
        self.assertEqual(len(lines), 2)
        header = json.loads(lines[0])
        self.assertEqual(header["key"], "header")
        self.assertEqual(header["value"]["summary"], "snap")
        entry = json.loads(lines[1])
        self.assertEqual(entry["value"]["path"], db_snapshot.SNAPSHOT_FILENAME)
        self.assertEqual(entry["value"]["encoding"], "base64")

    def test_commit_url_has_api_prefix_but_download_does_not(self) -> None:
        # 这条规则是踩过的坑：提交少 /api 会 404，下载多 /api 也会 404。
        self.assertIn("/api/datasets/owner/db/commit/main", db_snapshot.snapshot_commit_url("owner/db"))
        self.assertIn("/datasets/owner/db/resolve/main/", db_snapshot.snapshot_download_url("owner/db"))


class SnapshotCopyTests(unittest.TestCase):
    def test_consistent_copy_keeps_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            source = Path(tmpdir) / "app.db"
            target = Path(tmpdir) / "copy.db"
            conn = sqlite3.connect(str(source))
            try:
                conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
                conn.execute("INSERT INTO t (name) VALUES ('a'), ('b')")
                conn.commit()
            finally:
                conn.close()
            size = db_snapshot.copy_sqlite_consistent(source, target)
            self.assertGreater(size, 0)
            copy = sqlite3.connect(str(target))
            try:
                rows = copy.execute("SELECT COUNT(*) FROM t").fetchone()[0]
            finally:
                copy.close()
            self.assertEqual(rows, 2)


class RestoreSnapshotTests(unittest.TestCase):
    def _config(self, *, local_path: Path, enabled: bool = True) -> dict[str, object]:
        return {
            "enabled": enabled,
            "repo": "owner/db",
            "token": "hf_x",
            "interval_seconds": 120,
            "local_path": str(local_path),
        }

    def test_disabled_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = self._config(local_path=Path(tmpdir) / "app.db", enabled=False)
            result = db_snapshot.restore_snapshot(config, urlopen=_fake_urlopen(b"{}"))
        self.assertIn("skipped", result)

    def test_existing_database_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            local = Path(tmpdir) / "app.db"
            local.write_bytes(b"SQLite format 3\x00existing")
            result = db_snapshot.restore_snapshot(self._config(local_path=local), urlopen=_fake_urlopen(b"{}"))
            self.assertIn("本地库已存在", result)
            self.assertIn("existing", local.read_bytes().decode("utf-8", "replace"))

    def test_404_means_first_deploy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            local = Path(tmpdir) / "app.db"
            result = db_snapshot.restore_snapshot(
                self._config(local_path=local), urlopen=_fake_urlopen(b"", status=404)
            )
            self.assertIn("首次部署", result)
            self.assertFalse(local.exists())

    def test_http_error_does_not_raise(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            local = Path(tmpdir) / "app.db"
            result = db_snapshot.restore_snapshot(
                self._config(local_path=local), urlopen=_fake_urlopen(b"", status=500)
            )
            self.assertIn("failed", result)
            self.assertFalse(local.exists())

    def test_network_failure_does_not_raise(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            local = Path(tmpdir) / "app.db"

            def _boom(request, timeout=None):  # noqa: ANN001
                raise OSError("network down")

            result = db_snapshot.restore_snapshot(self._config(local_path=local), urlopen=_boom)
        self.assertIn("failed", result)

    def test_restore_writes_database(self) -> None:
        raw = b"SQLite format 3\x00" + os.urandom(512)
        payload = db_snapshot.encode_snapshot(raw).encode("utf-8")
        with tempfile.TemporaryDirectory() as tmpdir:
            local = Path(tmpdir) / "app.db"
            result = db_snapshot.restore_snapshot(self._config(local_path=local), urlopen=_fake_urlopen(payload))
            self.assertIn("restored", result)
            self.assertEqual(local.read_bytes(), raw)
            self.assertFalse(Path(str(local) + ".download").exists())

    def test_restore_rejects_non_sqlite_payload(self) -> None:
        payload = db_snapshot.encode_snapshot(b"<html>error</html>").encode("utf-8")
        with tempfile.TemporaryDirectory() as tmpdir:
            local = Path(tmpdir) / "app.db"
            result = db_snapshot.restore_snapshot(self._config(local_path=local), urlopen=_fake_urlopen(payload))
            self.assertIn("failed", result)
            self.assertFalse(local.exists())


class SnapshotUploaderTests(unittest.TestCase):
    def _make_db(self, path: Path, rows: int = 1) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path))
        try:
            conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT)")
            for index in range(rows):
                conn.execute("INSERT INTO t (name) VALUES (?)", (f"n{index}",))
            conn.commit()
        finally:
            conn.close()

    def _config(self, *, local_path: Path, enabled: bool = True) -> dict[str, object]:
        return {
            "enabled": enabled,
            "repo": "owner/db",
            "token": "hf_x",
            "interval_seconds": 120,
            "local_path": str(local_path),
        }

    def test_disabled_flush_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            uploader = db_snapshot.SnapshotUploader(
                self._config(local_path=Path(tmpdir) / "app.db", enabled=False)
            )
            self.assertEqual(uploader.flush(), "skipped: 未配置")
            self.assertEqual(uploader.uploads, 0)

    def test_missing_database_skips(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            fake = _fake_urlopen(b'{"commitOid":"abc"}')
            uploader = db_snapshot.SnapshotUploader(
                self._config(local_path=Path(tmpdir) / "app.db"), urlopen=fake
            )
            self.assertIn("本地库不存在", uploader.flush())
            self.assertEqual(fake.calls, [])

    def test_upload_then_unchanged_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            local = Path(tmpdir) / "app.db"
            self._make_db(local)
            fake = _fake_urlopen(b'{"commitOid":"deadbeefcafe"}')
            uploader = db_snapshot.SnapshotUploader(self._config(local_path=local), urlopen=fake)
            first = uploader.flush()
            self.assertIn("uploaded", first)
            self.assertIn("deadbeef", first)
            self.assertEqual(uploader.uploads, 1)
            self.assertEqual(len(fake.calls), 1)

            # 内容没变时不该再提交一次（否则仓库历史会被无意义提交灌满）。
            second = uploader.flush()
            self.assertIn("内容未变化", second)
            self.assertEqual(uploader.skipped, 1)
            self.assertEqual(len(fake.calls), 1)

            # 数据变化后才重新上传。
            conn = sqlite3.connect(str(local))
            try:
                conn.execute("INSERT INTO t (name) VALUES ('new')")
                conn.commit()
            finally:
                conn.close()
            third = uploader.flush()
            self.assertIn("uploaded", third)
            self.assertEqual(uploader.uploads, 2)

    def test_upload_failure_is_counted_not_raised(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            local = Path(tmpdir) / "app.db"
            self._make_db(local)
            uploader = db_snapshot.SnapshotUploader(
                self._config(local_path=local), urlopen=_fake_urlopen(b"", status=400)
            )
            result = uploader.flush()
            self.assertIn("failed", result)
            self.assertEqual(uploader.failures, 1)

    def test_request_carries_bearer_token_and_ndjson(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            local = Path(tmpdir) / "app.db"
            self._make_db(local)
            fake = _fake_urlopen(b'{"commitOid":"abc"}')
            db_snapshot.upload_snapshot(self._config(local_path=local), urlopen=fake)
        request = fake.calls[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer hf_x")
        self.assertIn("/api/datasets/owner/db/commit/main", request.full_url)
        self.assertEqual(request.get_header("Content-type"), "application/x-ndjson")
        body = json.loads(request.data.decode("utf-8").strip().split("\n")[1])
        self.assertEqual(body["value"]["path"], db_snapshot.SNAPSHOT_FILENAME)

    def test_snapshot_is_readable_by_sqlite(self) -> None:
        """上传的文本必须能被还原成可打开的库（整体往返，防止编码层写坏）。"""

        with tempfile.TemporaryDirectory() as tmpdir:
            local = Path(tmpdir) / "app.db"
            self._make_db(local, rows=3)
            captured: dict[str, str] = {}

            def _capture(request, timeout=None):  # noqa: ANN001
                entry = json.loads(request.data.decode("utf-8").strip().split("\n")[1])
                captured["text"] = __import__("base64").b64decode(entry["value"]["content"]).decode("ascii")
                return io.BytesIO(b'{"commitOid":"abc"}')

            db_snapshot.upload_snapshot(self._config(local_path=local), urlopen=_capture)
            raw = db_snapshot.decode_snapshot(captured["text"])
            restored = Path(tmpdir) / "restored.db"
            restored.write_bytes(raw)
            conn = sqlite3.connect(str(restored))
            try:
                rows = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
            finally:
                conn.close()
        self.assertEqual(rows, 3)


class SnapshotFileFormatTests(unittest.TestCase):
    def test_gzip_wrapper_is_valid(self) -> None:
        # 明确钉住「gzip + base64」这个格式，避免有人顺手改成裸 base64 后线上体积暴涨。
        raw = b"SQLite format 3\x00" + b"x" * 1000
        text = db_snapshot.encode_snapshot(raw)
        self.assertEqual(gzip.decompress(__import__("base64").b64decode(text)), raw)


if __name__ == "__main__":
    unittest.main()
