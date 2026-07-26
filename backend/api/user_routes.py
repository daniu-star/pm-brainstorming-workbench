from urllib.parse import urlparse

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from db.user_store import user_store
from core.config import settings

router = APIRouter(prefix="/api/user", tags=["user"])


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


class SaveApiKeyRequest(BaseModel):
    api_key: str
    base_url: str = ""
    model: str = ""


@router.get("/quota")
async def get_quota(request: Request):
    user_token = request.headers.get("X-User-Token", "")
    if not user_token:
        raise HTTPException(status_code=400, detail="缺少 X-User-Token Header")
    return user_store.get_quota(user_token)


@router.post("/apikey")
async def save_api_key(request: Request, req: SaveApiKeyRequest):
    user_token = request.headers.get("X-User-Token", "")
    if not user_token:
        raise HTTPException(status_code=400, detail="缺少 X-User-Token Header")
    user_store.save_api_key(user_token, req.api_key, req.base_url, req.model)
    return {"status": "saved"}


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


@router.post("/test-key")
async def test_api_key(req: SaveApiKeyRequest):
    try:
        from openai import AsyncOpenAI
        model = _normalize_model_name(req.model) if req.model else ""
        detected = _detect_provider(req.api_key, model)
        base_url = _resolve_base_url(req.base_url, detected, model)
        model = model or detected["model"]
        provider = detected["provider"]
        _validate_base_url(base_url)
        client = AsyncOpenAI(api_key=req.api_key, base_url=base_url, timeout=10.0)
        await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=1,
        )
        return {"status": "ok", "provider": provider, "base_url": base_url, "model": model}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"API Key 验证失败 (base_url={base_url}, model={model}): {str(e)}")


@router.get("/diagnose")
async def diagnose_connection(request: Request):
    user_token = request.headers.get("X-User-Token", "")
    api_key_raw = request.headers.get("X-API-Key", "") or request.headers.get("X-API_Key", "")
    base_url_raw = request.headers.get("X-Base-URL", "") or request.headers.get("X-Base_URL", "")
    model_raw = request.headers.get("X-Model", "")

    user_data = None
    if user_token:
        user_data = user_store.get_or_create_user(user_token)

    return {
        "received_headers": {
            "x-user-token": user_token[:8] + "..." if len(user_token) > 8 else user_token,
            "x-api-key": ("已设置 (" + api_key_raw[:6] + "...)" if api_key_raw else "未设置"),
            "x-base-url": base_url_raw or "未设置",
            "x-model": model_raw or "未使用默认值",
        },
        "user_info": {
            "token_quota": user_data["token_quota"] if user_data else 0,
            "tokens_used": user_data["tokens_used"] if user_data else 0,
            "has_stored_api_key": bool(user_data.get("api_key")) if user_data else False,
        } if user_data else None,
        "server_config": {
            "has_default_llm_key": bool(settings.llm_api_key),
            "default_model": settings.llm_model,
            "default_base_url": settings.llm_base_url,
        },
        "llm_config_will_use": {
            "api_key_source": "用户自带Key" if api_key_raw else ("服务器默认Key" if settings.llm_api_key else "无可用Key"),
            "base_url": base_url_raw or (settings.llm_base_url if settings.llm_api_key else ""),
            "model": model_raw or (settings.llm_model if settings.llm_api_key else ""),
        }
    }
