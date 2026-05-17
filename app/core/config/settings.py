from __future__ import annotations

import json
import os
import socket
from collections.abc import Mapping
from functools import lru_cache
from ipaddress import ip_address, ip_network
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.core.auth.dashboard_mode import DashboardAuthMode, normalize_dashboard_auth_proxy_header

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
type StringListInput = str | list[str] | None
type OptionalStringInput = str | None
type ModelContextWindowOverridesInput = str | dict[str, int] | None


def _validate_context_window_entries(data: Mapping[str, object]) -> dict[str, int]:
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


def _parse_port_value(raw: str) -> int | None:
    try:
        port = int(raw)
    except ValueError:
        return None
    if port <= 0:
        return None
    return port


def _configured_http_port() -> int:
    raw_env_port = os.getenv("PORT")
    if raw_env_port is not None:
        parsed_env_port = _parse_port_value(raw_env_port.strip())
        if parsed_env_port is not None:
            return parsed_env_port
    return 2455


def _normalize_cidr_list(value: StringListInput, *, field_name: str, invalid_label: str) -> list[str]:
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
        raise TypeError(f"{field_name} must be a list or comma-separated string")

    for cidr in cidrs:
        try:
            ip_network(cidr, strict=False)
        except ValueError as exc:
            raise ValueError(f"Invalid {invalid_label}: {cidr}") from exc
    return cidrs


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
    database_background_pool_size: int | None = Field(default=None, gt=0)
    database_background_max_overflow: int | None = Field(default=None, ge=0)
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
    sse_keepalive_interval_seconds: float = Field(default=10.0, ge=0)
    proxy_downstream_websocket_idle_timeout_seconds: float = Field(default=120.0, gt=0)
    # Applies to both upstream SSE event buffering and upstream websocket message
    # frames. Keep the default aligned with the common 16 MiB websocket ceiling so
    # large built-in tool payloads (for example image_generation outputs) do not
    # fail locally with a 1009 before upstream completion.
    max_sse_event_bytes: int = Field(default=16 * 1024 * 1024, gt=0)
    upstream_response_create_max_bytes: int = Field(default=15 * 1024 * 1024, gt=0)
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
    http_responses_session_bridge_advertise_base_url: str | None = None
    sticky_session_cleanup_enabled: bool = True
    sticky_session_cleanup_interval_seconds: int = Field(default=300, gt=0)
    automations_scheduler_enabled: bool = True
    automations_scheduler_interval_seconds: int = Field(default=30, gt=0)
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
    # OpenAI Images API compatibility (POST /v1/images/{generations,edits})
    # ``images_host_model`` is the internal Responses model used to invoke the
    # built-in ``image_generation`` tool. It is never echoed to clients.
    # ``images_default_model`` is the public model returned to clients when
    # they omit ``model``; it must remain in the ``gpt-image-*`` family.
    images_host_model: str = "gpt-5.5"
    images_default_model: str = "gpt-image-2"
    images_max_partial_images: int = Field(default=3, ge=0, le=3)
    # NOTE: there is intentionally no ``images_max_n`` setting. The
    # upstream ``image_generation`` tool path accepts only a single
    # image per call and codex-lb does not yet implement client-side
    # fan-out, so ``n > 1`` is hard-rejected at the API boundary. The
    # cap is lifted in the same change that introduces fan-out.
    model_registry_enabled: bool = True
    model_registry_refresh_interval_seconds: int = Field(default=300, gt=0)
    model_registry_client_version: str = "0.101.0"
    model_context_window_overrides: Annotated[dict[str, int], NoDecode] = Field(default_factory=dict)
    proxy_unauthenticated_client_cidrs: Annotated[list[str], NoDecode] = Field(default_factory=list)
    firewall_trust_proxy_headers: bool = False
    firewall_trusted_proxy_cidrs: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["127.0.0.1/32", "::1/128"]
    )
    dashboard_auth_mode: DashboardAuthMode = DashboardAuthMode.STANDARD
    dashboard_auth_proxy_header: str = "Remote-User"

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

    bulkhead_proxy_limit: int = Field(default=512, ge=0)
    bulkhead_proxy_http_limit: int | None = Field(default=None, ge=0)
    bulkhead_proxy_websocket_limit: int | None = Field(default=None, ge=0)
    bulkhead_proxy_compact_limit: int | None = Field(default=None, ge=0)
    bulkhead_dashboard_limit: int = Field(default=50, ge=0)
    dashboard_bootstrap_token: str | None = None
    proxy_token_refresh_limit: int = Field(default=64, ge=0)
    proxy_upstream_websocket_connect_limit: int = Field(default=128, ge=0)
    proxy_response_create_limit: int = Field(default=256, ge=0)
    proxy_compact_response_create_limit: int = Field(default=64, ge=0)
    proxy_admission_wait_timeout_seconds: float = Field(default=10.0, gt=0)
    proxy_refresh_failure_cooldown_seconds: float = Field(default=5.0, ge=0.0)
    usage_refresh_auth_failure_cooldown_seconds: float = Field(default=300.0, ge=0.0)

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
    def _normalize_image_inline_allowed_hosts(cls, value: StringListInput) -> list[str]:
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
    def _normalize_firewall_trusted_proxy_cidrs(cls, value: StringListInput) -> list[str]:
        return _normalize_cidr_list(
            value,
            field_name="firewall_trusted_proxy_cidrs",
            invalid_label="firewall trusted proxy CIDR",
        )

    @field_validator("proxy_unauthenticated_client_cidrs", mode="before")
    @classmethod
    def _normalize_proxy_unauthenticated_client_cidrs(cls, value: StringListInput) -> list[str]:
        return _normalize_cidr_list(
            value,
            field_name="proxy_unauthenticated_client_cidrs",
            invalid_label="proxy unauthenticated client CIDR",
        )

    @field_validator("dashboard_auth_proxy_header", mode="before")
    @classmethod
    def _normalize_dashboard_auth_proxy_header(cls, value: object) -> str:
        if not isinstance(value, str):
            raise TypeError("dashboard_auth_proxy_header must be a string")
        return normalize_dashboard_auth_proxy_header(value)

    @field_validator("http_responses_session_bridge_instance_ring", mode="before")
    @classmethod
    def _normalize_http_bridge_instance_ring(cls, value: StringListInput) -> list[str]:
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

    @field_validator("http_responses_session_bridge_advertise_base_url", mode="before")
    @classmethod
    def _normalize_http_bridge_advertise_base_url(cls, value: OptionalStringInput) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip().rstrip("/")
            return stripped or None
        raise TypeError("http_responses_session_bridge_advertise_base_url must be a string")

    @field_validator("model_context_window_overrides", mode="before")
    @classmethod
    def _parse_model_context_window_overrides(cls, value: ModelContextWindowOverridesInput) -> dict[str, int]:
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
        advertise_base_url = self.http_responses_session_bridge_advertise_base_url
        if advertise_base_url is not None:
            hostname = urlparse(advertise_base_url).hostname
            if hostname is None:
                raise ValueError("http_responses_session_bridge_advertise_base_url must include a valid hostname")
            if not _bridge_advertise_hostname_is_replica_specific(
                hostname,
                instance_id=self.http_responses_session_bridge_instance_id,
                multi_replica_intent=len(ring) > 1,
            ):
                raise ValueError(
                    "http_responses_session_bridge_advertise_base_url must be replica-specific for bridge routing"
                )
        return self

    @model_validator(mode="after")
    def _normalize_bulkhead_limits(self) -> "Settings":
        if self.bulkhead_proxy_http_limit is None:
            self.bulkhead_proxy_http_limit = self.bulkhead_proxy_limit
        if self.bulkhead_proxy_websocket_limit is None:
            self.bulkhead_proxy_websocket_limit = self.bulkhead_proxy_limit
        if self.bulkhead_proxy_compact_limit is None:
            http_limit = self.bulkhead_proxy_http_limit
            self.bulkhead_proxy_compact_limit = 0 if http_limit <= 0 else min(http_limit, 16)
        return self

    @model_validator(mode="after")
    def _validate_metrics_port(self) -> "Settings":
        http_port = _configured_http_port()
        if self.metrics_port == http_port:
            raise ValueError(f"metrics_port must not match the main application port ({http_port})")
        return self

    @model_validator(mode="after")
    def _validate_dashboard_auth_mode(self) -> "Settings":
        if self.dashboard_auth_mode != DashboardAuthMode.TRUSTED_HEADER:
            return self
        if not self.firewall_trust_proxy_headers:
            raise ValueError("dashboard_auth_mode=trusted_header requires firewall_trust_proxy_headers=true")
        if not self.firewall_trusted_proxy_cidrs:
            raise ValueError("dashboard_auth_mode=trusted_header requires non-empty firewall_trusted_proxy_cidrs")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def _bridge_advertise_hostname_is_replica_specific(
    hostname: str,
    *,
    instance_id: str,
    multi_replica_intent: bool = False,
) -> bool:
    pod_ip = os.getenv("POD_IP")
    if pod_ip and hostname == pod_ip:
        return True
    try:
        parsed_ip = ip_address(hostname)
    except ValueError:
        labels = set(hostname.split("."))
        pod_name = os.getenv("POD_NAME", "").strip()
        host_name = os.getenv("HOSTNAME", "").strip()
        allowed_labels = {
            label
            for label in {
                instance_id.strip(),
                pod_name,
                host_name,
                socket.gethostname().strip(),
            }
            if label
        }
        return bool(labels & allowed_labels)
    return parsed_ip.is_loopback and not multi_replica_intent
