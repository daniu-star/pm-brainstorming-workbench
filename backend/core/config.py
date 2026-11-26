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
    # FR-08 预算档位：standard|lean|frugal，只收紧不放宽（见 domain.contracts.BUDGET_PROFILES）
    llm_budget_profile: str = "standard"
    #: BYOK 模式：``byok``（默认）要求每个账户自带模型 Key；``system`` 用 .env 共享 Key
    #: （仅本地联调/内部测试使用，线上部署应保持 byok）。
    llm_key_mode: str = "byok"
    # 推理型模型（deepseek-flash/reasoner 等）会把 completion 预算先花在 reasoning_content 上，
    # 导致结构化输出的 content 为空。默认关闭思考（LLM_DISABLE_REASONING=false 可恢复）。
    llm_disable_reasoning: bool = True

    # 账户级配额（滚动 24 小时）：同一 owner 的累计模型消耗上限，0 表示不限。
    # 与单 Run 预算互补：单 Run 预算防一次性烧太多，账户配额防一个账号反复烧。
    account_daily_token_limit: int = 0
    account_daily_call_limit: int = 0

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

    # Loop Studio 真实执行沙箱：把「开发/测试」阶段的产物真正跑起来。
    # 默认关闭；`loop_sandbox_allowed_commands` 为空时即使 enabled 也不会执行（fail-closed）。
    # 说明：这是**进程级隔离**（独立临时目录、最小环境变量、超时、输出截断），
    # 不是安全边界——只在本地/受信环境开启，不要对外暴露给不可信用户。
    loop_sandbox_enabled: bool = False
    #: 逗号分隔的可执行文件名白名单（例如 ``python,node``）；只匹配 basename。
    loop_sandbox_allowed_commands: str = ""
    loop_sandbox_timeout_seconds: int = 20
    loop_sandbox_max_output_bytes: int = 8192
    #: 沙箱工作目录的父目录；空 → 系统临时目录（每次运行建独立子目录并清理）。
    loop_sandbox_workdir: str = ""

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value):
        """允许构造时传入逗号分隔字符串。"""
        if isinstance(value, str):
            return _parse_cors_origins(value)
        return value


settings = Settings()
