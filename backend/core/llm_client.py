from urllib.parse import urlparse
from typing import List, Optional, Tuple, AsyncGenerator
from openai import AsyncOpenAI, APIError
from .config import settings

_default_client = None

ALLOWED_BASE_URL_DOMAINS = [
    "api.openai.com",
    "api.deepseek.com",
    "api.anthropic.com",
    "api.moonshot.cn",
    "dashscope.aliyuncs.com",
    "open.bigmodel.cn",
    "api.lingyiwanwu.com",
    "api.minimax.chat",
    "api.baichuan-ai.com",
    "spark-api-open.xf-yun.com",
    "ark.cn-beijing.volces.com",
    "api.hunyuan.cloud.tencent.com",
    "api.siliconflow.cn",
    "api.groq.com",
    "api.together.xyz",
    "openrouter.ai",
]


def _validate_base_url(base_url: str) -> str:
    if not base_url:
        return base_url
    parsed = urlparse(base_url)
    hostname = parsed.hostname or ""
    for allowed in ALLOWED_BASE_URL_DOMAINS:
        if hostname == allowed or hostname.endswith("." + allowed):
            return base_url
    raise ValueError(f"不允许的 Base URL 域名: {hostname}。仅支持已知 LLM 提供商的域名。")

def _get_default_client():
    global _default_client
    if _default_client is None:
        _default_client = AsyncOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            timeout=60.0,
        )
    return _default_client


PROVIDER_MAP = {
    "deepseek": {"base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o"},
    "anthropic": {"base_url": "https://api.anthropic.com/v1", "model": "claude-3-sonnet-20240229"},
    "moonshot": {"base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k"},
    "qwen": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-turbo"},
    "dashscope": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-turbo"},
    "glm": {"base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4"},
    "zhipu": {"base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4"},
    "yi": {"base_url": "https://api.lingyiwanwu.com/v1", "model": "yi-lightning"},
    "minimax": {"base_url": "https://api.minimax.chat/v1", "model": "minimax-chat"},
    "baichuan": {"base_url": "https://api.baichuan-ai.com/v1", "model": "baichuan2-turbo"},
    "spark": {"base_url": "https://spark-api-open.xf-yun.com/v1", "model": "generalv3.5"},
    "doubao": {"base_url": "https://ark.cn-beijing.volces.com/api/v3", "model": "doubao-pro-4k"},
    "hunyuan": {"base_url": "https://api.hunyuan.cloud.tencent.com/v1", "model": "hunyuan-lite"},
}

KEY_PREFIX_MAP = [
    ("sk-ant-", "anthropic"),
    ("sk-dashscope-", "dashscope"),
    ("moonshot-", "moonshot"),
    ("dz-", "deepseek"),
    ("sk-a6f", "deepseek"),
]


def _detect_provider(api_key: str, model: str = "") -> dict:
    if model:
        model_lower = model.lower().replace(" ", "").replace("-", "").replace("_", "")
        for keyword, config in PROVIDER_MAP.items():
            if keyword in model_lower:
                return {"provider": keyword, **config}
    for prefix, provider in KEY_PREFIX_MAP:
        if api_key.startswith(prefix):
            config = PROVIDER_MAP.get(provider, PROVIDER_MAP["openai"])
            return {"provider": provider, **config}
    return {"provider": "openai", **PROVIDER_MAP["openai"]}


def _resolve_base_url(provided_base_url: str, detected: dict, model: str) -> str:
    detected_base_url = detected["base_url"].rstrip("/")
    if not provided_base_url:
        return detected["base_url"]
    if not model:
        return provided_base_url
    provided = provided_base_url.rstrip("/")
    if provided == detected_base_url:
        return provided_base_url
    for config in PROVIDER_MAP.values():
        if provided == config["base_url"].rstrip("/"):
            return detected["base_url"]
    return provided_base_url


def _normalize_model_name(model: str) -> str:
    return model.replace(" ", "-")


def _get_client_and_model(
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
) -> Tuple[AsyncOpenAI, str]:
    if api_key:
        normalized_model = _normalize_model_name(model) if model else ""
        detected = _detect_provider(api_key, normalized_model)
        base_url = _resolve_base_url(base_url or "", detected, normalized_model)
        if not normalized_model:
            normalized_model = detected["model"]
        _validate_base_url(base_url)
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=60.0,
        )
        return client, normalized_model or "gpt-4o"
    if not settings.llm_api_key:
        raise ValueError(
            f"服务暂不可用：后端未配置默认 LLM API Key，且请求中未携带用户 API Key。\n"
            f"诊断信息:\n"
            f"- 后端 LLM_API_KEY 环境变量: {'已设置' if settings.llm_api_key else '未设置'}\n"
            f"- 请求中的 api_key 参数: {'已提供' if api_key else '未提供'}\n"
            f"- 请在 .env 文件配置 LLM_API_KEY，或在前端设置中填写自己的 API Key"
        )
    return _get_default_client(), settings.llm_model


def _estimate_tokens(messages: List[dict]) -> int:
    total_chars = sum(len(m.get("content", "")) for m in messages)
    return max(1, int(total_chars / 2.5))


async def llm_stream(
    messages: List[dict],
    temperature: float = 0.7,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
) -> AsyncGenerator[Tuple[str, int], None]:
    client, model_name = _get_client_and_model(api_key, base_url, model)
    try:
        stream = await client.chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=temperature,
            stream=True,
            stream_options={"include_usage": True},
        )
    except Exception:
        stream = await client.chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=temperature,
            stream=True,
        )
    total_tokens = 0
    async for chunk in stream:
        if chunk.usage:
            total_tokens = chunk.usage.total_tokens or 0
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content, 0
    if total_tokens == 0:
        total_tokens = _estimate_tokens(messages)
    yield "", total_tokens


async def llm_complete(
    messages: List[dict],
    temperature: float = 0.3,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
) -> Tuple[str, int]:
    client, model_name = _get_client_and_model(api_key, base_url, model)
    response = await client.chat.completions.create(
        model=model_name,
        messages=messages,
        temperature=temperature,
    )
    content = response.choices[0].message.content or ""
    total_tokens = response.usage.total_tokens if response.usage else 0
    return content, total_tokens
