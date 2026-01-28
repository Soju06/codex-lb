from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[3]

DEFAULT_HOME_DIR = Path.home() / ".codex-lb"
DEFAULT_DB_PATH = DEFAULT_HOME_DIR / "store.db"
DEFAULT_ENCRYPTION_KEY_FILE = DEFAULT_HOME_DIR / "encryption.key"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CODEX_LB_",
        env_file=(BASE_DIR / ".env", BASE_DIR / ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = f"sqlite+aiosqlite:///{DEFAULT_DB_PATH}"
    upstream_base_url: str = "https://chatgpt.com/backend-api"
    upstream_connect_timeout_seconds: float = 30.0
    stream_idle_timeout_seconds: float = 300.0
    auth_base_url: str = "https://auth.openai.com"
    oauth_client_id: str = "app_EMoamEEZ73f0CkXaXp7hrann"
    oauth_scope: str = "openid profile email"
    oauth_timeout_seconds: float = 30.0
    oauth_redirect_uri: str = "http://localhost:1455/auth/callback"
    oauth_callback_host: str = "127.0.0.1"
    oauth_callback_port: int = 1455  # Do not change the port. OpenAI dislikes changes.
    token_refresh_timeout_seconds: float = 30.0
    token_refresh_interval_days: int = 8
    usage_fetch_timeout_seconds: float = 10.0
    usage_fetch_max_retries: int = 2
    usage_refresh_enabled: bool = True
    usage_refresh_interval_seconds: int = 60
    encryption_key_file: Path = DEFAULT_ENCRYPTION_KEY_FILE
    database_migrations_fail_fast: bool = True
    log_proxy_request_shape: bool = False
    log_proxy_request_shape_raw_cache_key: bool = False
    log_proxy_request_payload: bool = False
    max_decompressed_body_bytes: int = 32 * 1024 * 1024

    @field_validator("database_url")
    @classmethod
    def _normalize_database_url(cls, value: str) -> str:
        if not isinstance(value, str):
            return value

        for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
            if value.startswith(prefix):
                path = value[len(prefix) :]
                if path.startswith("~"):
                    expanded = str(Path(path).expanduser())
                    return f"{prefix}{expanded}"
        return value

    @field_validator("encryption_key_file", mode="before")
    @classmethod
    def _normalize_encryption_key_file(cls, value: object) -> Path:
        if isinstance(value, Path):
            return value.expanduser()
        if isinstance(value, str):
            return Path(value).expanduser()
        raise TypeError("encryption_key_file must be a path")

    @field_validator("max_decompressed_body_bytes")
    @classmethod
    def _validate_max_decompressed_body_bytes(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("max_decompressed_body_bytes must be positive")
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
