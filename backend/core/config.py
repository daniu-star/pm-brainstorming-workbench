import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    llm_model: str = os.getenv("LLM_MODEL", "gpt-4o")

    stt_model: str = os.getenv("STT_MODEL", "whisper-1")
    stt_timeout_seconds: float = float(os.getenv("STT_TIMEOUT_SECONDS", "45"))
    stt_max_audio_bytes: int = int(os.getenv("STT_MAX_AUDIO_BYTES", str(12 * 1024 * 1024)))
    tts_timeout_seconds: float = float(os.getenv("TTS_TIMEOUT_SECONDS", "45"))
    tts_max_characters: int = int(os.getenv("TTS_MAX_CHARACTERS", "3000"))

    backend_port: int = int(os.getenv("BACKEND_PORT", "8000"))
    backend_host: str = os.getenv("BACKEND_HOST", "0.0.0.0")

    session_data_dir: str = os.getenv("SESSION_DATA_DIR", "./data/sessions")
    user_data_dir: str = os.getenv("USER_DATA_DIR", "./data/users")
    initial_quota: int = int(os.getenv("INITIAL_QUOTA", "100000"))


settings = Settings()
