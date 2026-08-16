import asyncio
import logging
from io import BytesIO

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

from core.config import settings

logger = logging.getLogger(__name__)

# STT 客户端懒加载单例（B129）。
_stt_client: AsyncOpenAI | None = None

DEFAULT_VOICE = "zh-CN-YunjianNeural"
SUPPORTED_AUDIO_TYPES = {
    "audio/flac",
    "audio/m4a",
    "audio/mp3",
    "audio/mp4",
    "audio/mpeg",
    "audio/ogg",
    "audio/wav",
    "audio/webm",
    "video/webm",
}


class VoiceServiceError(RuntimeError):
    """Base exception for a failed voice operation."""


class VoiceServiceUnavailable(VoiceServiceError):
    """Raised when a requested voice capability is not configured."""


class VoiceUpstreamError(VoiceServiceError):
    """Raised when an upstream voice provider cannot complete a request."""


async def synthesize_speech(text: str, voice: str | None = None) -> bytes:
    """Synthesize speech with a bounded upstream request."""
    selected_voice = voice or DEFAULT_VOICE

    try:
        import edge_tts
    except ImportError as exc:
        raise VoiceServiceUnavailable("edge-tts 未安装，请运行: pip install edge-tts") from exc

    async def collect_audio() -> bytes:
        communicate = edge_tts.Communicate(text, selected_voice)
        buffer = BytesIO()

        try:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    buffer.write(chunk["data"])
            return buffer.getvalue()
        finally:
            buffer.close()

    try:
        audio_bytes = await asyncio.wait_for(collect_audio(), timeout=settings.tts_timeout_seconds)
    except TimeoutError as exc:
        raise VoiceUpstreamError("TTS 合成超时，请稍后重试") from exc
    except Exception as exc:
        logger.warning("TTS upstream request failed: %s", type(exc).__name__)
        raise VoiceUpstreamError("TTS 服务暂时不可用") from exc

    if not audio_bytes:
        raise VoiceUpstreamError("TTS 合成失败：未收到音频数据")

    logger.info("TTS synthesized %s bytes with voice '%s'", len(audio_bytes), selected_voice)
    return audio_bytes


async def transcribe_speech(audio: bytes, content_type: str) -> str:
    """Transcribe an in-memory audio recording through the configured STT provider."""
    if not settings.stt_api_key:
        raise VoiceServiceUnavailable("服务端语音识别尚未配置")

    normalized_type = content_type.split(";", 1)[0].lower()
    if normalized_type not in SUPPORTED_AUDIO_TYPES:
        raise VoiceServiceError(f"不支持的音频格式：{normalized_type or 'unknown'}")

    # 模块级懒加载单例，避免每次请求新建带连接池的客户端（B129）。
    global _stt_client
    if _stt_client is None:
        _stt_client = AsyncOpenAI(
            api_key=settings.stt_api_key,
            base_url=settings.stt_base_url,
            timeout=settings.stt_timeout_seconds,
            max_retries=1,
        )
    client = _stt_client
    extension = _extension_for_content_type(normalized_type)

    try:
        response = await client.audio.transcriptions.create(
            model=settings.stt_model,
            file=(f"recording.{extension}", audio, normalized_type),
            language=settings.stt_language,
            response_format="json",
        )
    except APITimeoutError as exc:
        raise VoiceUpstreamError("语音识别超时，请缩短录音后重试") from exc
    except APIConnectionError as exc:
        raise VoiceUpstreamError("无法连接语音识别服务") from exc
    except APIStatusError as exc:
        logger.warning("STT provider returned status %s", exc.status_code)
        raise VoiceUpstreamError("语音识别服务返回错误") from exc

    transcript = response.text.strip()
    if not transcript:
        raise VoiceUpstreamError("没有识别到清晰语音")

    logger.info("STT transcribed %s input bytes", len(audio))
    return transcript


def _extension_for_content_type(content_type: str) -> str:
    return {
        "audio/flac": "flac",
        "audio/m4a": "m4a",
        "audio/mp3": "mp3",
        "audio/mp4": "mp4",
        "audio/mpeg": "mp3",
        "audio/ogg": "ogg",
        "audio/wav": "wav",
        "audio/webm": "webm",
        "video/webm": "webm",
    }.get(content_type, "webm")
