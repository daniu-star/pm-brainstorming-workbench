import asyncio
import logging
import os
from io import BytesIO

from huggingface_hub import InferenceClient
from openai import AsyncOpenAI

from .config import settings

logger = logging.getLogger(__name__)

DEFAULT_VOICE = "zh-CN-YunjianNeural"

# 面试官专用嗓音 profile：年轻男声，略慢语速 + 微降音调，营造沉稳压力感
INTERVIEWER_VOICE_PROFILE = {
    "voice": "zh-CN-YunxiNeural",
    "rate": "-5%",
    "pitch": "-2%",
}


async def transcribe_audio(
    audio_bytes: bytes,
    content_type: str,
    api_key: str = "",
    base_url: str = "",
    model: str = "",
) -> str:
    effective_key = api_key or settings.llm_api_key
    if not effective_key:
        raise RuntimeError("未提供 API Key，无法使用语音转文字服务")

    effective_base_url = base_url or settings.llm_base_url or None
    client = AsyncOpenAI(
        api_key=effective_key,
        base_url=effective_base_url,
        timeout=settings.stt_timeout_seconds,
    )

    extension_by_type = {
        "audio/wav": "wav",
        "audio/mp4": "mp4",
        "audio/ogg": "ogg",
        "audio/mpeg": "mp3",
        "audio/webm": "webm",
    }
    ext = extension_by_type.get(content_type, "webm")
    filename = f"audio.{ext}"

    response = await client.audio.transcriptions.create(
        model=model or settings.stt_model,
        file=(filename, audio_bytes, content_type),
    )

    text = (response.text or "").strip()
    logger.info("STT transcribed %s bytes -> %s chars", len(audio_bytes), len(text))
    return text


HF_API_TOKEN = os.getenv("HF_API_TOKEN", "")

# 多模型回退链：turbo 优先保证响应速度，large-v3 负责中文识别回退。
HF_WHISPER_MODELS = [
    os.getenv("HF_WHISPER_MODEL_PRIMARY", "openai/whisper-large-v3-turbo"),
    os.getenv("HF_WHISPER_MODEL_FALLBACK", "openai/whisper-large-v3"),
]


async def _call_hf_whisper(model: str, audio_bytes: bytes, content_type: str) -> str:
    """Call one Whisper model through the current HF Inference Providers API."""
    if not HF_API_TOKEN:
        raise RuntimeError("HF_API_TOKEN 未配置")

    client = InferenceClient(
        provider="hf-inference",
        api_key=HF_API_TOKEN,
        timeout=min(settings.stt_timeout_seconds, 30.0),
    )

    def _transcribe():
        return client.automatic_speech_recognition(audio_bytes, model=model)

    result = await asyncio.to_thread(_transcribe)
    text = (getattr(result, "text", "") or "").strip()
    logger.info("HF STT (%s) transcribed %s bytes -> %s chars", model, len(audio_bytes), len(text))
    return text


async def transcribe_audio_hf(
    audio_bytes: bytes,
    content_type: str,
    model: str = "",
) -> str:
    """多模型回退链：依次尝试多个 HF Whisper 模型"""
    if not HF_API_TOKEN:
        raise RuntimeError("HF_API_TOKEN 未配置，无法使用免费语音识别服务")

    models_to_try = [model] if model else HF_WHISPER_MODELS
    last_error = None

    for m in models_to_try:
        try:
            text = await _call_hf_whisper(m, audio_bytes, content_type)
            if text.strip():
                return text
            logger.warning("HF STT model %s returned empty text, trying next...", m)
        except Exception as e:
            last_error = e
            logger.warning("HF STT model %s failed: %s, trying next model...", m, e)

    raise RuntimeError(f"所有 HF Whisper 模型均失败，最后错误: {last_error}")


async def synthesize_speech(text: str, voice: str | None = None, rate: str = "+0%", pitch: str = "+0%") -> bytes:
    """TTS 合成，支持语速和音调参数"""
    voice = voice or DEFAULT_VOICE

    try:
        import edge_tts
    except ImportError:
        raise RuntimeError("edge-tts 未安装，请运行: pip install edge-tts")

    communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    buffer = BytesIO()

    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            buffer.write(chunk["data"])

    audio_bytes = buffer.getvalue()
    buffer.close()

    if not audio_bytes:
        raise RuntimeError("TTS 合成失败：未收到音频数据")

    logger.info("TTS synthesized %s bytes with voice '%s'", len(audio_bytes), voice)
    return audio_bytes
