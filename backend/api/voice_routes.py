import asyncio

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from core.config import settings
from core.voice import (
    DEFAULT_VOICE,
    SUPPORTED_AUDIO_TYPES,
    VoiceServiceError,
    VoiceServiceUnavailable,
    VoiceUpstreamError,
    synthesize_speech,
    transcribe_speech,
)

router = APIRouter(prefix="/api/voice", tags=["voice"])


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=settings.tts_max_characters)
    voice: str | None = Field(default=None, max_length=80)


class STTResponse(BaseModel):
    text: str
    engine: str


class VoiceCapabilitiesResponse(BaseModel):
    stt_enabled: bool
    stt_model: str | None
    max_audio_bytes: int
    max_recording_seconds: int


@router.get("/capabilities", response_model=VoiceCapabilitiesResponse)
async def voice_capabilities() -> VoiceCapabilitiesResponse:
    return VoiceCapabilitiesResponse(
        stt_enabled=bool(settings.stt_api_key),
        stt_model=settings.stt_model if settings.stt_api_key else None,
        max_audio_bytes=settings.stt_max_audio_bytes,
        max_recording_seconds=settings.stt_max_recording_seconds,
    )


@router.post("/tts")
async def text_to_speech(req: TTSRequest) -> Response:
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="文本不能为空")

    try:
        audio = await synthesize_speech(req.text.strip(), req.voice or DEFAULT_VOICE)
    except VoiceServiceUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except VoiceUpstreamError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return Response(content=audio, media_type="audio/mpeg")


@router.post("/stt", response_model=STTResponse)
async def speech_to_text(file: UploadFile = File(...)) -> STTResponse:
    content_type = (file.content_type or "").lower()
    normalized_type = content_type.split(";", 1)[0]

    if normalized_type not in SUPPORTED_AUDIO_TYPES:
        raise HTTPException(status_code=415, detail="不支持的音频格式")

    try:
        audio = await file.read(settings.stt_max_audio_bytes + 1)
    finally:
        await file.close()

    if not audio:
        raise HTTPException(status_code=400, detail="录音内容为空")
    if len(audio) > settings.stt_max_audio_bytes:
        raise HTTPException(status_code=413, detail="录音文件过大，请缩短录音后重试")

    try:
        transcript = await transcribe_speech(audio, content_type)
    except VoiceServiceUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except VoiceUpstreamError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except VoiceServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except asyncio.CancelledError:
        raise

    return STTResponse(text=transcript, engine="server")
