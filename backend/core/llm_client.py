"""LLM 客户端封装：超时、重试分类、结构化输出与 usage（PRD FR-03 / FR-04C / FR-08）。

设计要点：
* **SDK 内部重试必须关闭**（``max_retries=0``）。上游重试由 ``workflows.runner``
  统一控制（单步骤最多两次上游尝试），否则预算与尝试计数会失真。
* 原生 ``usage`` 优先；供应商不返回 usage 时按 ``len(text)//3`` 保守估算并标记
  ``usage_source="estimated"``。
* 结构化输出：``json_schema`` 非空时优先 ``response_format={"type":"json_schema",...}``；
  供应商返回 BadRequest（不支持）时降级为 ``{"type":"json_object"}``，两次调用都计入
  ``LLMResult.attempts``（runner 的预算按实际上游调用计数）。
* 错误映射：429 → ``LLMRateLimitedError(retryable=True)``；超时/连接/5xx →
  ``LLMUnavailableError(retryable=True)``；400/401/403/404 等配置/密钥/模型问题 →
  ``LLMRequestError(retryable=False)``。
* 旧接口 ``llm_stream`` / ``llm_complete`` 保留（兼容路由与老前端仍调用），内部复用
  同一套 SDK 客户端与异常映射。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Literal

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
)

try:  # 兼容不同 openai 版本的异常命名
    from openai import APIError as _APIError
except ImportError:  # pragma: no cover - 极少见的旧版本
    from openai import OpenAIError as _APIError

from core.config import settings

logger = logging.getLogger(__name__)

UsageSource = Literal["provider", "estimated"]


# --------------------------------------------------------------------------- #
# 异常
# --------------------------------------------------------------------------- #


class LLMError(RuntimeError):
    """LLM 调用失败基类。

    ``code`` 是稳定错误码，runner 在步骤/ Run 失败时直接沿用；``retryable`` 决定
    执行器是否允许第二次上游尝试（PRD FR-03）。
    """

    code = "LLM_ERROR"
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        if retryable is not None:
            self.retryable = retryable
        if code is not None:
            self.code = code


class LLMRateLimitedError(LLMError):
    """上游限流（429），可重试。"""

    code = "LLM_RATE_LIMITED"
    retryable = True


class LLMUnavailableError(LLMError):
    """上游不可用（超时 / 连接失败 / 5xx），可重试。"""

    code = "LLM_UNAVAILABLE"
    retryable = True


class LLMRequestError(LLMError):
    """请求本身有问题（400/404 等），不可重试。"""

    code = "LLM_REQUEST_ERROR"
    retryable = False


class LLMAuthError(LLMRequestError):
    """鉴权失败（401/403）：API Key 无效、过期或无权限。

    与普通 4xx 分开，是因为它属于「人工改配置才能恢复」的错误：重试永远不会成功，
    但错误信息必须直接告诉使用者去更新 ``LLM_API_KEY``，否则界面上只剩一句失败。
    """

    code = "LLM_AUTH_ERROR"
    retryable = False


class LLMInsufficientBalanceError(LLMRequestError):
    """账户余额不足（402）：同样属于「人工处理才能恢复」的错误。

    上游在余额耗尽时会返回 402（某些中转站返回 403 并附带 ``insufficient_balance``
    文案）。若当成普通 4xx 或干脆当成「输出不合法」，用户只能看到一句「失败」，
    所以单列一类，把「充值 / 换 Key」写进错误信息。
    """

    code = "LLM_INSUFFICIENT_BALANCE"
    retryable = False


class LLMOutputInvalidError(LLMError):
    """结构化输出无法解析；属于可修复错误，允许一次重试。"""

    code = "MODEL_OUTPUT_INVALID"
    retryable = True


# 兼容旧代码可能引用的名字（历史上 429 也被归为不可用）。
LLMUpstreamError = LLMUnavailableError


# --------------------------------------------------------------------------- #
# 结果值对象（契约 §4.4）
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class LLMUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    usage_source: UsageSource = "provider"

    def as_dict(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "usage_source": self.usage_source,
        }


@dataclass(slots=True)
class LLMResult:
    text: str
    usage: LLMUsage
    model: str
    latency_ms: int
    attempts: int
    finish_reason: str | None = None

    @property
    def estimated_usage(self) -> bool:
        return self.usage.usage_source == "estimated"

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "usage": self.usage.as_dict(),
            "model": self.model,
            "latency_ms": self.latency_ms,
            "attempts": self.attempts,
            "finish_reason": self.finish_reason,
        }


def estimate_tokens(text: str) -> int:
    """供应商不返回 usage 时的保守估算：约 3 字符 ≈ 1 token，向下取整。"""
    return len(text or "") // 3


def _messages_text(messages: list[dict]) -> str:
    """把 messages 拍平成文本用于估算 prompt token。"""
    parts: list[str] = []
    for message in messages or []:
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# SDK 客户端与异常映射
# --------------------------------------------------------------------------- #


def _make_sdk_client(*, timeout_seconds: float | None = None) -> AsyncOpenAI:
    """创建关闭内部重试的 SDK 客户端（PRD FR-08：隐藏重试必须为 0）。"""
    return AsyncOpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        timeout=timeout_seconds or settings.llm_timeout_seconds,
        max_retries=0,
    )


#: 模块级共享客户端（旧接口复用；新代码请使用 :class:`LLMClient`）。
client = _make_sdk_client()


def _status_code_of(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    return code if isinstance(code, int) else None


def _translate_exception(exc: Exception) -> LLMError:
    """将上游异常映射为带 ``retryable`` 的领域异常。

    429 → 限流（可重试）；超时/连接/5xx → 不可用（可重试）；其余 4xx →
    请求/配置错误（不可重试，例如 401 密钥错误、404 模型不存在）。
    """
    if isinstance(exc, LLMError):
        return exc

    status = _status_code_of(exc)

    if status == 429:
        return LLMRateLimitedError("LLM 请求过于频繁，请稍后重试", status_code=status)
    if status == 403 and "insufficient" in str(exc).lower():
        return LLMInsufficientBalanceError(
            "模型账户余额不足（HTTP 403 / insufficient_balance）：请为 backend/.env 的 LLM_API_KEY 充值或更换可用 Key 后重启服务（start.bat / stop.bat）",
            status_code=status,
        )
    if status in (401, 403):
        return LLMAuthError(
            "模型服务鉴权失败（API Key 无效、已过期或无权限）："
            "请更新 backend/.env 的 LLM_API_KEY 后重启服务（start.bat / stop.bat）",
            status_code=status,
        )
    if status == 402:
        return LLMInsufficientBalanceError(
            "模型账户余额不足（HTTP 402）：请为 backend/.env 的 LLM_API_KEY 充值或更换可用 Key 后重启服务（start.bat / stop.bat）",
            status_code=status,
        )
    if status is not None and 500 <= status < 600:
        return LLMUnavailableError(f"LLM 服务暂时不可用（HTTP {status}）", status_code=status)
    if isinstance(exc, (APITimeoutError, APIConnectionError, TimeoutError, asyncio.TimeoutError)):
        return LLMUnavailableError("LLM 服务暂时不可用（超时或连接失败）")
    if isinstance(exc, APIStatusError) or (status is not None and 400 <= status < 500):
        return LLMRequestError(
            f"LLM 请求被拒绝（HTTP {status if status is not None else '客户端错误'}）",
            status_code=status,
        )
    if isinstance(exc, _APIError):
        return LLMUnavailableError("LLM 服务调用失败")
    return LLMUnavailableError("LLM 服务调用失败")


# --------------------------------------------------------------------------- #
# 新客户端
# --------------------------------------------------------------------------- #


class LLMClient:
    """单次补全的上游封装（无内部重试，严格超时，usage 归一化）。"""

    def __init__(
        self,
        *,
        sdk_client: Any | None = None,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.model = model or settings.llm_model
        if sdk_client is None:
            sdk_client = AsyncOpenAI(
                api_key=api_key or settings.llm_api_key,
                base_url=base_url or settings.llm_base_url,
                timeout=timeout_seconds or settings.llm_timeout_seconds,
                max_retries=0,
            )
        self._sdk = sdk_client
        #: None=未知；False=该供应商不支持 json_schema（缓存后直接降级，避免重复 400）。
        self._json_schema_supported: bool | None = None

    @property
    def sdk_client(self) -> Any:
        return self._sdk

    # -- 内部工具 ---------------------------------------------------------- #

    @staticmethod
    def _usage_from_response(response: Any, text: str, prompt_text: str) -> LLMUsage:
        usage = getattr(response, "usage", None)
        if usage is not None:
            prompt = getattr(usage, "prompt_tokens", None)
            completion = getattr(usage, "completion_tokens", None)
            total = getattr(usage, "total_tokens", None)
            if isinstance(prompt, int) and isinstance(completion, int):
                return LLMUsage(
                    prompt_tokens=prompt,
                    completion_tokens=completion,
                    total_tokens=total if isinstance(total, int) else prompt + completion,
                    usage_source="provider",
                )
        prompt_tokens = estimate_tokens(prompt_text)
        completion_tokens = estimate_tokens(text)
        return LLMUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            usage_source="estimated",
        )

    def _reasoning_extra_body(self) -> dict[str, Any]:
        """推理型模型（deepseek-flash/reasoner 等）关闭思考，避免 completion 预算被
        ``reasoning_content`` 吃光、导致结构化输出的 ``content`` 为空（AC-20 真模型联调发现）。
        """

        if not getattr(settings, "llm_disable_reasoning", False):
            return {}
        model = (self.model or "").lower()
        if "deepseek" not in model:
            return {}
        return {"extra_body": {"thinking": {"type": "disabled"}}}

    async def _create(self, *, kwargs: dict[str, Any], timeout_seconds: float):
        try:
            # 显式传 timeout：请求级超时优先于 SDK 默认值。
            return await self._sdk.chat.completions.create(timeout=timeout_seconds, **kwargs)
        except LLMError:
            raise
        except Exception as exc:  # noqa: BLE001 - 统一映射后再抛
            logger.warning(
                "LLM 调用失败 purpose=%s error=%s status=%s",
                kwargs.get("_purpose", ""),
                type(exc).__name__,
                _status_code_of(exc),
            )
            raise _translate_exception(exc) from exc

    # -- 补全 -------------------------------------------------------------- #

    async def complete(
        self,
        *,
        messages: list[dict],
        max_output_tokens: int | None = None,
        timeout_seconds: float,
        purpose: str,
        json_schema: dict | None = None,
        temperature: float = 0.3,
    ) -> LLMResult:
        """执行一次（或结构化降级时的两次）上游补全，返回归一化结果。

        ``max_output_tokens=None`` 时不发送 token 上限（仅供旧接口保留行为使用；
        新代码必须显式给出硬上限，PRD FR-08）。
        """
        started = time.perf_counter()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_output_tokens is not None:
            kwargs["max_tokens"] = int(max_output_tokens)

        prompt_text = _messages_text(messages)
        attempts = 0
        response: Any = None

        if json_schema:
            # OpenAI 兼容接口要求 json_schema 是 {name, schema, strict} 三件套；
            # 调用方若已传完整包装则原样使用，避免无谓的 400 + 降级（浪费一次上游调用）。
            if isinstance(json_schema, dict) and {"name", "schema"} <= set(json_schema):
                spec = json_schema
            else:
                spec = {"name": "structured_output", "schema": json_schema, "strict": True}
            schema_format = {"type": "json_schema", "json_schema": spec}
            if self._json_schema_supported is False:
                # 同一进程里已知该供应商拒绝 json_schema：直接走 json_object，
                # 否则每次角色调用都要白付一次 400（AC-20 真模型联调：4 角色 ×2 = 8 次上游调用）。
                attempts += 1
                response = await self._create(
                    kwargs={
                        **kwargs,
                        **self._reasoning_extra_body(),
                        "response_format": {"type": "json_object"},
                    },
                    timeout_seconds=timeout_seconds,
                )
            else:
                attempts += 1
                try:
                    response = await self._create(
                        kwargs={
                            **kwargs,
                            **self._reasoning_extra_body(),
                            "response_format": schema_format,
                        },
                        timeout_seconds=timeout_seconds,
                    )
                except LLMRequestError as exc:
                    # 供应商不支持 json_schema（通常 400）：记下结论，本次降级为 json_object。
                    logger.info(
                        "结构化输出降级 purpose=%s status=%s",
                        purpose,
                        getattr(exc, "status_code", None),
                    )
                    self._json_schema_supported = False
                    attempts += 1
                    response = await self._create(
                        kwargs={
                            **kwargs,
                            **self._reasoning_extra_body(),
                            "response_format": {"type": "json_object"},
                        },
                        timeout_seconds=timeout_seconds,
                    )
                else:
                    self._json_schema_supported = True
        else:
            attempts += 1
            response = await self._create(
                kwargs={**kwargs, **self._reasoning_extra_body()},
                timeout_seconds=timeout_seconds,
            )

        text = ""
        finish_reason: str | None = None
        choices = getattr(response, "choices", None) or []
        if choices:
            message = getattr(choices[0], "message", None)
            text = (getattr(message, "content", None) or "") if message is not None else ""
            finish_reason = getattr(choices[0], "finish_reason", None)

        usage = self._usage_from_response(response, text, prompt_text)
        model = getattr(response, "model", None) or self.model
        latency_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "LLM 调用完成 purpose=%s model=%s attempts=%s latency_ms=%s tokens=%s/%s usage_source=%s",
            purpose,
            model,
            attempts,
            latency_ms,
            usage.prompt_tokens,
            usage.completion_tokens,
            usage.usage_source,
        )
        return LLMResult(
            text=text,
            usage=usage,
            model=model,
            latency_ms=latency_ms,
            attempts=attempts,
            finish_reason=finish_reason,
        )

    # -- 便捷包装 ---------------------------------------------------------- #

    async def complete_text(
        self,
        *,
        messages: list[dict],
        max_output_tokens: int | None = None,
        timeout_seconds: float,
        purpose: str,
        temperature: float = 0.3,
    ) -> LLMResult:
        """纯文本补全，返回完整的 :class:`LLMResult`（含 usage，便于预算记账）。"""
        return await self.complete(
            messages=messages,
            max_output_tokens=max_output_tokens,
            timeout_seconds=timeout_seconds,
            purpose=purpose,
            temperature=temperature,
        )

    async def complete_json(
        self,
        *,
        messages: list[dict],
        max_output_tokens: int | None = None,
        timeout_seconds: float,
        purpose: str,
        json_schema: dict | None = None,
        temperature: float = 0.2,
    ) -> tuple[Any, LLMResult]:
        """结构化补全。

        返回 ``(parsed_json, LLMResult)`` —— 已在本函数的 docstring 中固定该契约
        （接口文档 §4.4 允许两种形式之一）。JSON 非法时抛
        ``LLMOutputInvalidError(retryable=True)``，由 runner 决定是否重试一次。
        """
        result = await self.complete(
            messages=messages,
            max_output_tokens=max_output_tokens,
            timeout_seconds=timeout_seconds,
            purpose=purpose,
            json_schema=json_schema,
            temperature=temperature,
        )
        try:
            parsed = json.loads(result.text)
        except (TypeError, ValueError) as exc:
            raise LLMOutputInvalidError(
                f"模型输出不是合法 JSON（purpose={purpose}）"
            ) from exc
        return parsed, result

    # -- 流式（旧接口复用） ------------------------------------------------ #

    async def stream(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.7,
        timeout_seconds: float | None = None,
    ) -> AsyncIterator[str]:
        """流式 token（供旧 ``llm_stream`` 兼容接口使用）。"""
        try:
            stream = await self._sdk.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                stream=True,
                timeout=timeout_seconds or settings.llm_timeout_seconds,
            )
            async for chunk in stream:
                # 某些代理会发不含 choices 的心跳/用量帧，直接跳过（B063）。
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                delta = getattr(choices[0], "delta", None)
                content = getattr(delta, "content", None) if delta is not None else None
                if content:
                    yield content
        except LLMError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM 流式调用失败：%s", type(exc).__name__)
            raise _translate_exception(exc) from exc


#: 默认客户端（旧接口与便捷函数复用）。
default_client = LLMClient(sdk_client=client)


# --------------------------------------------------------------------------- #
# 旧 API（必须保留：compat adapter 与老前端仍调用）
# --------------------------------------------------------------------------- #


async def llm_stream(messages: list[dict], temperature: float = 0.7):
    """流式输出 LLM token（保留旧签名，内部复用 :class:`LLMClient`）。"""
    async for token in default_client.stream(messages, temperature=temperature):
        yield token


async def llm_complete(messages: list[dict], temperature: float = 0.3) -> str:
    """非流式补全（保留旧签名）。不设置 max_tokens，保持旧行为。"""
    result = await default_client.complete(
        messages=messages,
        max_output_tokens=None,
        timeout_seconds=settings.llm_timeout_seconds,
        purpose="compat.llm_complete",
        temperature=temperature,
    )
    return result.text


# --------------------------------------------------------------------------- #
# 模块级便捷函数（新代码可用；usage 通过 LLMResult 返回）
# --------------------------------------------------------------------------- #


async def complete_text(
    messages: list[dict],
    *,
    max_output_tokens: int,
    timeout_seconds: float,
    purpose: str,
    temperature: float = 0.3,
) -> LLMResult:
    return await default_client.complete_text(
        messages=messages,
        max_output_tokens=max_output_tokens,
        timeout_seconds=timeout_seconds,
        purpose=purpose,
        temperature=temperature,
    )


async def complete_json(
    messages: list[dict],
    *,
    max_output_tokens: int,
    timeout_seconds: float,
    purpose: str,
    json_schema: dict | None = None,
    temperature: float = 0.2,
) -> tuple[Any, LLMResult]:
    return await default_client.complete_json(
        messages=messages,
        max_output_tokens=max_output_tokens,
        timeout_seconds=timeout_seconds,
        purpose=purpose,
        json_schema=json_schema,
        temperature=temperature,
    )


__all__ = [
    "LLMClient",
    "LLMError",
    "LLMOutputInvalidError",
    "LLMRateLimitedError",
    "LLMRequestError",
    "LLMResult",
    "LLMUnavailableError",
    "LLMUpstreamError",
    "LLMUsage",
    "client",
    "complete_json",
    "complete_text",
    "default_client",
    "estimate_tokens",
    "llm_complete",
    "llm_stream",
]
