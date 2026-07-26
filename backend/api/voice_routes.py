import asyncio
import logging
from fastapi import APIRouter, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import Response
from pydantic import BaseModel, Field
from core.voice import (
    synthesize_speech,
    transcribe_audio,
    transcribe_audio_hf,
    HF_API_TOKEN,
    INTERVIEWER_VOICE_PROFILE,
)
from core.config import settings
from api.deps import get_current_user, get_user_llm_config, check_quota

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/voice", tags=["voice"])


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=settings.tts_max_characters)
    voice: str | None = Field(default=None, max_length=80)
    rate: str = Field(default="+0%", pattern=r"^[+-]\d+%$", max_length=12)
    pitch: str = Field(default="+0Hz", pattern=r"^[+-]\d+Hz$", max_length=12)


class InterviewerTTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=settings.tts_max_characters)


INTERVIEWER_VOICE = "zh-CN-YunxiNeural"

# 支持 audio.transcriptions 的 provider 域名关键词
AUDIO_CAPABLE_KEYWORDS = ("openai", "groq", "together")


def _is_audio_capable_provider(llm_config: dict) -> bool:
    """判断用户的 LLM provider 是否支持 Whisper 语音识别"""
    base_url = (llm_config.get("base_url") or "").lower()
    return any(kw in base_url for kw in AUDIO_CAPABLE_KEYWORDS)


@router.get("/capabilities")
async def voice_capabilities(request: Request):
    """告诉前端应优先使用服务端 STT 还是浏览器内置识别，不暴露密钥信息。"""
    get_current_user(request)
    llm_config = get_user_llm_config(request)
    return {
        "server_stt_available": bool(HF_API_TOKEN)
        or bool(llm_config.get("api_key") and _is_audio_capable_provider(llm_config)),
        "max_audio_bytes": settings.stt_max_audio_bytes,
    }


@router.post("/tts")
async def text_to_speech(
    req: TTSRequest,
    request: Request,
    voice_preset: str | None = Query(None, alias="voice"),
):
    user = get_current_user(request)
    llm_config = get_user_llm_config(request)
    check_quota(user, llm_config)
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="文本不能为空")
    effective_voice = req.voice
    if voice_preset == "interviewer":
        effective_voice = INTERVIEWER_VOICE
    try:
        audio = await asyncio.wait_for(
            synthesize_speech(req.text.strip(), effective_voice, rate=req.rate, pitch=req.pitch),
            timeout=settings.tts_timeout_seconds,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="语音合成超时，请缩短文本后重试")
    except (RuntimeError, ValueError):
        logger.exception("TTS synthesis failed")
        raise HTTPException(status_code=503, detail="语音合成服务暂不可用，请稍后重试")
    return Response(content=audio, media_type="audio/mpeg")


@router.post("/tts/interviewer")
async def interviewer_text_to_speech(req: InterviewerTTSRequest, request: Request):
    """面试官专用 TTS 端点，使用 INTERVIEWER_VOICE_PROFILE（语速 -5%、音调 -2%）"""
    user = get_current_user(request)
    llm_config = get_user_llm_config(request)
    check_quota(user, llm_config)
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="文本不能为空")
    try:
        audio = await asyncio.wait_for(
            synthesize_speech(
                req.text.strip(),
                voice=INTERVIEWER_VOICE_PROFILE["voice"],
                rate=INTERVIEWER_VOICE_PROFILE["rate"],
                pitch=INTERVIEWER_VOICE_PROFILE["pitch"],
            ),
            timeout=settings.tts_timeout_seconds,
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="语音合成超时，请缩短文本后重试")
    except (RuntimeError, ValueError):
        logger.exception("Interviewer TTS synthesis failed")
        raise HTTPException(status_code=503, detail="语音合成服务暂不可用，请稍后重试")
    return Response(content=audio, media_type="audio/mpeg")


@router.post("/stt")
async def speech_to_text(request: Request, file: UploadFile = File(...)):
    user = get_current_user(request)
    llm_config = get_user_llm_config(request)
    check_quota(user, llm_config)

    content_type = file.content_type or "audio/webm"
    # Normalize content_type - browsers may send "audio/webm;codecs=opus"
    ct_lower = content_type.lower().split(";")[0].strip()
    if ct_lower not in ("audio/webm", "audio/wav", "audio/mp4", "audio/ogg", "audio/mpeg"):
        raise HTTPException(status_code=400, detail=f"不支持的音频格式: {content_type}，仅支持 webm/wav/mp3/ogg")
    content_type = ct_lower

    try:
        audio_bytes = await file.read(settings.stt_max_audio_bytes + 1)
    except Exception:
        raise HTTPException(status_code=400, detail="读取音频文件失败")
    finally:
        await file.close()

    if not audio_bytes:
        raise HTTPException(status_code=400, detail="音频文件为空")
    if len(audio_bytes) > settings.stt_max_audio_bytes:
        max_mb = settings.stt_max_audio_bytes // (1024 * 1024)
        raise HTTPException(status_code=413, detail=f"音频文件过大，请控制在 {max_mb}MB 以内")

    text = ""
    provider_error: Exception | None = None
    hf_error: Exception | None = None

    # 已配置兼容 Whisper 的用户 API 时优先使用，避免免费 HF 模型冷启动导致时灵时不灵。
    if llm_config.get("api_key") and _is_audio_capable_provider(llm_config):
        try:
            text = await asyncio.wait_for(
                transcribe_audio(
                    audio_bytes=audio_bytes,
                    content_type=content_type,
                    api_key=llm_config["api_key"],
                    base_url=llm_config["base_url"],
                    model=settings.stt_model,
                ),
                timeout=settings.stt_timeout_seconds,
            )
        except Exception as error:
            provider_error = error
            logger.warning("Configured provider STT failed: %s", error)

    # 用户 API 未配置或失败时，使用 HF 多模型回退链。
    if not text and HF_API_TOKEN:
        try:
            text = await asyncio.wait_for(
                transcribe_audio_hf(
                    audio_bytes=audio_bytes,
                    content_type=content_type,
                ),
                timeout=settings.stt_timeout_seconds,
            )
        except Exception as error:
            hf_error = error
            logger.warning("HF STT fallback failed: %s", error)

    if not text.strip():
        if provider_error or hf_error:
            logger.error(
                "All STT providers failed (configured=%s, hf=%s)",
                type(provider_error).__name__ if provider_error else "not-used",
                type(hf_error).__name__ if hf_error else "not-used",
            )
            raise HTTPException(status_code=503, detail="语音识别服务暂不可用，请稍后重试")
        raise HTTPException(
            status_code=503,
            detail="服务端语音识别尚未配置，将自动使用浏览器语音识别。",
        )

    return {"text": text.strip()}
