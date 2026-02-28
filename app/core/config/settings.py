from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[3]

DOCKER_DATA_DIR = Path("/var/lib/codex-lb")
DOCKER_CALLBACK_HOST = "0.0.0.0"


def _in_container() -> bool:
    return Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()


def _default_home_dir() -> Path:
    if _in_container():
        return DOCKER_DATA_DIR
    return Path.home() / ".codex-lb"


def _default_oauth_callback_host() -> str:
    if _in_container():
        return DOCKER_CALLBACK_HOST
    return "127.0.0.1"


DEFAULT_HOME_DIR = _default_home_dir()
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
    database_pool_size: int = Field(default=15, gt=0)
    database_max_overflow: int = Field(default=10, ge=0)
    database_pool_timeout_seconds: float = Field(default=30.0, gt=0)
    database_migrate_on_startup: bool = True
    database_sqlite_pre_migrate_backup_enabled: bool = True
    database_sqlite_pre_migrate_backup_max_files: int = Field(default=5, ge=1)
    upstream_base_url: str = "https://chatgpt.com/backend-api"
    upstream_connect_timeout_seconds: float = 30.0
    stream_idle_timeout_seconds: float = 300.0
    max_sse_event_bytes: int = Field(default=2 * 1024 * 1024, gt=0)
    anthropic_api_base_url: str = "https://api.anthropic.com"
    anthropic_api_version: str = "2023-06-01"
    anthropic_api_beta: str | None = None
    anthropic_api_timeout_seconds: float = 300.0
    anthropic_api_detect_cli_headers: bool = True
    anthropic_api_system_prompt_injection_mode: Literal["none", "minimal", "full"] = "minimal"
    anthropic_oauth_token_url: str = "https://console.anthropic.com/v1/oauth/token"
    anthropic_oauth_client_id: str = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
    anthropic_sdk_cli_path: str | None = None
    anthropic_sdk_default_session_id: str | None = None
    anthropic_usage_base_url: str = "https://api.anthropic.com"
    anthropic_usage_beta: str = "oauth-2025-04-20"
    anthropic_usage_refresh_enabled: bool = True
    anthropic_usage_bearer_token: str | None = None
    anthropic_org_id: str | None = None
    anthropic_auto_discover_org: bool = False
    anthropic_credentials_discovery_enabled: bool = True
    anthropic_credentials_file: Path | None = None
    anthropic_credentials_helper_command: str | None = None
    anthropic_credentials_cache_seconds: int = Field(default=60, ge=0)
    anthropic_default_account_id: str = "anthropic_default"
    anthropic_default_account_email: str = "anthropic@local"
    anthropic_default_plan_type: str = "pro"
    auth_base_url: str = "https://auth.openai.com"
    oauth_client_id: str = "app_EMoamEEZ73f0CkXaXp7hrann"
    oauth_scope: str = "openid profile email"
    oauth_timeout_seconds: float = 30.0
    oauth_redirect_uri: str = "http://localhost:1455/auth/callback"
    oauth_callback_host: str = _default_oauth_callback_host()
    oauth_callback_port: int = 1455  # Do not change the port. OpenAI dislikes changes.
    token_refresh_timeout_seconds: float = 30.0
    token_refresh_interval_days: int = 8
    usage_fetch_timeout_seconds: float = 10.0
    usage_fetch_max_retries: int = 2
    usage_refresh_enabled: bool = True
    usage_refresh_interval_seconds: int = Field(default=60, gt=0)
    encryption_key_file: Path = DEFAULT_ENCRYPTION_KEY_FILE
    database_migrations_fail_fast: bool = True
    log_proxy_request_shape: bool = False
    log_proxy_request_shape_raw_cache_key: bool = False
    log_proxy_request_payload: bool = False
    max_decompressed_body_bytes: int = Field(default=32 * 1024 * 1024, gt=0)
    image_inline_fetch_enabled: bool = True
    image_inline_allowed_hosts: Annotated[list[str], NoDecode] = Field(default_factory=list)
    model_registry_enabled: bool = True
    model_registry_refresh_interval_seconds: int = Field(default=300, gt=0)
    model_registry_client_version: str = "0.101.0"
    embeddings_enabled: bool = False
    embeddings_upstream_url: str = ""
    embeddings_upstream_api_key: str | None = None
    embeddings_upstream_timeout_seconds: float = 30.0
    embeddings_model_override: str | None = None
    response_context_ttl_seconds: int = Field(default=6 * 60 * 60, gt=0)
    response_context_max_responses: int = Field(default=1024, gt=0)
    response_context_max_items: int = Field(default=4096, gt=0)
    response_context_enable_durable: bool = True
    response_context_global_fallback_enabled: bool = False
    response_context_cleanup_enabled: bool = True
    response_context_cleanup_interval_seconds: int = Field(default=300, gt=0)

    @field_validator("database_url")
    @classmethod
    def _expand_database_url(cls, value: str) -> str:
        for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
            if value.startswith(prefix):
                path = value[len(prefix) :]
                if path.startswith("~"):
                    return f"{prefix}{Path(path).expanduser()}"
        return value

    @field_validator("encryption_key_file", mode="before")
    @classmethod
    def _expand_encryption_key_file(cls, value: str | Path) -> Path:
        if isinstance(value, Path):
            return value.expanduser()
        if isinstance(value, str):
            return Path(value).expanduser()
        raise TypeError("encryption_key_file must be a path")

    @field_validator("anthropic_credentials_file", mode="before")
    @classmethod
    def _expand_anthropic_credentials_file(cls, value: str | Path | None) -> Path | None:
        if value is None:
            return None
        if isinstance(value, Path):
            return value.expanduser()
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return None
            return Path(stripped).expanduser()
        raise TypeError("anthropic_credentials_file must be a path")

    @field_validator("image_inline_allowed_hosts", mode="before")
    @classmethod
    def _normalize_image_inline_allowed_hosts(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            entries = [entry.strip().lower().rstrip(".") for entry in value.split(",")]
            return [entry for entry in entries if entry]
        if isinstance(value, list):
            normalized: list[str] = []
            for entry in value:
                if isinstance(entry, str):
                    host = entry.strip().lower().rstrip(".")
                    if host:
                        normalized.append(host)
            return normalized
        raise TypeError("image_inline_allowed_hosts must be a list or comma-separated string")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
