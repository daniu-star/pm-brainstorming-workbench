import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 始终从 backend/.env 加载，避免启动目录不同导致配置静默丢失。
# load_dotenv 同时写入 os.environ，保证 os.getenv 直接读取的模块行为不变。
ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(dotenv_path=ENV_FILE)

_DEFAULT_CORS_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:3001",
]


def _parse_cors_origins(raw: str | None) -> list[str]:
    if not raw:
        return list(_DEFAULT_CORS_ORIGINS)
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


class Settings(BaseSettings):
    """全局配置：字段名小写、环境变量大写，pydantic-settings 自动匹配。

    注意：cors_origins 是逗号分隔的环境变量（非 JSON），
    pydantic-settings 对 list 字段默认按 JSON 解析会报错，
    因此该字段通过 validation_alias 屏蔽自动解析，
    由 default_factory 沿用 os.getenv 语义（OS 环境变量优先于 .env）。
    """

    model_config = SettingsConfigDict(env_file=str(ENV_FILE), extra="ignore")

    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o"
    llm_embedding_model: str = "text-embedding-3-small"
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 3

    stt_api_key: str = ""
    stt_base_url: str = "https://api.openai.com/v1"
    stt_model: str = "whisper-1"
    stt_language: str = "zh"
    stt_timeout_seconds: float = 45.0
    stt_max_audio_bytes: int = 12 * 1024 * 1024
    stt_max_recording_seconds: int = 300

    tts_timeout_seconds: float = 30.0
    tts_max_characters: int = 3000

    backend_port: int = 8000
    backend_host: str = "0.0.0.0"
    cors_origins: list[str] = Field(
        default_factory=lambda: _parse_cors_origins(os.getenv("CORS_ORIGINS")),
        validation_alias="_cors_origins_do_not_read_from_env",
    )

    smtp_host: str = "smtp.163.com"
    smtp_port: int = 465
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_use_ssl: bool = True
    smtp_timeout_seconds: float = 20.0

    auth_secret_key: str = ""
    auth_cookie_name: str = "pm_brainstorm_session"
    auth_cookie_secure: bool = False
    auth_cookie_samesite: str = "lax"
    auth_cookie_domain: str = ""
    auth_session_days: int = 7
    auth_code_expire_minutes: int = 10
    auth_code_resend_seconds: int = 60

    chroma_persist_dir: str = "./data/chroma_db"
    session_data_dir: str = "./data/sessions"

    # 四角色并发脑暴之间的间隔（秒），默认 0 表示不等待（B080）。
    agent_role_interval: float = 0.0

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value):
        """允许构造时传入逗号分隔字符串。"""
        if isinstance(value, str):
            return _parse_cors_origins(value)
        return value


settings = Settings()
