from __future__ import annotations

import json
import socket
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


def _default_http_bridge_instance_id() -> str:
    hostname = socket.gethostname().strip()
    return hostname or "codex-lb"


DEFAULT_HOME_DIR = _default_home_dir()
DEFAULT_DB_PATH = DEFAULT_HOME_DIR / "store.db"
DEFAULT_ENCRYPTION_KEY_FILE = DEFAULT_HOME_DIR / "encryption.key"


def _validate_context_window_entries(data: dict) -> dict[str, int]:
    result: dict[str, int] = {}
    for k, v in data.items():
        if isinstance(v, bool):
            raise TypeError(f"model_context_window_overrides value for '{k}' must be a positive integer, got bool")
        if not isinstance(v, int):
            raise TypeError(
                f"model_context_window_overrides value for '{k}' must be a positive integer, got {type(v).__name__}"
            )
        if v <= 0:
            raise ValueError(f"model_context_window_overrides value for '{k}' must be a positive integer, got {v}")
        result[str(k)] = v
    return result


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
    database_sqlite_startup_check_mode: Literal["quick", "full", "off"] = "quick"
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
    oauth_originator: str = "codex_chatgpt_desktop"
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
    openai_cache_affinity_max_age_seconds: int = Field(default=1800, gt=0)
    openai_prompt_cache_key_derivation_enabled: bool = True
    http_responses_session_bridge_enabled: bool = True
    http_responses_session_bridge_idle_ttl_seconds: float = Field(default=120.0, gt=0)
    http_responses_session_bridge_codex_idle_ttl_seconds: float = Field(default=900.0, gt=0)
    http_responses_session_bridge_codex_prewarm_enabled: bool = False
    http_responses_session_bridge_max_sessions: int = Field(default=256, gt=0)
    http_responses_session_bridge_queue_limit: int = Field(default=8, gt=0)
    http_responses_session_bridge_gateway_safe_mode: bool = False
    http_responses_session_bridge_instance_id: str = Field(default_factory=_default_http_bridge_instance_id)
    http_responses_session_bridge_instance_ring: Annotated[list[str], NoDecode] = Field(default_factory=list)
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
    model_context_window_overrides: Annotated[dict[str, int], NoDecode] = Field(default_factory=dict)
    firewall_trust_proxy_headers: bool = False
    firewall_trusted_proxy_cidrs: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["127.0.0.1/32", "::1/128"]
    )

    # --- Multi-replica & production settings ---
    # Prometheus metrics
    metrics_enabled: bool = False
    metrics_port: int = 9090

    # Logging
    log_format: str = "text"  # "text" or "json"

    # Leader election
    leader_election_enabled: bool = False
    leader_election_ttl_seconds: int = 600

    # Circuit breaker
    circuit_breaker_enabled: bool = False
    circuit_breaker_failure_threshold: int = 5
    circuit_breaker_recovery_timeout_seconds: int = 60

    # Soft drain & deterministic failover
    soft_drain_enabled: bool = True
    deterministic_failover_enabled: bool = True
    drain_primary_threshold_pct: float = 85.0
    drain_secondary_threshold_pct: float = 90.0
    drain_error_window_seconds: float = 60.0
    drain_error_count_threshold: int = 2
    probe_quiet_seconds: float = 60.0
    probe_success_streak_required: int = 3

    # Backpressure
    backpressure_max_concurrent_requests: int = 0  # 0 = unlimited

    bulkhead_proxy_limit: int = 200
    bulkhead_dashboard_limit: int = 50
    dashboard_bootstrap_token: str | None = None

    memory_warning_threshold_mb: int = 0
    memory_reject_threshold_mb: int = 0

    # OpenTelemetry
    otel_enabled: bool = False
    otel_exporter_endpoint: str = ""

    # Shutdown drain
    shutdown_drain_timeout_seconds: int = 30

    # HTTP connector limits
    http_connector_limit: int = 100
    http_connector_limit_per_host: int = 50

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

    @field_validator("http_responses_session_bridge_instance_ring", mode="before")
    @classmethod
    def _normalize_http_bridge_instance_ring(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            entries = [entry.strip() for entry in value.split(",")]
            return [entry for entry in entries if entry]
        if isinstance(value, list):
            normalized: list[str] = []
            for entry in value:
                if isinstance(entry, str):
                    instance_id = entry.strip()
                    if instance_id:
                        normalized.append(instance_id)
            return normalized
        raise TypeError("http_responses_session_bridge_instance_ring must be a list or comma-separated string")

    @field_validator("model_context_window_overrides", mode="before")
    @classmethod
    def _parse_model_context_window_overrides(cls, value: object) -> dict[str, int]:
        if value is None:
            return {}
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return {}
            parsed = json.loads(value)
            if not isinstance(parsed, dict):
                raise TypeError("model_context_window_overrides must be a JSON object")
            return _validate_context_window_entries(parsed)
        if isinstance(value, dict):
            return _validate_context_window_entries(value)
        raise TypeError("model_context_window_overrides must be a JSON object string or dict")

    @field_validator("upstream_compact_timeout_seconds")
    @classmethod
    def _validate_upstream_compact_timeout_seconds(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if value <= 0:
            raise ValueError("upstream_compact_timeout_seconds must be greater than zero")
        return value

    @model_validator(mode="after")
    def _validate_http_bridge_instance_configuration(self) -> "Settings":
        ring = self.http_responses_session_bridge_instance_ring
        if ring and self.http_responses_session_bridge_instance_id not in ring:
            raise ValueError(
                "http_responses_session_bridge_instance_id must be explicitly present in "
                "http_responses_session_bridge_instance_ring"
            )
        return self

    @model_validator(mode="after")
    def _validate_metrics_port(self) -> "Settings":
        if self.metrics_port == 2455:
            raise ValueError("metrics_port must not be 2455 (main application port)")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
