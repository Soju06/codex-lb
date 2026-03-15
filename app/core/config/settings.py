from __future__ import annotations

from functools import lru_cache
from ipaddress import ip_network
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
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
DEFAULT_ENCRYPTION_KEY_FILE = DEFAULT_HOME_DIR / "encryption.key"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CODEX_LB_",
        env_file=(BASE_DIR / ".env", BASE_DIR / ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(min_length=1)
    database_migration_url: str | None = None
    database_pool_size: int = Field(default=15, gt=0)
    database_max_overflow: int = Field(default=10, ge=0)
    database_pool_timeout_seconds: float = Field(default=30.0, gt=0)
    database_migrate_on_startup: bool = True
    database_alembic_auto_remap_enabled: bool = True
    upstream_base_url: str = "https://chatgpt.com/backend-api"
    upstream_stream_transport: Literal["http", "websocket", "auto"] = "auto"
    upstream_connect_timeout_seconds: float = 8.0
    upstream_compact_timeout_seconds: float | None = None
    upstream_websocket_trust_env: bool = False
    proxy_request_budget_seconds: float = Field(default=600.0, gt=0)
    compact_request_budget_seconds: float = Field(default=75.0, gt=0)
    stream_idle_timeout_seconds: float = 300.0
    max_sse_event_bytes: int = Field(default=2 * 1024 * 1024, gt=0)
    auth_base_url: str = "https://auth.openai.com"
    oauth_client_id: str = "app_EMoamEEZ73f0CkXaXp7hrann"
    oauth_scope: str = "openid profile email"
    oauth_timeout_seconds: float = 30.0
    oauth_redirect_uri: str = "http://localhost:1455/auth/callback"
    oauth_callback_host: str = _default_oauth_callback_host()
    oauth_callback_port: int = 1455  # Do not change the port. OpenAI dislikes changes.
    token_refresh_timeout_seconds: float = 8.0
    transcription_request_budget_seconds: float = Field(default=120.0, gt=0)
    token_refresh_interval_days: int = 8
    usage_fetch_timeout_seconds: float = 10.0
    usage_fetch_max_retries: int = 2
    usage_refresh_enabled: bool = True
    usage_refresh_interval_seconds: int = Field(default=60, gt=0)
    openai_cache_affinity_max_age_seconds: int = Field(default=300, gt=0)
    sticky_session_cleanup_enabled: bool = True
    sticky_session_cleanup_interval_seconds: int = Field(default=300, gt=0)
    encryption_key_file: Path = DEFAULT_ENCRYPTION_KEY_FILE
    database_migrations_fail_fast: bool = True
    log_proxy_request_shape: bool = False
    log_proxy_request_shape_raw_cache_key: bool = False
    log_proxy_request_payload: bool = False
    log_proxy_service_tier_trace: bool = False
    log_upstream_request_summary: bool = False
    log_upstream_request_payload: bool = False
    max_decompressed_body_bytes: int = Field(default=32 * 1024 * 1024, gt=0)
    image_inline_fetch_enabled: bool = True
    image_inline_allowed_hosts: Annotated[list[str], NoDecode] = Field(default_factory=list)
    model_registry_enabled: bool = True
    model_registry_refresh_interval_seconds: int = Field(default=300, gt=0)
    model_registry_client_version: str = "0.101.0"
    firewall_trust_proxy_headers: bool = False
    firewall_trusted_proxy_cidrs: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["127.0.0.1/32", "::1/128"]
    )

    @field_validator("database_url", "database_migration_url")
    @classmethod
    def _normalize_database_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("database url must not be empty")
        return value

    @model_validator(mode="after")
    def _finalize_database_urls(self) -> Settings:
        if self.database_migration_url is None:
            self.database_migration_url = self.database_url
        return self

    @field_validator("encryption_key_file", mode="before")
    @classmethod
    def _expand_encryption_key_file(cls, value: str | Path) -> Path:
        if isinstance(value, Path):
            return value.expanduser()
        if isinstance(value, str):
            return Path(value).expanduser()
        raise TypeError("encryption_key_file must be a path")

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

    @field_validator("firewall_trusted_proxy_cidrs", mode="before")
    @classmethod
    def _normalize_firewall_trusted_proxy_cidrs(cls, value: object) -> list[str]:
        if value is None:
            return []
        cidrs: list[str] = []
        if isinstance(value, str):
            entries = [entry.strip() for entry in value.split(",")]
            cidrs = [entry for entry in entries if entry]
        elif isinstance(value, list):
            for entry in value:
                if isinstance(entry, str):
                    cidr = entry.strip()
                    if cidr:
                        cidrs.append(cidr)
        else:
            raise TypeError("firewall_trusted_proxy_cidrs must be a list or comma-separated string")

        for cidr in cidrs:
            try:
                ip_network(cidr, strict=False)
            except ValueError as exc:
                raise ValueError(f"Invalid firewall trusted proxy CIDR: {cidr}") from exc
        return cidrs

    @field_validator("upstream_compact_timeout_seconds")
    @classmethod
    def _validate_upstream_compact_timeout_seconds(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if value <= 0:
            raise ValueError("upstream_compact_timeout_seconds must be greater than zero")
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    if settings.database_migrate_on_startup and not settings.database_migration_url:
        raise RuntimeError(
            "CODEX_LB_DATABASE_MIGRATION_URL is required when database migrations on startup are enabled"
        )
    return settings
