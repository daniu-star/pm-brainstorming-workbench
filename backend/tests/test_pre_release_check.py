"""scripts/pre_release_check.py 的自检。

这些用例只验证「体检脚本自身的编排逻辑」，不重复跑它调用的那些真实步骤
（后端全量 / 前端 / 浏览器 e2e / 备份演练都在发版体检时真正执行一次）。
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import pathlib
import sys
import unittest

BACKEND = pathlib.Path(__file__).resolve().parent.parent
REPO = BACKEND.parent
SCRIPT = REPO / "scripts" / "pre_release_check.py"


def load_module():
    spec = importlib.util.spec_from_file_location("pre_release_check", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # dataclass 在解析注解时要能从 sys.modules 找到模块本身
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PreReleaseCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = load_module()

    def test_every_step_has_timeout_and_callable(self) -> None:
        self.assertEqual(list(self.mod.ALL_STEPS), list(self.mod.STEPS))
        for name in self.mod.ALL_STEPS:
            self.assertIn(name, self.mod.TIMEOUTS)
            self.assertTrue(callable(self.mod.STEPS[name]))
            self.assertGreater(self.mod.TIMEOUTS[name], 0)

    def test_list_prints_all_steps(self) -> None:
        # 捕获 stdout：否则 `unittest` 的 OK 行之后会混进 9 行步骤名（第 47 批）。
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            self.assertEqual(self.mod.main(["--list"]), 0)
        printed = buffer.getvalue()
        for name in self.mod.ALL_STEPS:
            self.assertIn(name, printed)

    def test_unknown_step_returns_two(self) -> None:
        self.assertEqual(self.mod.main(["--only", "docs,not-a-step"]), 2)

    def test_select_steps_filters_and_skips(self) -> None:
        wanted, error = self.mod.select_steps(only=None, skip="", skip_browser=False)
        self.assertIsNone(error)
        self.assertEqual(wanted, list(self.mod.ALL_STEPS))

        wanted, error = self.mod.select_steps(only="docs,backend-tests", skip="", skip_browser=False)
        self.assertIsNone(error)
        self.assertEqual(wanted, ["docs", "backend-tests"])

        wanted, error = self.mod.select_steps(only=None, skip="frontend-tests", skip_browser=True)
        self.assertIsNone(error)
        self.assertNotIn("frontend-tests", wanted)
        self.assertNotIn("browser-e2e", wanted)
        self.assertIn("backup-drill", wanted)

    def test_select_steps_reports_unknown_and_empty_selection(self) -> None:
        wanted, error = self.mod.select_steps(only="docs,nope", skip="", skip_browser=False)
        self.assertEqual(wanted, [])
        self.assertIsNotNone(error)
        self.assertIn("nope", error)

        wanted, error = self.mod.select_steps(only="docs", skip="docs", skip_browser=False)
        self.assertIsNone(error)
        self.assertEqual(wanted, [])

    def test_tail_keeps_last_non_empty_lines(self) -> None:
        text = "a\n\nb\n c \nd\n"
        self.assertEqual(self.mod._tail(text, lines=2), [" c", "d"])

    def test_serving_hint_flags_unreachable_api(self) -> None:
        original = self.mod._http_ok
        self.mod._http_ok = lambda url, timeout=5.0: False
        try:
            result = self.mod.step_browser_e2e()
        finally:
            self.mod._http_ok = original
        self.assertFalse(result.ok)
        self.assertIn("start.bat", result.detail)

    def test_deploy_env_step_passes_with_warnings_only(self) -> None:
        payload = {
            "ok": True,
            "checks": [
                {"name": "auth", "level": "ok", "detail": "ok", "hint": ""},
                {"name": "database", "level": "warn", "detail": "no url", "hint": "挂卷"},
            ],
        }
        original = self.mod._run
        self.mod._run = lambda *a, **k: (0, json.dumps(payload, ensure_ascii=False))
        try:
            result = self.mod.step_deploy_env()
        finally:
            self.mod._run = original
        self.assertTrue(result.ok)
        self.assertIn("2 项检查", result.detail)
        self.assertIn("database", result.detail)

    def test_deploy_env_step_fails_on_fail_level(self) -> None:
        payload = {
            "ok": False,
            "checks": [
                {"name": "auth", "level": "fail", "detail": "missing", "hint": "填 AUTH_SECRET_KEY"},
                {"name": "cors", "level": "fail", "detail": "wildcard", "hint": "写具体域名"},
            ],
        }
        original = self.mod._run
        self.mod._run = lambda *a, **k: (1, json.dumps(payload, ensure_ascii=False))
        try:
            result = self.mod.step_deploy_env()
        finally:
            self.mod._run = original
        self.assertFalse(result.ok)
        self.assertIn("必须修复 2", result.detail)
        self.assertIn("auth", result.detail)

    def test_deploy_env_step_survives_non_json_output(self) -> None:
        original = self.mod._run
        self.mod._run = lambda *a, **k: (0, "Traceback: boom")
        try:
            result = self.mod.step_deploy_env()
        finally:
            self.mod._run = original
        self.assertFalse(result.ok)
        self.assertEqual(result.detail, "输出不是 JSON")

    def test_deploy_env_is_scheduled_before_backend_tests(self) -> None:
        steps = list(self.mod.ALL_STEPS)
        self.assertIn("deploy-env", steps)
        self.assertLess(steps.index("deploy-env"), steps.index("backend-tests"))


if __name__ == "__main__":
    unittest.main()