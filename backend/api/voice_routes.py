import logging
from fastapi import APIRouter, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import Response
from pydantic import BaseModel
from core.voice import (
    synthesize_speech,
    transcribe_audio,
    transcribe_audio_hf,
    DEFAULT_VOICE,
    INTERVIEWER_VOICE_PROFILE,
)
from api.deps import get_current_user, get_user_llm_config, check_quota

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/voice", tags=["voice"])


class TTSRequest(BaseModel):
    text: str
    voice: str | None = None
    rate: str = "+0%"
    pitch: str = "+0%"


class InterviewerTTSRequest(BaseModel):
    text: str


INTERVIEWER_VOICE = "zh-CN-YunxiNeural"

# 支持 audio.transcriptions 的 provider 域名关键词
AUDIO_CAPABLE_KEYWORDS = ("openai", "groq", "together")


def _is_audio_capable_provider(llm_config: dict) -> bool:
    """判断用户的 LLM provider 是否支持 Whisper 语音识别"""
    base_url = (llm_config.get("base_url") or "").lower()
    return any(kw in base_url for kw in AUDIO_CAPABLE_KEYWORDS)


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
        audio = await synthesize_speech(req.text, effective_voice, rate=req.rate, pitch=req.pitch)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
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
        audio = await synthesize_speech(
            req.text,
            voice=INTERVIEWER_VOICE_PROFILE["voice"],
            rate=INTERVIEWER_VOICE_PROFILE["rate"],
            pitch=INTERVIEWER_VOICE_PROFILE["pitch"],
        )
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
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
        audio_bytes = await file.read()
    except Exception:
        raise HTTPException(status_code=400, detail="读取音频文件失败")

    if not audio_bytes:
        raise HTTPException(status_code=400, detail="音频文件为空")

    try:
        # 优先使用 HF Whisper（免费），多模型回退链已在 transcribe_audio_hf 内实现
        try:
            text = await transcribe_audio_hf(
                audio_bytes=audio_bytes,
                content_type=content_type,
            )
        except RuntimeError as hf_err:
            # HF 全部模型失败后，若用户配置了支持 audio 的 provider 则尝试回退
            if llm_config["api_key"] and _is_audio_capable_provider(llm_config):
                logger.warning(f"HF STT failed ({hf_err}), falling back to user API ({llm_config.get('base_url')})")
                try:
                    text = await transcribe_audio(
                        audio_bytes=audio_bytes,
                        content_type=content_type,
                        api_key=llm_config["api_key"],
                        base_url=llm_config["base_url"],
                        model=llm_config["model"],
                    )
                except Exception as user_err:
                    raise HTTPException(
                        status_code=503,
                        detail=f"语音识别服务暂不可用。HF错误：{hf_err}；用户API错误：{user_err}。请在设置中配置有效的 HF_API_TOKEN 或使用支持 Whisper 的 API（如 OpenAI/Groq）。"
                    )
            else:
                raise HTTPException(
                    status_code=503,
                    detail=f"语音识别服务暂不可用：{hf_err}。请在后端 .env 中配置有效的 HF_API_TOKEN，或在设置中配置支持 Whisper 的 API（如 OpenAI/Groq/Together）。"
                )
    except HTTPException:
        raise
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.exception("STT transcription failed")
        raise HTTPException(status_code=500, detail=f"语音转文字失败: {str(e)}")

    return {"text": text}
