import logging
from fastapi import APIRouter, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import Response
from pydantic import BaseModel
from core.voice import synthesize_speech, transcribe_audio, transcribe_audio_hf, DEFAULT_VOICE
from api.deps import get_current_user, get_user_llm_config, check_quota

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/voice", tags=["voice"])


class TTSRequest(BaseModel):
    text: str
    voice: str | None = None


INTERVIEWER_VOICE = "zh-CN-YunxiNeural"


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
        audio = await synthesize_speech(req.text, effective_voice)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return Response(content=audio, media_type="audio/mpeg")


@router.post("/stt")
async def speech_to_text(request: Request, file: UploadFile = File(...)):
    user = get_current_user(request)
    llm_config = get_user_llm_config(request)
    check_quota(user, llm_config)

    content_type = file.content_type or "audio/webm"
    if content_type not in ("audio/webm", "audio/wav"):
        raise HTTPException(status_code=400, detail="仅支持 audio/webm 或 audio/wav 格式")

    try:
        audio_bytes = await file.read()
    except Exception:
        raise HTTPException(status_code=400, detail="读取音频文件失败")

    if not audio_bytes:
        raise HTTPException(status_code=400, detail="音频文件为空")

    try:
        if llm_config["api_key"]:
            text = await transcribe_audio(
                audio_bytes=audio_bytes,
                content_type=content_type,
                api_key=llm_config["api_key"],
                base_url=llm_config["base_url"],
                model=llm_config["model"],
            )
        else:
            text = await transcribe_audio_hf(
                audio_bytes=audio_bytes,
                content_type=content_type,
            )
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.exception("STT transcription failed")
        raise HTTPException(status_code=500, detail=f"语音转文字失败: {str(e)}")

    return {"text": text}
