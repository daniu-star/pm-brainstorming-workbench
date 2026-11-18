"""``core.llm_client`` 单元测试（真实运行，不依赖网络）。

覆盖：
* 原生 usage 优先 / 缺失时 ``usage_source="estimated"`` 的保守估算；
* SDK 内部重试必须为 0（PRD FR-08）；
* 错误映射：429 → 限流（可重试）、超时/5xx → 不可用（可重试）、
  400/401 → 请求错误（不可重试）；
* ``json_schema`` 不被支持时降级为 ``json_object``，两次调用都计入 attempts；
* ``complete_json`` 返回 ``(parsed, LLMResult)``，非法 JSON 抛可重试的
  ``LLMOutputInvalidError``；
* 旧接口 ``llm_complete`` / ``llm_stream`` 保持可用。
"""
from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import llm_client as llm  # noqa: E402
from core.llm_client import (  # noqa: E402
    LLMClient,
    LLMOutputInvalidError,
    LLMRateLimitedError,
    LLMRequestError,
    LLMUnavailableError,
)


# --------------------------------------------------------------------------- #
# 假的 OpenAI SDK
# --------------------------------------------------------------------------- #


class FakeStatusError(Exception):
    """模拟 openai.APIStatusError（有 status_code 即可走映射逻辑）。"""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class _FakeCompletions:
    def __init__(self, results: list[object]) -> None:
        self.results = list(results)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.results:
            raise AssertionError("假的 SDK 没有更多预设响应")
        item = self.results.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class _FakeChat:
    def __init__(self, completions: _FakeCompletions) -> None:
        self.completions = completions


class FakeSDK:
    def __init__(self, *results: object) -> None:
        self.completions = _FakeCompletions(list(results))
        self.chat = _FakeChat(self.completions)

    @property
    def calls(self) -> list[dict]:
        return self.completions.calls


def make_response(
    content: str = "hello",
    *,
    model: str = "fake-model",
    with_usage: bool = True,
    finish_reason: str = "stop",
):
    usage = (
        SimpleNamespace(prompt_tokens=12, completion_tokens=6, total_tokens=18)
        if with_usage
        else None
    )
    return SimpleNamespace(
        model=model,
        usage=usage,
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason=finish_reason,
            )
        ],
    )


class _AsyncIter:
    def __init__(self, items: list[object]) -> None:
        self._items = items

    def __aiter__(self):
        async def _gen():
            for item in self._items:
                yield item

        return _gen()


def _run(coro):
    return asyncio.run(coro)


MESSAGES = [{"role": "user", "content": "请分析这个问题"}]


# --------------------------------------------------------------------------- #
# 测试
# --------------------------------------------------------------------------- #


class LLMClientCompleteTests(unittest.TestCase):
    def test_provider_usage_priority_and_request_options(self) -> None:
        sdk = FakeSDK(make_response("文本结果"))
        client = LLMClient(sdk_client=sdk, model="fake-model")

        result = _run(
            client.complete(
                messages=MESSAGES,
                max_output_tokens=321,
                timeout_seconds=7.5,
                purpose="test.provider",
                temperature=0.1,
            )
        )

        self.assertEqual(result.text, "文本结果")
        self.assertEqual(result.usage.usage_source, "provider")
        self.assertEqual(result.usage.prompt_tokens, 12)
        self.assertEqual(result.usage.completion_tokens, 6)
        self.assertEqual(result.usage.total_tokens, 18)
        self.assertEqual(result.attempts, 1)
        self.assertEqual(result.model, "fake-model")
        self.assertFalse(result.estimated_usage)

        call = sdk.calls[0]
        self.assertEqual(call["max_tokens"], 321)
        self.assertEqual(call["timeout"], 7.5)
        self.assertEqual(call["temperature"], 0.1)
        self.assertNotIn("response_format", call)

    def test_missing_usage_falls_back_to_estimated(self) -> None:
        sdk = FakeSDK(make_response("abcdef", with_usage=False))
        client = LLMClient(sdk_client=sdk, model="fake-model")

        result = _run(
            client.complete(
                messages=MESSAGES,
                max_output_tokens=100,
                timeout_seconds=3,
                purpose="test.estimated",
            )
        )

        self.assertEqual(result.usage.usage_source, "estimated")
        self.assertTrue(result.estimated_usage)
        # 保守估算：3 字符 ≈ 1 token
        self.assertEqual(result.usage.completion_tokens, len("abcdef") // 3)
        self.assertEqual(result.usage.prompt_tokens, len("请分析这个问题") // 3)
        self.assertEqual(
            result.usage.total_tokens,
            result.usage.prompt_tokens + result.usage.completion_tokens,
        )

    def test_rate_limit_is_retryable(self) -> None:
        sdk = FakeSDK(FakeStatusError(429))
        client = LLMClient(sdk_client=sdk, model="fake-model")

        with self.assertRaises(LLMRateLimitedError) as ctx:
            _run(
                client.complete(
                    messages=MESSAGES,
                    max_output_tokens=10,
                    timeout_seconds=3,
                    purpose="test.429",
                )
            )
        self.assertTrue(ctx.exception.retryable)
        self.assertEqual(ctx.exception.code, "LLM_RATE_LIMITED")
        self.assertEqual(len(sdk.calls), 1, "SDK 内部不得自动重试")

    def test_server_error_and_timeout_are_retryable_unavailable(self) -> None:
        for error in (FakeStatusError(503), TimeoutError("request timed out")):
            with self.subTest(error=type(error).__name__):
                client = LLMClient(sdk_client=FakeSDK(error), model="fake-model")
                with self.assertRaises(LLMUnavailableError) as ctx:
                    _run(
                        client.complete(
                            messages=MESSAGES,
                            max_output_tokens=10,
                            timeout_seconds=3,
                            purpose="test.unavailable",
                        )
                    )
                self.assertTrue(ctx.exception.retryable)
                self.assertEqual(ctx.exception.code, "LLM_UNAVAILABLE")

    def test_client_errors_are_not_retryable(self) -> None:
        for status in (400, 401, 404):
            with self.subTest(status=status):
                client = LLMClient(sdk_client=FakeSDK(FakeStatusError(status)), model="fake-model")
                with self.assertRaises(LLMRequestError) as ctx:
                    _run(
                        client.complete(
                            messages=MESSAGES,
                            max_output_tokens=10,
                            timeout_seconds=3,
                            purpose="test.4xx",
                        )
                    )
                self.assertFalse(ctx.exception.retryable)
                self.assertEqual(ctx.exception.code, "LLM_REQUEST_ERROR")


class LLMClientJsonTests(unittest.TestCase):
    def test_json_schema_downgrades_to_json_object(self) -> None:
        sdk = FakeSDK(FakeStatusError(400), make_response('{"answer": 42}'))
        client = LLMClient(sdk_client=sdk, model="fake-model")

        parsed, result = _run(
            client.complete_json(
                messages=MESSAGES,
                max_output_tokens=200,
                timeout_seconds=5,
                purpose="test.json",
                json_schema={"name": "answer", "schema": {"type": "object"}},
            )
        )

        self.assertEqual(parsed, {"answer": 42})
        self.assertEqual(result.attempts, 2)
        self.assertEqual(len(sdk.calls), 2)
        self.assertEqual(sdk.calls[0]["response_format"]["type"], "json_schema")
        self.assertEqual(sdk.calls[1]["response_format"], {"type": "json_object"})

    def test_bare_json_schema_is_wrapped_without_downgrade(self) -> None:
        """裸 schema 必须包成 {name, schema, strict}，第一次就成功、不浪费降级调用。"""
        bare_schema = {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
            "additionalProperties": False,
        }
        sdk = FakeSDK(make_response(json.dumps({"question": "你最近在忙什么？"})))
        client = LLMClient(sdk_client=sdk, model="fake-model")

        parsed, result = _run(
            client.complete_json(
                messages=MESSAGES,
                max_output_tokens=200,
                timeout_seconds=5,
                purpose="test.json.wrap",
                json_schema=bare_schema,
            )
        )

        self.assertEqual(parsed, {"question": "你最近在忙什么？"})
        self.assertEqual(result.attempts, 1, "合法包装不得触发 json_object 降级")
        self.assertEqual(len(sdk.calls), 1)
        self.assertEqual(
            sdk.calls[0]["response_format"],
            {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_output",
                    "schema": bare_schema,
                    "strict": True,
                },
            },
        )

    def test_prewrapped_json_schema_used_as_is(self) -> None:
        wrapped = {"name": "answer", "schema": {"type": "object"}, "strict": True}
        sdk = FakeSDK(make_response('{"answer": 1}'))
        client = LLMClient(sdk_client=sdk, model="fake-model")

        _run(
            client.complete_json(
                messages=MESSAGES,
                max_output_tokens=200,
                timeout_seconds=5,
                purpose="test.json.prewrapped",
                json_schema=wrapped,
            )
        )
        self.assertEqual(sdk.calls[0]["response_format"]["json_schema"], wrapped)

    def test_invalid_json_raises_retryable_output_error(self) -> None:
        sdk = FakeSDK(make_response("这不是 JSON"))
        client = LLMClient(sdk_client=sdk, model="fake-model")

        with self.assertRaises(LLMOutputInvalidError) as ctx:
            _run(
                client.complete_json(
                    messages=MESSAGES,
                    max_output_tokens=50,
                    timeout_seconds=3,
                    purpose="test.json.bad",
                )
            )
        self.assertTrue(ctx.exception.retryable)
        self.assertEqual(ctx.exception.code, "MODEL_OUTPUT_INVALID")

    def test_valid_json_without_schema(self) -> None:
        sdk = FakeSDK(make_response(json.dumps({"k": [1, 2, 3]})))
        client = LLMClient(sdk_client=sdk, model="fake-model")

        parsed, result = _run(
            client.complete_json(
                messages=MESSAGES,
                max_output_tokens=50,
                timeout_seconds=3,
                purpose="test.json.ok",
            )
        )
        self.assertEqual(parsed, {"k": [1, 2, 3]})
        self.assertEqual(result.attempts, 1)


class LLMClientLegacyTests(unittest.TestCase):
    def test_sdk_retries_disabled(self) -> None:
        self.assertEqual(llm.client.max_retries, 0)
        client = LLMClient(sdk_client=FakeSDK(make_response()), model="m")
        # 传入的 SDK 客户端被原样使用，且 client 不会开启内部重试。
        self.assertIsInstance(client.sdk_client, FakeSDK)

    def test_llm_complete_keeps_old_signature(self) -> None:
        sdk = FakeSDK(make_response("旧接口输出"))
        original = llm.default_client
        llm.default_client = LLMClient(sdk_client=sdk, model="fake-model")
        try:
            text = _run(llm.llm_complete(MESSAGES, temperature=0.3))
        finally:
            llm.default_client = original

        self.assertEqual(text, "旧接口输出")
        call = sdk.calls[0]
        self.assertNotIn("max_tokens", call, "旧接口不设置 max_tokens，保持旧行为")
        self.assertEqual(call["temperature"], 0.3)

    def test_llm_stream_keeps_old_signature(self) -> None:
        chunks = [
            SimpleNamespace(choices=[]),  # 心跳帧应被跳过
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="你"))]),
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="好"))]),
        ]

        class _StreamCompletions(_FakeCompletions):
            async def create(self, **kwargs):
                self.calls.append(kwargs)
                return _AsyncIter(chunks)

        sdk = FakeSDK()
        sdk.chat.completions = _StreamCompletions([])
        sdk.completions = sdk.chat.completions

        original = llm.default_client
        llm.default_client = LLMClient(sdk_client=sdk, model="fake-model")
        try:

            async def collect() -> list[str]:
                return [token async for token in llm.llm_stream(MESSAGES, temperature=0.7)]

            tokens = _run(collect())
        finally:
            llm.default_client = original

        self.assertEqual(tokens, ["你", "好"])


if __name__ == "__main__":
    unittest.main()
