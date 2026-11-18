"""迁移导入器测试（PRD §8.1）。

使用独立临时 sqlite 文件与合成源目录，绝不触碰 ``backend/data`` 下的真实归档。
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import select

from domain.contracts import sha256_text
from migrations import import_json_sessions
from repositories import database
from repositories.database import (
    AuditRow,
    LegacyArtifactRow,
    MessageRow,
    MigrationRecordRow,
    ProductStateRow,
    SessionRow,
)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class MigrationImportTest(unittest.TestCase):
    """覆盖 dry-run/apply、幂等、冲突、隔离与归档语义。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.source_dir = self.tmp_path / "sessions"
        self.source_dir.mkdir()
        self.manifest_dir = self.tmp_path / "backups"
        self.db_file = self.tmp_path / "app.db"
        database.configure(f"sqlite:///{self.db_file.as_posix()}", force=True)
        database.initialize_database()

    def tearDown(self) -> None:
        # 恢复全局默认配置，避免把指向已删除临时库的 engine 留给后续测试模块。
        with contextlib.suppress(Exception):
            database.configure(database.default_database_url(), force=True)
        self._tmp.cleanup()

    # ------------------------------------------------------------------ #
    # 辅助
    # ------------------------------------------------------------------ #
    def _write_source(self, name: str, payload: dict) -> Path:
        path = self.source_dir / name
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def _run_cli(self, *args: str) -> tuple[int, str]:
        out = io.StringIO()
        argv = [
            "--source",
            str(self.source_dir),
            "--manifest-dir",
            str(self.manifest_dir),
            *args,
        ]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = import_json_sessions.main(argv)
        return code, out.getvalue()

    def _rows(self, model: type) -> list:
        with database.session_scope() as db:
            return list(db.execute(select(model)).scalars().all())

    def _session_by_id(self, session_id: str) -> SessionRow:
        with database.session_scope() as db:
            row = db.get(SessionRow, session_id)
            self.assertIsNotNone(row)
            return row

    def _mapped_id(self, legacy_id: str) -> str:
        return sha256_text(legacy_id)[:32]

    @staticmethod
    def _legacy_payload(legacy_id: str, **overrides: object) -> dict:
        payload = {
            "id": legacy_id,
            "problem_statement": "旧会话的问题描述",
            "phase": "brainstorm",
            "messages": [
                {"role": "user", "content": "问题一", "timestamp": "2025-07-25T15:47:26.742238"},
                {"role": "assistant", "content": "回答一", "timestamp": "2025-07-25T15:47:30"},
            ],
            "canvas_tree": None,
            "canvas_last_msg_index": None,
            "interview_dimensions_covered": [],
            "interview_question_count": 0,
            "created_at": "2025-07-25T15:47:26.742238",
        }
        payload.update(overrides)
        return payload

    # ------------------------------------------------------------------ #
    # dry-run
    # ------------------------------------------------------------------ #
    def test_dry_run_reports_without_writing(self) -> None:
        self._write_source("legacy.json", self._legacy_payload("0b1e146622ae"))
        code, output = self._run_cli("--dry-run")

        self.assertEqual(code, 0)
        report = json.loads(output)
        self.assertEqual(report["mode"], "dry-run")
        self.assertEqual(report["scanned"], 1)
        self.assertEqual(report["valid"], 1)
        self.assertEqual(report["invalid"], [])
        self.assertEqual(report["remapped_legacy_ids"], 1)
        self.assertEqual(report["planned"]["messages"], 2)
        self.assertEqual(report["planned"]["product_states"], 1)

        self.assertEqual(self._rows(SessionRow), [])
        self.assertEqual(self._rows(MessageRow), [])
        self.assertEqual(self._rows(ProductStateRow), [])
        self.assertEqual(self._rows(MigrationRecordRow), [])
        self.assertFalse(self.manifest_dir.exists())

    def test_dry_run_does_not_leak_content_or_email(self) -> None:
        marker = "SECRET-PROBLEM-STATEMENT-9c1f"
        email = "owner-marker@example.com"
        self._write_source(
            "private.json",
            self._legacy_payload("0b1e146622ae", problem_statement=marker, owner_email=email),
        )
        _, output = self._run_cli("--dry-run")
        self.assertNotIn(marker, output)
        self.assertNotIn(email, output)
        self.assertNotIn("问题一", output)

    def test_dry_run_reports_invalid_and_duplicates(self) -> None:
        self._write_source("dup-a.json", self._legacy_payload("abcdef123456"))
        self._write_source("dup-b.json", self._legacy_payload("abcdef123456"))
        (self.source_dir / "broken.json").write_text("{not json", encoding="utf-8")

        _, output = self._run_cli("--dry-run")
        report = json.loads(output)
        self.assertEqual(report["scanned"], 3)
        self.assertEqual(report["valid"], 2)
        self.assertEqual(len(report["invalid"]), 1)
        self.assertEqual(report["duplicate_legacy_ids"]["abcdef123456"], [
            str((self.source_dir / "dup-a.json").resolve()),
            str((self.source_dir / "dup-b.json").resolve()),
        ])

    # ------------------------------------------------------------------ #
    # apply
    # ------------------------------------------------------------------ #
    def test_apply_imports_sessions_messages_and_product_states(self) -> None:
        remapped_file = self._write_source(
            "remapped.json",
            self._legacy_payload(
                "0b1e146622ae",
                phase="coach",
                canvas_tree={"root": "根问题", "branches": []},
                canvas_last_msg_index=1,
            ),
        )
        stable_id = "0123456789abcdef0123456789abcdef"
        self._write_source(
            "stable.json",
            self._legacy_payload(stable_id, phase="brainstorm", owner_email="real@example.com"),
        )
        before_hashes = {path.name: _hash(path) for path in self.source_dir.glob("*.json")}

        code, output = self._run_cli("--apply")
        self.assertEqual(code, 0)
        summary = json.loads(output)
        self.assertEqual(summary["mode"], "apply")
        self.assertEqual(summary["imported_sessions"], 2)
        self.assertEqual(summary["messages_imported"], 4)
        self.assertEqual(summary["product_states_imported"], 2)
        self.assertEqual(summary["legacy_artifacts_imported"], 1)
        self.assertTrue(Path(summary["manifest"]).is_file())

        sessions = self._rows(SessionRow)
        self.assertEqual(len(sessions), 2)
        mapped = self._session_by_id(self._mapped_id("0b1e146622ae"))
        self.assertEqual(mapped.phase, "clarifying")
        self.assertEqual(mapped.owner_email, import_json_sessions.LEGACY_UNKNOWN_OWNER)
        self.assertEqual(mapped.session_version, 1)
        self.assertEqual(mapped.brief_status, "draft")
        self.assertEqual(mapped.next_message_seq, 3)
        self.assertEqual(mapped.created_at, "2025-07-25T15:47:26Z")

        stable = self._session_by_id(stable_id)
        self.assertEqual(stable.owner_email, "real@example.com")
        self.assertEqual(stable.phase, "brainstorming")

        messages = self._rows(MessageRow)
        self.assertEqual(sorted(row.seq for row in messages), [1, 1, 2, 2])
        for row in messages:
            self.assertEqual(row.status, "completed")
            self.assertIn(row.created_at, {"2025-07-25T15:47:26Z", "2025-07-25T15:47:30Z"})
            if row.content == "问题一":
                self.assertEqual(row.sender_kind, "human")
            if row.content == "回答一":
                self.assertEqual(row.sender_kind, "agent")
        self.assertEqual(len(self._rows(ProductStateRow)), 2)

        canvas = [
            row
            for row in self._rows(LegacyArtifactRow)
            if row.kind == import_json_sessions.ARTIFACT_KIND_CANVAS
        ]
        self.assertEqual(len(canvas), 1)
        payload = json.loads(canvas[0].payload_json)
        self.assertFalse(payload["source_text_verified"])
        self.assertEqual(payload["canvas_tree"]["root"], "根问题")

        records = self._rows(MigrationRecordRow)
        self.assertEqual(len(records), 2)
        self.assertTrue(all(row.import_status == "imported" for row in records))
        remapped_record = next(row for row in records if row.source_path.endswith("remapped.json"))
        self.assertIn(
            "legacy_id_remapped:0b1e146622ae->" + self._mapped_id("0b1e146622ae"),
            json.loads(remapped_record.warnings_json),
        )

        after_hashes = {path.name: _hash(path) for path in self.source_dir.glob("*.json")}
        self.assertEqual(before_hashes, after_hashes)
        self.assertEqual(remapped_file.exists(), True)

    def test_apply_is_idempotent(self) -> None:
        self._write_source("legacy.json", self._legacy_payload("0b1e146622ae"))
        first = json.loads(self._run_cli("--apply")[1])
        self.assertEqual(first["imported_sessions"], 1)

        second = json.loads(self._run_cli("--apply")[1])
        self.assertEqual(second["imported_sessions"], 0)
        self.assertEqual(second["skipped"], 1)
        self.assertEqual(second["conflicts"], [])
        self.assertEqual(len(self._rows(SessionRow)), 1)
        self.assertEqual(len(self._rows(MessageRow)), 2)
        self.assertEqual(len(self._rows(MigrationRecordRow)), 1)

    def test_changed_source_is_conflict_not_overwrite(self) -> None:
        path = self._write_source("legacy.json", self._legacy_payload("0b1e146622ae"))
        self._run_cli("--apply")

        path.write_text(
            json.dumps(
                self._legacy_payload(
                    "0b1e146622ae", problem_statement="内容被修改后的描述"
                ),
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        summary = json.loads(self._run_cli("--apply")[1])
        self.assertEqual(summary["imported_sessions"], 0)
        self.assertEqual(len(summary["conflicts"]), 1)

        mapped = self._session_by_id(self._mapped_id("0b1e146622ae"))
        self.assertEqual(mapped.problem_statement, "旧会话的问题描述")

    def test_duplicate_legacy_ids_do_not_create_twins(self) -> None:
        self._write_source("dup-a.json", self._legacy_payload("abcdef123456"))
        self._write_source("dup-b.json", self._legacy_payload("abcdef123456"))
        summary = json.loads(self._run_cli("--apply")[1])
        self.assertEqual(summary["imported_sessions"], 1)
        self.assertEqual(len(summary["conflicts"]), 1)
        self.assertEqual(len(self._rows(SessionRow)), 1)

    def test_invalid_files_are_recorded(self) -> None:
        (self.source_dir / "broken.json").write_text("{not json", encoding="utf-8")
        self._write_source("list.json", ["not", "an", "object"])
        summary = json.loads(self._run_cli("--apply")[1])
        self.assertEqual(summary["imported_sessions"], 0)
        self.assertEqual(len(summary["invalid"]), 2)
        records = self._rows(MigrationRecordRow)
        self.assertEqual(len(records), 2)
        self.assertTrue(all(row.import_status == "invalid" for row in records))
        self.assertTrue(all(row.session_id is None for row in records))
        self.assertEqual(self._rows(SessionRow), [])

    def test_empty_owner_is_isolated(self) -> None:
        self._write_source("no-owner.json", self._legacy_payload("0b1e146622ae"))
        self._write_source(
            "blank-owner.json", self._legacy_payload("abcdef123456", owner_email="   ")
        )
        self._run_cli("--apply")
        for session in self._rows(SessionRow):
            self.assertEqual(session.owner_email, import_json_sessions.LEGACY_UNKNOWN_OWNER)

    # ------------------------------------------------------------------ #
    # 旧面试与阶段映射
    # ------------------------------------------------------------------ #
    def test_legacy_interview_is_cancelled_and_archived(self) -> None:
        self._write_source(
            "interview.json",
            self._legacy_payload(
                "e4d65c0de875",
                phase="interview",
                interview_dimensions_covered=["business_viability"],
                interview_question_count=25,
            ),
        )
        summary = json.loads(self._run_cli("--apply")[1])
        self.assertEqual(summary["legacy_audits_imported"], 1)

        session = self._session_by_id(self._mapped_id("e4d65c0de875"))
        self.assertEqual(session.phase, "auditing")

        audits = self._rows(AuditRow)
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0].status, "cancelled")
        self.assertEqual(audits[0].end_reason, "legacy_unstructured")
        self.assertEqual(audits[0].question_count, 18)
        self.assertEqual(json.loads(audits[0].dimension_states_json), [])

        artifacts = [
            row
            for row in self._rows(LegacyArtifactRow)
            if row.kind == import_json_sessions.ARTIFACT_KIND_INTERVIEW
        ]
        self.assertEqual(len(artifacts), 1)
        payload = json.loads(artifacts[0].payload_json)
        self.assertEqual(payload["legacy_phase"], "interview")
        self.assertEqual(payload["interview_dimensions_covered"], ["business_viability"])

        record = next(
            row
            for row in self._rows(MigrationRecordRow)
            if row.source_path.endswith("interview.json")
        )
        self.assertIn(
            "legacy_interview_cancelled:legacy_unstructured",
            json.loads(record.warnings_json),
        )

    def test_phase_mapping_and_history_incomplete(self) -> None:
        messages = [
            {"role": "user" if index % 2 == 0 else "assistant", "content": f"消息 {index}"}
            for index in range(205)
        ]
        self._write_source(
            "define.json",
            self._legacy_payload("define12345678", phase="define", messages=messages),
        )
        self._run_cli("--apply")
        session = self._session_by_id(self._mapped_id("define12345678"))
        self.assertEqual(session.phase, "clarifying")
        self.assertEqual(session.next_message_seq, 206)

        record = next(
            row
            for row in self._rows(MigrationRecordRow)
            if row.source_path.endswith("define.json")
        )
        self.assertIn("history_incomplete", json.loads(record.warnings_json))
        self.assertEqual(len(self._rows(MessageRow)), 205)

    def test_unknown_phase_falls_back_to_brainstorming(self) -> None:
        self._write_source(
            "unknown-phase.json", self._legacy_payload("abcdef123456", phase="legacy_mystery")
        )
        self._run_cli("--apply")
        session = self._session_by_id(self._mapped_id("abcdef123456"))
        self.assertEqual(session.phase, "brainstorming")


if __name__ == "__main__":
    unittest.main()