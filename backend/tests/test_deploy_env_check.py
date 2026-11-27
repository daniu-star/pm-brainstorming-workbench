"""部署环境自检脚本（第 44 批）：``scripts/check_deploy_environment.py``。

覆盖：认证密钥长度与旧名回退、数据库 URL 解析与端口探测、CORS 通配符、
邮件通道（代理 / SMTP 可达性）、LLM 模式、存储目录可写、退出码与文本输出。
所有网络探测都用注入的假探针，不真的连外网。
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from tests import _TEST_ROOT

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_deploy_environment.py"
_spec = importlib.util.spec_from_file_location("check_deploy_environment", _SCRIPT)
assert _spec and _spec.loader
deploy = importlib.util.module_from_spec(_spec)
# 必须先注册进 sys.modules：脚本用了 `from __future__ import annotations` + @dataclass，
# dataclasses 解析注解时会用 cls.__module__ 去 sys.modules 里取模块字典。
sys.modules[_spec.name] = deploy
_spec.loader.exec_module(deploy)


def _reachable(_host: str, _port: int, _timeout: float) -> bool:
    return True


def _unreachable(_host: str, _port: int, _timeout: float) -> bool:
    return False


def _base_env(**overrides: str) -> dict[str, str]:
    env = {
        "AUTH_SECRET_KEY": "x" * 40,
        "SESSION_DATA_DIR": str(_TEST_ROOT / f"deployenv-{uuid.uuid4().hex[:8]}"),
    }
    env.update(overrides)
    return env


class AuthCheckTests(unittest.TestCase):
    def test_missing_secret_fails(self) -> None:
        check = deploy.check_auth({})
        self.assertEqual(check.level, deploy.FAIL)
        self.assertIn("AUTH_SECRET_KEY", check.detail)

    def test_short_secret_fails_with_length(self) -> None:
        check = deploy.check_auth({"AUTH_SECRET_KEY": "short"})
        self.assertEqual(check.level, deploy.FAIL)
        self.assertIn("5 字符", check.detail)

    def test_legacy_jwt_secret_is_accepted(self) -> None:
        check = deploy.check_auth({"JWT_SECRET": "y" * 48})
        self.assertEqual(check.level, deploy.OK)
        self.assertIn("JWT_SECRET", check.detail)


class DatabaseCheckTests(unittest.TestCase):
    def test_missing_url_warns_about_ephemeral_storage(self) -> None:
        check = deploy.check_database({}, tcp=_reachable)
        self.assertEqual(check.level, deploy.WARN)
        self.assertIn("重启", check.hint)

    def test_postgres_unreachable_fails(self) -> None:
        check = deploy.check_database(
            {"DATABASE_URL": "postgresql://u:p@db.example.com:5432/app"}, tcp=_unreachable
        )
        self.assertEqual(check.level, deploy.FAIL)
        self.assertIn("db.example.com:5432", check.detail)

    def test_postgres_reachable_is_ok(self) -> None:
        check = deploy.check_database(
            {"DATABASE_URL": "postgresql://u:p@db.example.com:5432/app"}, tcp=_reachable
        )
        self.assertEqual(check.level, deploy.OK)
        self.assertIn("未校验凭据", check.detail)

    def test_sqlite_url_is_ok(self) -> None:
        check = deploy.check_database({"DATABASE_URL": "sqlite+pysqlite:///app.db"}, tcp=_unreachable)
        self.assertEqual(check.level, deploy.OK)


class CorsCheckTests(unittest.TestCase):
    def test_wildcard_fails(self) -> None:
        check = deploy.check_cors({"CORS_ORIGINS": "*"})
        self.assertEqual(check.level, deploy.FAIL)
        self.assertIn("通配符", check.detail)

    def test_wildcard_among_origins_fails(self) -> None:
        check = deploy.check_cors({"CORS_ORIGINS": "https://a.example, *"})
        self.assertEqual(check.level, deploy.FAIL)

    def test_explicit_origins_ok(self) -> None:
        check = deploy.check_cors({"CORS_ORIGINS": "https://a.example,https://b.example"})
        self.assertEqual(check.level, deploy.OK)
        self.assertIn("2 个来源", check.detail)

    def test_empty_uses_builtin_allowlist(self) -> None:
        self.assertEqual(deploy.check_cors({}).level, deploy.OK)


class EmailCheckTests(unittest.TestCase):
    def test_proxy_configured_is_ok(self) -> None:
        check = deploy.check_email(
            {"EMAIL_PROXY_URL": "https://mail-proxy.example.com/send"}, tcp=_unreachable
        )
        self.assertEqual(check.level, deploy.OK)
        self.assertIn("mail-proxy.example.com", check.detail)

    def test_missing_channel_warns(self) -> None:
        check = deploy.check_email({}, tcp=_reachable)
        self.assertEqual(check.level, deploy.WARN)
        self.assertIn("日志", check.detail)

    def test_smtp_unreachable_fails(self) -> None:
        check = deploy.check_email(
            {"SMTP_HOST": "smtp.163.com", "SMTP_FROM": "a@b.com", "SMTP_PORT": "465"},
            tcp=_unreachable,
        )
        self.assertEqual(check.level, deploy.FAIL)
        self.assertIn("smtp.163.com:465", check.detail)

    def test_smtp_reachable_ok(self) -> None:
        check = deploy.check_email(
            {"SMTP_HOST": "smtp.163.com", "SMTP_FROM_EMAIL": "a@b.com"}, tcp=_reachable
        )
        self.assertEqual(check.level, deploy.OK)


class LlmCheckTests(unittest.TestCase):
    def test_byok_is_ok(self) -> None:
        self.assertEqual(deploy.check_llm({}).level, deploy.OK)

    def test_system_mode_without_key_fails(self) -> None:
        check = deploy.check_llm({"LLM_KEY_MODE": "system"})
        self.assertEqual(check.level, deploy.FAIL)
        self.assertIn("428", check.detail)

    def test_system_mode_with_key_ok(self) -> None:
        check = deploy.check_llm({"LLM_KEY_MODE": "system", "LLM_API_KEY": "sk-x", "LLM_MODEL": "deepseek-flash"})
        self.assertEqual(check.level, deploy.OK)
        self.assertIn("deepseek-flash", check.detail)


class StorageCheckTests(unittest.TestCase):
    def test_directory_is_created_and_writable(self) -> None:
        env = _base_env()
        check = deploy.check_storage(env)
        self.assertEqual(check.level, deploy.OK)
        self.assertTrue(Path(env["SESSION_DATA_DIR"]).is_dir())

    def test_missing_setting_warns(self) -> None:
        self.assertEqual(deploy.check_storage({}).level, deploy.WARN)


class OverallTests(unittest.TestCase):
    def test_inspect_runs_in_fixed_order(self) -> None:
        checks = deploy.inspect_environment(_base_env(), tcp=_reachable)
        self.assertEqual([c.name for c in checks], list(deploy.CHECK_ORDER))

    def test_exit_code_reflects_failures(self) -> None:
        failing = deploy.inspect_environment({}, tcp=_unreachable)
        self.assertEqual(deploy.overall_exit_code(failing), 1)
        passing = deploy.inspect_environment(_base_env(), tcp=_reachable)
        self.assertEqual(deploy.overall_exit_code(passing), 0)

    def test_render_text_lists_hints(self) -> None:
        text = deploy.render_text(deploy.inspect_environment({}, tcp=_unreachable))
        self.assertIn("部署环境自检", text)
        self.assertIn("AUTH_SECRET_KEY", text)
        self.assertIn("→", text)
        self.assertIn("项必须修复", text)

    def test_json_output_is_machine_readable(self) -> None:
        env = _base_env()
        buffer = io.StringIO()
        with mock.patch.dict("os.environ", env, clear=True), redirect_stdout(buffer):
            code = deploy.main(["--json", "--skip-network"])
        self.assertEqual(code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertTrue(payload["ok"])
        names = [item["name"] for item in payload["checks"]]
        self.assertEqual(names, list(deploy.CHECK_ORDER))

    def test_env_file_is_read_and_overridden_by_process_env(self) -> None:
        env_path = _TEST_ROOT / f"deploy-{uuid.uuid4().hex[:8]}.env"
        env_path.write_text(
            "# comment\nAUTH_SECRET_KEY=from-file-secret-0123456789abcdef\nEMPTY=\n",
            encoding="utf-8",
        )
        try:
            merged = deploy.merged_env(env_path, environ={"AUTH_SECRET_KEY": "from-env"})
            self.assertEqual(merged["AUTH_SECRET_KEY"], "from-env")
            self.assertEqual(merged["EMPTY"], "")
            file_only = deploy.merged_env(env_path, environ={})
            self.assertTrue(file_only["AUTH_SECRET_KEY"].startswith("from-file"))
        finally:
            env_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
