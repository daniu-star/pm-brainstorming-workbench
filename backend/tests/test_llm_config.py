"""账户自带模型密钥（第 33 批，BYOK）。

覆盖四件事：
1. 密钥加密/脱敏：库里只存密文，视图只回末 4 位；
2. 闸门：同步命令放行，需要模型的命令未配置时抛 428 ``LLM_CONFIG_REQUIRED``；
3. 接口：``GET/PUT/DELETE /api/v2/llm-config`` + ``POST /test``；
4. 执行器兜底：Worker 跑 Run 时账户未配置 Key，Run 直接以可操作错误失败。
"""
from __future__ import annotations

import asyncio
import shutil
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

from core import crypto
from core.config import settings
from core.crypto import SecretDecodeError
from domain import contracts as C
from domain.errors import AppError, ErrorCode
from repositories import database, llm_config_repo, run_repo, session_repo
from services import llm_config_service as svc
from tests.test_api_v2 import V2ApiTestBase
from workflows import runner

BACKEND = Path(__file__).resolve().parent.parent
PLAIN_KEY = "sk-test-abcdef1234"


class CryptoSecretTests(unittest.TestCase):
    def test_roundtrip_and_mask(self) -> None:
        token = crypto.encrypt_secret(PLAIN_KEY)
        self.assertNotIn(PLAIN_KEY, token)
        self.assertEqual(crypto.decrypt_secret(token), PLAIN_KEY)
        self.assertEqual(crypto.mask_secret(PLAIN_KEY), "****1234")
        self.assertEqual(crypto.mask_secret("ab"), "****")

    def test_tampered_token_raises_decode_error(self) -> None:
        token = crypto.encrypt_secret(PLAIN_KEY)
        with self.assertRaises(SecretDecodeError):
            crypto.decrypt_secret(token[:-4] + "AAAA")
        with self.assertRaises(SecretDecodeError):
            crypto.decrypt_secret("not-a-token")


class _ByokCase(unittest.TestCase):
    """临时库 + 一个会话，并把密钥模式固定为 ``byok``。"""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="byok_", dir=str(BACKEND / ".tmp_tests"))
        database.configure(
            f"sqlite+pysqlite:///{(Path(self._tmpdir) / 'byok.db').as_posix()}", force=True
        )
        database.initialize_database(drop_first=True)
        self.owner = f"byok-{uuid.uuid4().hex[:8]}@example.com"
        with database.session_scope() as db:
            session = session_repo.create_session(
                db, owner_email=self.owner, problem_statement="BYOK 测试会话"
            )
            self.session_id = session.id
        original = settings.llm_key_mode
        settings.llm_key_mode = "byok"
        self.addCleanup(setattr, settings, "llm_key_mode", original)

    def tearDown(self) -> None:
        database.dispose_engine()
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _payload(self, key: str = PLAIN_KEY) -> C.LlmConfigPayload:
        return C.LlmConfigPayload(
            base_url="https://api.example.com/v1/", model="demo-model", api_key=key
        )


class LlmConfigServiceTests(_ByokCase):
    def test_save_stores_ciphertext_and_view_masks_key(self) -> None:
        with database.session_scope() as db:
            self.assertTrue(svc.view(db, self.owner)["requires_config"])
            view = svc.save(db, self.owner, self._payload())
            self.assertFalse(view["requires_config"])
            self.assertTrue(view["configured"])
            self.assertEqual(view["key_hint"], "****1234")
            self.assertEqual(view["base_url"], "https://api.example.com/v1")
            row = llm_config_repo.get_config(db, self.owner)
            self.assertIsNotNone(row)
            assert row is not None
            self.assertNotIn(PLAIN_KEY, row.api_key_enc)
            self.assertEqual(crypto.decrypt_secret(row.api_key_enc), PLAIN_KEY)
            self.assertTrue(svc.is_configured(db, self.owner))
            self.assertEqual(svc.client_kwargs(db, self.owner)["api_key"], PLAIN_KEY)

    def test_gate_allows_sync_actions_and_rejects_model_actions(self) -> None:
        with database.session_scope() as db:
            svc.gate(db, self.owner, action=C.Action.revise_brief.value)
            svc.gate(db, self.owner, action=C.Action.add_evidence.value)
            with self.assertRaises(AppError) as caught:
                svc.gate(db, self.owner, action=C.Action.clarify.value)
            self.assertEqual(caught.exception.code, ErrorCode.LLM_CONFIG_REQUIRED)
            self.assertIn("模型 API", str(caught.exception))

            svc.save(db, self.owner, self._payload())
            svc.gate(db, self.owner, action=C.Action.clarify.value)

    def test_system_mode_needs_no_per_account_config(self) -> None:
        settings.llm_key_mode = "system"
        original_key = settings.llm_api_key
        settings.llm_api_key = "sk-system-shared"
        self.addCleanup(setattr, settings, "llm_api_key", original_key)
        with database.session_scope() as db:
            svc.gate(db, self.owner, action=C.Action.clarify.value)
            self.assertEqual(svc.client_kwargs(db, self.owner), {})
            self.assertFalse(svc.view(db, self.owner)["requires_config"])

    def test_test_connection_success_and_failure(self) -> None:
        with database.session_scope() as db:
            svc.save(db, self.owner, self._payload())

            class _Ok:
                async def complete(self, **kwargs):
                    class _Result:
                        text = "ok"
                        model = "demo-model"

                    return _Result()

            with mock.patch.object(svc, "build_client", return_value=_Ok()):
                ok = asyncio.run(svc.test_connection(db, self.owner))
            self.assertTrue(ok["ok"])
            self.assertEqual(ok["reply"], "ok")
            row = llm_config_repo.get_config(db, self.owner)
            assert row is not None
            self.assertIsNotNone(row.last_verified_at)

            class _Bad:
                async def complete(self, **kwargs):
                    raise RuntimeError("上游 401：Key 无效")

            with mock.patch.object(svc, "build_client", return_value=_Bad()):
                bad = asyncio.run(svc.test_connection(db, self.owner))
            self.assertFalse(bad["ok"])
            self.assertIn("401", bad["error"])
            row = llm_config_repo.get_config(db, self.owner)
            assert row is not None
            self.assertIn("401", str(row.last_error))


class LlmConfigApiTests(V2ApiTestBase):
    def setUp(self) -> None:
        super().setUp()
        original = settings.llm_key_mode
        settings.llm_key_mode = "byok"
        self.addCleanup(setattr, settings, "llm_key_mode", original)

    def test_get_put_delete_flow_never_leaks_key(self) -> None:
        first = self.get("/api/v2/llm-config")
        self.assertEqual(first.status_code, 200)
        self.assertTrue(first.json()["requires_config"])

        saved = self.client.put(
            "/api/v2/llm-config",
            headers=self.headers(),
            json={
                "base_url": "https://api.example.com/v1",
                "model": "demo-model",
                "api_key": PLAIN_KEY,
            },
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertNotIn(PLAIN_KEY, saved.text)
        self.assertEqual(saved.json()["key_hint"], "****1234")
        self.assertFalse(saved.json()["requires_config"])

        again = self.get("/api/v2/llm-config")
        self.assertTrue(again.json()["configured"])
        self.assertNotIn(PLAIN_KEY, again.text)

        removed = self.client.delete("/api/v2/llm-config", headers=self.headers())
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertTrue(removed.json()["requires_config"])

    def test_save_rejects_non_http_base_url(self) -> None:
        bad = self.client.put(
            "/api/v2/llm-config",
            headers=self.headers(),
            json={"base_url": "ftp://bad", "model": "m", "api_key": PLAIN_KEY},
        )
        self.assertEqual(bad.status_code, 422)

    def test_test_endpoint_reports_upstream_error(self) -> None:
        self.client.put(
            "/api/v2/llm-config",
            headers=self.headers(),
            json={
                "base_url": "https://api.example.com/v1",
                "model": "demo-model",
                "api_key": PLAIN_KEY,
            },
        )

        class _Bad:
            async def complete(self, **kwargs):
                raise RuntimeError("上游 402：余额不足")

        with mock.patch.object(svc, "build_client", return_value=_Bad()):
            resp = self.post("/api/v2/llm-config/test")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertFalse(resp.json()["ok"])
        self.assertIn("402", resp.json()["error"])


class RunnerByokGateTests(_ByokCase):
    def _claim_run(self) -> str:
        with database.session_scope() as db:
            now = time.time()
            run = run_repo.create_run(
                db,
                session_id=self.session_id,
                owner_email=self.owner,
                request_id=f"byok-{uuid.uuid4().hex[:12]}",
                action=C.Action.clarify.value,
                input_hash="byok-hash",
                base_session_version=1,
                base_product_version=1,
                deadline_at=int(now) + 300,
                queue_deadline_at=int(now) + 600,
            )
        with database.session_scope() as db:
            claimed = run_repo.claim_next_run(
                db, worker_id="byok-worker", now_epoch=int(time.time())
            )
        self.assertIsNotNone(claimed)
        return run.id

    def test_unconfigured_run_fails_with_actionable_code(self) -> None:
        run_id = self._claim_run()
        built: list[dict] = []

        def _record(**kwargs):
            built.append(kwargs)
            raise RuntimeError("不应该构造客户端")

        with mock.patch.object(runner, "LLMClient", side_effect=_record):
            runner.execute_run(run_id, worker_id="byok-worker", renew_thread=False)

        self.assertEqual(built, [])
        with database.session_scope() as db:
            row = run_repo.get_run(db, run_id)
            assert row is not None
            self.assertEqual(row.status, C.RunStatus.failed.value)
            self.assertEqual(row.error_code, ErrorCode.LLM_CONFIG_REQUIRED)
            self.assertIn("模型 API", str(row.error_message))

    def test_configured_run_uses_account_client_kwargs(self) -> None:
        run_id = self._claim_run()
        with database.session_scope() as db:
            svc.save(db, self.owner, self._payload())

        captured: list[dict] = []

        class _Stop(Exception):
            pass

        def _factory(**kwargs):
            captured.append(kwargs)
            raise _Stop("stop after client construction")

        with mock.patch.object(runner, "LLMClient", side_effect=_factory):
            # 构造客户端后立刻抛错以中止执行：本用例只验证「用了账户的 Key」。
            with self.assertRaises(_Stop):
                runner.execute_run(run_id, worker_id="byok-worker", renew_thread=False)

        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["api_key"], PLAIN_KEY)
        self.assertEqual(captured[0]["model"], "demo-model")
        self.assertEqual(captured[0]["base_url"], "https://api.example.com/v1")
