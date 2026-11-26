"""Loop Studio 真实执行沙箱（`services/loop_sandbox.py` + `/sandbox*` 端点）测试。

覆盖范围（全部走真实进程，不 mock ``subprocess``）：

1. 默认 fail-closed：未开启时 ``GET /sandbox`` 报未开启、``POST`` 503；
2. 白名单：不在白名单的可执行文件 422，shell 元字符 422；
3. 正常执行：退出码 0 → ``passed``，产物落库且能列出；
4. 非 0 退出 → ``failed`` 且保留 stderr 尾巴（必须留证据，不能抛异常）；
5. 超时 → ``timeout``，exit_code 为空，记录 timeout_seconds；
6. 输出截断 → ``truncated`` 且长度不超过配置上限；
7. 归属：他人闭环 403；
8. 环境隔离：子进程看不到 ``LLM_API_KEY``（用 python 打印环境变量做真实验证）；
9. 临时目录清理：运行结束后工作目录不残留。

注：测试用 ``sys.executable`` 作为白名单程序（basename 归一化到 ``python``），
因此不依赖 PATH 里有裸 ``python`` 命令。
"""
from __future__ import annotations

import json
import os
import sys
import unittest
import uuid

from core.config import settings
from domain import contracts as C
from repositories import database, loop_repo
from tests.test_api_v2 import V2ApiTestBase

_PY = sys.executable


class LoopSandboxTests(V2ApiTestBase):
    """沙箱测试：库由基类准备，闭环直接经仓储播种（沙箱端点不校验会话阶段）。"""

    _sandbox_originals: dict[str, object] = {}

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._sandbox_originals = {
            "loop_sandbox_enabled": settings.loop_sandbox_enabled,
            "loop_sandbox_allowed_commands": settings.loop_sandbox_allowed_commands,
            "loop_sandbox_timeout_seconds": settings.loop_sandbox_timeout_seconds,
            "loop_sandbox_max_output_bytes": settings.loop_sandbox_max_output_bytes,
            "loop_sandbox_workdir": settings.loop_sandbox_workdir,
        }

    @classmethod
    def tearDownClass(cls) -> None:
        for key, value in cls._sandbox_originals.items():
            setattr(settings, key, value)
        super().tearDownClass()

    def setUp(self) -> None:
        super().setUp()
        self.session_id = self.create_session()["id"]
        self.loop_id = self._seed_loop()

    # -- helpers ---------------------------------------------------------
    def _seed_loop(self) -> str:
        with database.session_scope() as db:
            loop = loop_repo.create_loop(
                db,
                session_id=self.session_id,
                owner_email=self.email,
                max_rounds=2,
                target_score=70,
            )
            return loop.id

    def _enable(self, *, allowed: str = "python") -> None:
        settings.loop_sandbox_enabled = True
        settings.loop_sandbox_allowed_commands = allowed
        settings.loop_sandbox_timeout_seconds = 8
        settings.loop_sandbox_max_output_bytes = 4096
        settings.loop_sandbox_workdir = ""

    def _run(self, command: list[str], *, email: str | None = None, **kwargs):
        return self.post(
            f"/api/v2/loops/{self.loop_id}/sandbox-runs",
            email=email,
            json={"command": command, **kwargs},
        )

    def _receipt(self, resp) -> dict:
        self.assertEqual(resp.status_code, 201, resp.text)
        return resp.json()

    # -- 1. 开关 ---------------------------------------------------------
    def test_status_reports_fail_closed_by_default(self) -> None:
        settings.loop_sandbox_enabled = False
        settings.loop_sandbox_allowed_commands = ""
        resp = self.get("/api/v2/sandbox")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertFalse(body["enabled"])
        self.assertEqual(body["allowed_commands"], [])
        self.assertIn("沙箱未开启", body["note"])

    def test_allowed_status_lists_whitelist_and_limits(self) -> None:
        self._enable(allowed="python,node")
        body = self.get("/api/v2/sandbox").json()
        self.assertTrue(body["enabled"])
        self.assertEqual(body["allowed_commands"], ["python", "node"])
        self.assertEqual(body["timeout_seconds"], 8)
        self.assertEqual(body["max_output_bytes"], 4096)
        self.assertIn("进程隔离", body["note"])

    def test_post_when_disabled_is_503(self) -> None:
        settings.loop_sandbox_enabled = False
        resp = self._run([_PY, "--version"])
        self.assertEqual(resp.status_code, 503, resp.text)
        self.assertEqual(resp.json()["error"]["code"], "SANDBOX_DISABLED")

    def test_enabled_without_whitelist_still_refuses(self) -> None:
        settings.loop_sandbox_enabled = True
        settings.loop_sandbox_allowed_commands = ""
        resp = self._run([_PY, "--version"])
        self.assertEqual(resp.status_code, 503, resp.text)
        self.assertEqual(resp.json()["error"]["code"], "SANDBOX_DISABLED")

    # -- 2. 白名单 -------------------------------------------------------
    def test_command_outside_whitelist_is_422(self) -> None:
        self._enable(allowed="python")
        resp = self._run(["cmd.exe", "/c", "echo", "hi"])
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertEqual(resp.json()["error"]["code"], "SANDBOX_COMMAND_NOT_ALLOWED")

    def test_shell_metacharacters_are_422(self) -> None:
        self._enable(allowed="python")
        for bad in (
            [_PY, "-c", "print(1); print(2)"],
            [_PY, "-c", "print(1) && echo hi"],
            [_PY, "-c", "print(`whoami`)"],
            [_PY, "-c", "print($(whoami))"],
        ):
            resp = self._run(bad)
            self.assertEqual(resp.status_code, 422, (bad, resp.text))
            self.assertEqual(
                resp.json()["error"]["code"], "SANDBOX_COMMAND_NOT_ALLOWED"
            )

    def test_empty_or_too_many_args_are_422(self) -> None:
        self._enable(allowed="python")
        resp = self._run([])
        self.assertEqual(resp.status_code, 422, resp.text)
        resp = self._run([_PY] + [f"-{i}" for i in range(13)])
        self.assertEqual(resp.status_code, 422, resp.text)

    # -- 3/4/5/6 执行语义 ---------------------------------------------
    def test_success_records_passed_and_artifact_is_listed(self) -> None:
        self._enable()
        receipt = self._receipt(self._run([_PY, "--version"]))
        self.assertEqual(receipt["status"], C.SandboxStatus.passed.value)
        self.assertEqual(receipt["exit_code"], 0)
        self.assertGreaterEqual(receipt["duration_ms"], 0)
        self.assertIn("python", receipt["command_display"].lower())
        self.assertTrue(receipt["id"])

        page = self.get(f"/api/v2/loops/{self.loop_id}/sandbox-runs")
        self.assertEqual(page.status_code, 200, page.text)
        items = page.json()["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], receipt["id"])

        with database.session_scope() as db:
            artifacts = [
                row
                for row in loop_repo.list_artifacts(db, loop_id=self.loop_id)
                if row.kind == C.LoopArtifactKind.sandbox_run.value
            ]
            self.assertEqual(len(artifacts), 1)
            payload = json.loads(artifacts[0].payload_json)
            self.assertEqual(payload["status"], "passed")
            self.assertEqual(payload["command"][0], _PY)

    def test_nonzero_exit_is_failed_with_stderr_tail(self) -> None:
        self._enable()
        receipt = self._receipt(self._run([_PY, "-m", "definitely_not_a_module_xyz"]))
        self.assertEqual(receipt["status"], C.SandboxStatus.failed.value)
        self.assertNotEqual(receipt["exit_code"], 0)
        self.assertIn("definitely_not_a_module_xyz", receipt["stderr_tail"])

    def test_timeout_is_recorded_not_raised(self) -> None:
        self._enable()
        receipt = self._receipt(
            self._run([_PY, "-c", "import time\ntime.sleep(6)"], timeout_seconds=1)
        )
        self.assertEqual(receipt["status"], C.SandboxStatus.timeout.value)
        self.assertIsNone(receipt["exit_code"])
        self.assertEqual(receipt["timeout_seconds"], 1)
        self.assertGreaterEqual(receipt["duration_ms"], 900)

    def test_output_is_truncated_to_configured_limit(self) -> None:
        self._enable()
        settings.loop_sandbox_max_output_bytes = 512
        receipt = self._receipt(self._run([_PY, "-c", "print('x' * 20000)"]))
        self.assertTrue(receipt["truncated"])
        self.assertLessEqual(len(receipt["stdout_tail"]), 512)
        self.assertTrue(receipt["stdout_tail"].strip())

    def test_appends_multiple_runs_without_overwriting(self) -> None:
        self._enable()
        first = self._receipt(self._run([_PY, "--version"]))
        second = self._receipt(self._run([_PY, "-m", "definitely_not_a_module_xyz"]))
        self.assertNotEqual(first["id"], second["id"])
        items = self.get(f"/api/v2/loops/{self.loop_id}/sandbox-runs").json()["items"]
        self.assertEqual({item["id"] for item in items}, {first["id"], second["id"]})

    # -- 7/8/9 归属、环境隔离、清理 ------------------------------------
    def test_other_owner_is_403(self) -> None:
        self._enable()
        stranger = f"stranger-{uuid.uuid4().hex[:8]}@example.com"
        resp = self._run([_PY, "--version"], email=stranger)
        self.assertEqual(resp.status_code, 403, resp.text)
        self.assertEqual(resp.json()["error"]["code"], "FORBIDDEN")

    def test_child_process_cannot_see_api_keys(self) -> None:
        self._enable()
        probe = "import json,os\nprint(json.dumps({k: k in os.environ for k in "
        probe += "['LLM_API_KEY','SMTP_PASSWORD','AUTH_SECRET_KEY']}))"
        receipt = self._receipt(self._run([_PY, "-c", probe]))
        seen = json.loads(receipt["stdout_tail"].strip())
        self.assertEqual(seen, {"LLM_API_KEY": False, "SMTP_PASSWORD": False, "AUTH_SECRET_KEY": False})

    def test_child_env_keeps_utf8_settings(self) -> None:
        self._enable()
        probe = "import os\nprint(os.environ.get('PYTHONIOENCODING'), os.environ.get('PYTHONUTF8'))"
        receipt = self._receipt(self._run([_PY, "-c", probe]))
        self.assertEqual(receipt["stdout_tail"].strip(), "utf-8 1")

    def test_temp_workdir_is_removed(self) -> None:
        self._enable()
        probe = "import os\nprint(os.getcwd())"
        receipt = self._receipt(self._run([_PY, "-c", probe]))
        workdir = receipt["stdout_tail"].strip()
        self.assertIn("loop-sandbox-", workdir)
        self.assertFalse(os.path.exists(workdir), "运行结束后工作目录应被清理")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
