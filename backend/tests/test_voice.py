import asyncio

import pytest
from pydantic import ValidationError

import core.voice as voice
from api.voice_routes import TTSRequest


def test_hf_stt_uses_inference_providers_client(monkeypatch):
    captured = {}

    class FakeOutput:
        text = "  这是一次语音测试  "

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client"] = kwargs

        def automatic_speech_recognition(self, audio, *, model):
            captured["audio"] = audio
            captured["model"] = model
            return FakeOutput()

    monkeypatch.setattr(voice, "HF_API_TOKEN", "test-token")
    monkeypatch.setattr(voice, "InferenceClient", FakeClient)

    text = asyncio.run(
        voice._call_hf_whisper(
            "openai/whisper-large-v3-turbo",
            b"fake-audio",
            "audio/webm",
        )
    )

    assert text == "这是一次语音测试"
    assert captured["client"]["provider"] == "hf-inference"
    assert captured["client"]["api_key"] == "test-token"
    assert captured["client"]["headers"] == {"Content-Type": "audio/webm"}
    assert captured["audio"] == b"fake-audio"
    assert captured["model"] == "openai/whisper-large-v3-turbo"


def test_hf_stt_falls_back_to_second_chinese_model(monkeypatch):
    attempted = []

    async def fake_call(model, audio_bytes, content_type):
        attempted.append(model)
        if len(attempted) == 1:
            raise RuntimeError("primary unavailable")
        return "回退识别成功"

    monkeypatch.setattr(voice, "HF_API_TOKEN", "test-token")
    monkeypatch.setattr(voice, "_call_hf_whisper", fake_call)

    text = asyncio.run(
        voice.transcribe_audio_hf(
            b"fake-audio",
            "audio/webm",
        )
    )

    assert text == "回退识别成功"
    assert attempted == [
        "openai/whisper-large-v3-turbo",
        "openai/whisper-large-v3",
    ]


def test_hf_stt_reports_all_provider_failures(monkeypatch):
    async def always_fail(model, audio_bytes, content_type):
        raise RuntimeError(f"{model} unavailable")

    monkeypatch.setattr(voice, "HF_API_TOKEN", "test-token")
    monkeypatch.setattr(voice, "_call_hf_whisper", always_fail)

    with pytest.raises(RuntimeError, match="所有 HF Whisper 模型均失败"):
        asyncio.run(voice.transcribe_audio_hf(b"fake-audio", "audio/webm"))


def test_tts_pitch_uses_edge_tts_hertz_format():
    request = TTSRequest(text="测试")

    assert request.pitch == "+0Hz"
    assert voice.INTERVIEWER_VOICE_PROFILE["pitch"] == "-2Hz"

    with pytest.raises(ValidationError):
        TTSRequest(text="测试", pitch="+0%")
