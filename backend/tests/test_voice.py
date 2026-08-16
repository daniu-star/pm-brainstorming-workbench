import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.voice_routes import router
from core.config import settings
from core.voice import VoiceServiceUnavailable, _extension_for_content_type, transcribe_speech


class VoiceRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        app = FastAPI()
        app.include_router(router)
        cls.client = TestClient(app)

    def test_capabilities_reports_disabled_stt_without_key(self) -> None:
        with patch.object(settings, "stt_api_key", ""):
            response = self.client.get("/api/voice/capabilities")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["stt_enabled"])
        self.assertIsNone(response.json()["stt_model"])

    def test_tts_rejects_text_above_configured_limit(self) -> None:
        response = self.client.post(
            "/api/voice/tts",
            json={"text": "x" * (settings.tts_max_characters + 1)},
        )

        self.assertEqual(response.status_code, 422)

    def test_stt_rejects_unsupported_content_type(self) -> None:
        response = self.client.post(
            "/api/voice/stt",
            files={"file": ("recording.txt", b"not audio", "text/plain")},
        )

        self.assertEqual(response.status_code, 415)

    def test_stt_rejects_audio_above_limit(self) -> None:
        with patch.object(settings, "stt_max_audio_bytes", 4):
            response = self.client.post(
                "/api/voice/stt",
                files={"file": ("recording.webm", b"12345", "audio/webm")},
            )

        self.assertEqual(response.status_code, 413)


class VoiceServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_transcription_requires_server_configuration(self) -> None:
        with patch.object(settings, "stt_api_key", ""):
            with self.assertRaises(VoiceServiceUnavailable):
                await transcribe_speech(b"audio", "audio/webm")

    async def test_transcription_uses_configured_provider(self) -> None:
        create = AsyncMock(return_value=SimpleNamespace(text="  这是转写结果  "))
        mock_client = SimpleNamespace(
            audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create))
        )

        with (
            patch.object(settings, "stt_api_key", "test-key"),
            patch.object(settings, "stt_base_url", "https://example.invalid/v1"),
            patch("core.voice.AsyncOpenAI", return_value=mock_client),
        ):
            transcript = await transcribe_speech(b"audio", "audio/webm;codecs=opus")

        self.assertEqual(transcript, "这是转写结果")
        create.assert_awaited_once()
        request = create.await_args.kwargs
        self.assertEqual(request["model"], settings.stt_model)
        self.assertEqual(request["language"], settings.stt_language)
        self.assertEqual(request["file"][0], "recording.webm")

    def test_audio_extension_mapping(self) -> None:
        self.assertEqual(_extension_for_content_type("audio/mpeg"), "mp3")
        self.assertEqual(_extension_for_content_type("audio/webm"), "webm")


if __name__ == "__main__":
    unittest.main()
