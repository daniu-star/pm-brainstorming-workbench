"""LLM 客户端封装：超时、重试与异常分类（B031/B063）。"""
import logging

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
)

try:
    from openai import APIRateLimitError
except ImportError:  # 兼容不同 openai 版本的异常命名
    from openai import RateLimitError as APIRateLimitError

from core.config import settings

logger = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """LLM 调用失败基类。"""


class LLMRateLimitedError(LLMError):
    """上游限流（429）。"""


class LLMUnavailableError(LLMError):
    """上游不可用（超时 / 连接失败 / 其他状态错误）。"""


client = AsyncOpenAI(
    api_key=settings.llm_api_key,
    base_url=settings.llm_base_url,
    timeout=settings.llm_timeout_seconds,
    max_retries=settings.llm_max_retries,
)


def _translate_exception(exc: Exception) -> LLMError:
    """将 openai SDK 异常映射为自定义异常，供上层统一处理。"""
    if isinstance(exc, APIRateLimitError):
        return LLMRateLimitedError("LLM 请求过于频繁，请稍后重试")
    if isinstance(exc, (APITimeoutError, APIConnectionError)):
        return LLMUnavailableError("LLM 服务暂时不可用（超时或连接失败）")
    if isinstance(exc, APIStatusError):
        return LLMUnavailableError(f"LLM 服务返回错误（HTTP {exc.status_code}）")
    return LLMUnavailableError("LLM 服务调用失败")


async def llm_stream(messages: list[dict], temperature: float = 0.7):
    """流式输出 LLM token。"""
    try:
        stream = await client.chat.completions.create(
            model=settings.llm_model,
            messages=messages,
            temperature=temperature,
            stream=True,
        )
        async for chunk in stream:
            # 某些 chunk 不含 choices（如部分代理的心跳/用量帧），直接跳过（B063）。
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta
    except LLMError:
        raise
    except Exception as exc:
        logger.warning("LLM 流式调用失败：%s", type(exc).__name__)
        raise _translate_exception(exc) from exc


async def llm_complete(messages: list[dict], temperature: float = 0.3) -> str:
    """非流式补全，用于结构化输出（Canvas 解析等）。"""
    try:
        response = await client.chat.completions.create(
            model=settings.llm_model,
            messages=messages,
            temperature=temperature,
        )
        return response.choices[0].message.content or ""
    except LLMError:
        raise
    except Exception as exc:
        logger.warning("LLM 补全调用失败：%s", type(exc).__name__)
        raise _translate_exception(exc) from exc
