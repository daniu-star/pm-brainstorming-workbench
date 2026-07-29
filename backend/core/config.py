import os
from pathlib import Path
from dotenv import load_dotenv

# 始终从 backend/.env 加载，避免启动目录不同导致配置静默丢失。
ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(dotenv_path=ENV_FILE)


class Settings:
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    llm_model: str = os.getenv("LLM_MODEL", "gpt-4o")
    llm_max_output_tokens: int = max(
        256,
        min(8000, int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "2500"))),
    )

    stt_model: str = os.getenv("STT_MODEL", "whisper-1")
    stt_timeout_seconds: float = float(os.getenv("STT_TIMEOUT_SECONDS", "45"))
    stt_max_audio_bytes: int = int(os.getenv("STT_MAX_AUDIO_BYTES", str(12 * 1024 * 1024)))
    tts_timeout_seconds: float = float(os.getenv("TTS_TIMEOUT_SECONDS", "45"))
    tts_max_characters: int = int(os.getenv("TTS_MAX_CHARACTERS", "3000"))

    backend_port: int = int(os.getenv("BACKEND_PORT", "8000"))
    backend_host: str = os.getenv("BACKEND_HOST", "0.0.0.0")

    session_data_dir: str = os.getenv("SESSION_DATA_DIR", "./data/sessions")
    user_data_dir: str = os.getenv("USER_DATA_DIR", "./data/users")
    team_data_dir: str = os.getenv("TEAM_DATA_DIR", "./data/teams")
    initial_quota: int = int(os.getenv("INITIAL_QUOTA", "100000"))
    guest_initial_quota: int = max(
        1000,
        min(50000, int(os.getenv("GUEST_INITIAL_QUOTA", "20000"))),
    )
    allow_anonymous_tokens: bool = os.getenv(
        "ALLOW_ANONYMOUS_TOKENS", "false"
    ).lower() in ("1", "true", "yes")
    allow_sms_code_echo: bool = os.getenv(
        "ALLOW_SMS_CODE_ECHO", "false"
    ).lower() in ("1", "true", "yes")
    smtp_host: str = os.getenv("SMTP_HOST", "")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_username: str = os.getenv("SMTP_USERNAME", "")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    smtp_from_email: str = os.getenv("SMTP_FROM_EMAIL", "")
    smtp_from_name: str = os.getenv("SMTP_FROM_NAME", "产品脑暴工作台")
    smtp_use_tls: bool = os.getenv("SMTP_USE_TLS", "true").lower() in ("1", "true", "yes")
    smtp_use_ssl: bool = os.getenv("SMTP_USE_SSL", "false").lower() in ("1", "true", "yes")
    email_proxy_url: str = os.getenv("VERCEL_EMAIL_PROXY_URL", "")
    email_proxy_key: str = os.getenv("VERCEL_EMAIL_PROXY_KEY", "")
    frontend_base_url: str = os.getenv("FRONTEND_BASE_URL", "https://www.brainstorming.top").rstrip("/")
    team_invite_expiry_hours: int = max(
        1,
        min(168, int(os.getenv("TEAM_INVITE_EXPIRY_HOURS", "72"))),
    )
    cors_allowed_origins: list[str] = [
        origin.strip()
        for origin in os.getenv(
            "CORS_ALLOWED_ORIGINS",
            "https://www.brainstorming.top,https://brainstorming.top,http://localhost:3000,http://127.0.0.1:3000,http://127.0.0.1:3100",
        ).split(",")
        if origin.strip()
    ]


settings = Settings()
