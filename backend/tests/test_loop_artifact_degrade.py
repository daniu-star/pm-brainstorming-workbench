"""闭环产物损坏时的降级测试。

背景
----
`loop_artifacts.payload_json` 是历史 JSON：契约模型收紧字段类型（例如
`PrototypeSpec.limitations` 从 list 变 str）、或某次工作流写了半截载荷之后，
库里就会留下**无法反序列化**的行。

`loop_to_snapshot` 是只读聚合接口，页面首屏就要用它。如果它对一条脏数据抛
异常，用户看到的是「闭环读取失败」且没有任何自助恢复路径（PRD 错误处理要求
可诊断、可恢复）——所以这里断言「逐条降级 + 记日志 + 其余产物照常返回」。

同时确保：损坏行**不被删除**，仍可通过产物详情接口取原始载荷排查。
"""
from __future__ import annotations

import atexit
import gc
import shutil
import tempfile
import unittest
from pathlib import Path

from domain import contracts as C
from repositories import database, loop_repo, session_repo

OWNER = "artifact-degrade@example.com"
_BACKEND_DIR = Path(__file__).resolve().parents[1]
_TMP_ROOT = _BACKEND_DIR / ".tmp_tests"
_TEMP_DIRS: list[str] = []


def _cleanup() -> None:
    try:
        gc.collect()
        database.dispose_engine()
    finally:
        for item in _TEMP_DIRS:
            shutil.rmtree(item, ignore_errors=True)
        _TEMP_DIRS.clear()


atexit.register(_cleanup)


class ArtifactDegradeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _TMP_ROOT.mkdir(parents=True, exist_ok=True)
        cls._dir = tempfile.mkdtemp(prefix="loop_artifact_", dir=str(_TMP_ROOT))
        _TEMP_DIRS.append(cls._dir)
        database.configure(f"sqlite+pysqlite:///{Path(cls._dir) / 'app.db'}", force=True)
        database.initialize_database(drop_first=True)

    @classmethod
    def tearDownClass(cls) -> None:
        _cleanup()

    def _seed_loop(self) -> tuple[str, str]:
        session_id = C.new_id()
        now = C.utc_now_iso()
        with database.session_scope() as db:
            session_repo.create_session(
                db,
                session_id=session_id,
                owner_email=OWNER,
                problem_statement="产物降级",
                now_iso=now,
            )
            loop = loop_repo.create_loop(
                db,
                session_id=session_id,
                owner_email=OWNER,
                now_iso=now,
            )
            loop_repo.ensure_round(db, loop_id=loop.id, ordinal=1, now_iso=now)
            loop_id = loop.id
        return session_id, loop_id

    def test_corrupted_prototype_payload_degrades_instead_of_500(self) -> None:
        _session_id, loop_id = self._seed_loop()
        now = C.utc_now_iso()

        with database.session_scope() as db:
            # 合法产物：应正常还原
            loop_repo.add_artifact(
                db,
                loop_id=loop_id,
                ordinal=1,
                kind=C.LoopArtifactKind.mvp_spec,
                title="MVP",
                payload={
                    "title": "闭环 MVP",
                    "summary": "主路径",
                    "features": [
                        {
                            "id": "f1",
                            "title": "提交",
                            "description": "提交预算",
                            "acceptance": "可提交",
                            "priority": "must",
                            "effort": "S",
                        }
                    ],
                },
                now_iso=now,
            )
            # 损坏产物：limitations 是 list，但契约要求 str
            loop_repo.add_artifact(
                db,
                loop_id=loop_id,
                ordinal=1,
                kind=C.LoopArtifactKind.prototype,
                title="原型",
                payload={
                    "title": "原型",
                    "summary": "单页",
                    "entry_file": "index.html",
                    "files": [],
                    "limitations": ["无后端"],
                },
                now_iso=now,
            )

        with database.session_scope() as db:
            loop = loop_repo.get_loop(db, loop_id)
            assert loop is not None
            with self.assertLogs("repositories.loop_repo", level="WARNING") as captured:
                snapshot = loop_repo.loop_to_snapshot(db, loop)

        # 合法产物照常返回
        self.assertIsNotNone(snapshot.mvp_spec)
        self.assertEqual(snapshot.mvp_spec.features[0].id, "f1")
        # 损坏产物降级为 None（页面显示「未生成」，而不是整页 500）
        self.assertIsNone(snapshot.prototype)
        # 日志里能定位到具体行，便于排查
        joined = "\n".join(captured.output)
        self.assertIn("prototype", joined)
        self.assertIn(loop_id, joined)

        # 原始载荷仍可取回（降级不等于丢弃）
        with database.session_scope() as db:
            artifacts = loop_repo.list_artifacts(db, loop_id=loop_id, ordinal=1)
            prototype_rows = [item for item in artifacts if item.kind == "prototype"]
            self.assertEqual(len(prototype_rows), 1)
            self.assertIn("无后端", prototype_rows[0].payload_json)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
