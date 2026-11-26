"""第 42 批（后端 P1）回归：

线上评测记录里三条「看似正常、实际不可用」的问题，各自补一条可复跑的断言：

1. ``container_start.py`` 起 uvicorn 时必须开 ``--proxy-headers``：HF Space 在应用前面
   还有一层代理，不开的话 ``request.client.host`` 永远是代理地址，登录限流退化成
   「全站共享一个额度」。
2. 登录发码除了按 IP 限流，还要按邮箱限流（双桶），否则代理化后单 IP 放大成全局。
3. ``/health`` 恒 200 只说明「配置存在」；``/health/ready`` 必须真正探一次数据库，
   库挂了返回 503 + ``blocking_reasons``。
"""
from __future__ import annotations

import pathlib
import os
import sys
import unittest
import uuid
from unittest import mock

BACKEND = pathlib.Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND.parent
if str(BACKEND) not in sys.path:  # pragma: no cover
    sys.path.insert(0, str(BACKEND))

import main  # noqa: E402
from core import auth as auth_module  # noqa: E402
from tests.test_api_v2 import V2ApiTestBase  # noqa: E402

CONTAINER_START = REPO_ROOT / "scripts" / "container_start.py"


class ProxyHeaderContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = CONTAINER_START.read_text(encoding="utf-8")

    def test_uvicorn_gets_proxy_headers_and_forwarded_allow_ips(self) -> None:
        self.assertIn('"--proxy-headers"', self.text)
        self.assertIn('"--forwarded-allow-ips"', self.text)
        # 默认信任任意上游（容器平台代理地址不固定），但必须可配置。
        self.assertIn("FORWARDED_ALLOW_IPS", self.text)

    def test_forwarded_allow_ips_is_configurable_via_cli(self) -> None:
        self.assertIn('"--forwarded-allow-ips",', self.text)
        self.assertIn("args.forwarded_allow_ips", self.text)


class EmailRateLimitBucketTests(V2ApiTestBase):
    """邮箱维度限流：同一邮箱第 6 次被挡，换邮箱仍放行（IP 桶未打满）。"""

    def _fresh(self) -> tuple[str, str]:
        # 每次用独立 IP + 邮箱，避免和别的用例（或上一次跑）共享 rate_limits 窗口。
        # IP 段必须真正随机：旧实现只有 256 个候选，多跑几轮就会撞上历史计数。
        token = uuid.uuid4().hex[:12]
        ip = f"10.{int(token[0:2], 16)}.{int(token[2:4], 16)}.{int(token[4:6], 16)}"
        return ip, f"rate-{token}@example.com"

    def test_sixth_send_for_same_email_is_rejected(self) -> None:
        ip, email = self._fresh()
        for _ in range(auth_module.EMAIL_SEND_LIMIT_PER_HOUR):
            auth_module.check_send_rate_limit(ip, email)
        with self.assertRaises(auth_module.AuthRateLimitError) as ctx:
            auth_module.check_send_rate_limit(ip, email)
        self.assertIn("邮箱", str(ctx.exception))

    def test_other_email_on_same_ip_still_allowed(self) -> None:
        ip, email = self._fresh()
        for _ in range(auth_module.EMAIL_SEND_LIMIT_PER_HOUR):
            auth_module.check_send_rate_limit(ip, email)
        # 同一 IP 换一个邮箱：邮箱桶是新 key，IP 桶还有额度。
        auth_module.check_send_rate_limit(ip, f"other-{uuid.uuid4().hex[:8]}@example.com")

    def test_email_bucket_constants_are_tighter_than_ip_bucket(self) -> None:
        self.assertGreaterEqual(auth_module.IP_SEND_LIMIT_PER_HOUR, auth_module.EMAIL_SEND_LIMIT_PER_HOUR)
        self.assertGreater(auth_module.EMAIL_SEND_LIMIT_PER_HOUR, 0)


class ReadinessProbeTests(V2ApiTestBase):
    def test_live_is_200_without_touching_database(self) -> None:
        with mock.patch.object(main, "_database_probe", side_effect=AssertionError("不该探测数据库")):
            resp = self.client.get("/health/live")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "ok")

    def test_ready_is_ok_when_database_reachable(self) -> None:
        resp = self.client.get("/health/ready")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["blocking_reasons"], [])

    def test_ready_returns_503_with_reason_when_database_is_down(self) -> None:
        with mock.patch.object(main, "_database_probe", return_value=(False, "数据库不可用：OperationalError")):
            resp = self.client.get("/health/ready")
        self.assertEqual(resp.status_code, 503, resp.text)
        body = resp.json()
        self.assertEqual(body["status"], "degraded")
        self.assertIn("数据库不可用：OperationalError", body["blocking_reasons"])

    def test_legacy_health_stays_200_for_keep_warm(self) -> None:
        # keep-warm 工作流与老反代只认 /health，语义不能变。
        self.assertEqual(self.client.get("/health").status_code, 200)


class CorsPolicyTests(V2ApiTestBase):
    """CORS 白名单不能出现通配符（第 42 批评测记录 D=85 的单点绕过）。"""

    def test_parse_drops_wildcard_and_keeps_allowlist(self) -> None:
        from core.config import _parse_cors_origins

        parsed = _parse_cors_origins("https://evil.example,*")
        self.assertEqual(parsed, ["https://evil.example"])
        self.assertNotIn("*", parsed)

    def test_wildcard_only_falls_back_to_known_origins(self) -> None:
        from core.config import _parse_cors_origins

        parsed = _parse_cors_origins("*")
        self.assertNotIn("*", parsed)
        self.assertIn("https://www.brainstorming.top", parsed)
        self.assertTrue(any("localhost" in o for o in parsed))

    def test_unknown_origin_gets_no_cors_headers(self) -> None:
        resp = self.client.get("/health", headers={"Origin": "https://evil.example"})
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("access-control-allow-origin", {k.lower() for k in resp.headers})

    def test_allowlisted_origin_gets_cors_headers(self) -> None:
        resp = self.client.get("/health", headers={"Origin": "http://localhost:3001"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("access-control-allow-origin"), "http://localhost:3001")


class PersistenceWarningTests(V2ApiTestBase):
    """线上 P0-2：无持久卷时不能让数据丢失这件事「静默发生」。"""

    def test_space_without_database_url_warns_about_ephemeral_sqlite(self) -> None:
        with mock.patch.dict("os.environ", {"SPACE_ID": "demo/space", "DATABASE_URL": ""}, clear=False):
            warnings = main._persistence_warnings()
            ready = self.client.get("/health/ready").json()
            health = self.client.get("/health").json()
        self.assertTrue(any("数据会丢失" in w for w in warnings), warnings)
        self.assertTrue(any("数据会丢失" in w for w in ready["warnings"]), ready)
        self.assertTrue(any("数据会丢失" in w for w in health.get("warnings", [])), health)

    def test_external_database_has_no_warning(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {"SPACE_ID": "demo/space", "DATABASE_URL": "postgresql://u:p@db/app"},
            clear=False,
        ):
            self.assertEqual(main._persistence_warnings(), [])

    def test_local_runs_stay_warning_free(self) -> None:
        env = dict(os.environ)
        env.pop("SPACE_ID", None)
        env.pop("SPACE_HOST", None)
        env["DATABASE_URL"] = ""
        with mock.patch.dict("os.environ", env, clear=True):
            self.assertEqual(main._persistence_warnings(), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
