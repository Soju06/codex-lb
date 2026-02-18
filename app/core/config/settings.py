from __future__ import annotations

from functools import lru_cache
from ipaddress import ip_network
from pathlib import Path
from typing import Annotated

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
    firewall_trust_proxy_headers: bool = False
    firewall_trusted_proxy_cidrs: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["127.0.0.1/32", "::1/128"]
    )

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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
