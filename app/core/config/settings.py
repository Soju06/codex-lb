from __future__ import annotations

import json
import logging
import os
import socket
from collections.abc import Mapping
from functools import cached_property, lru_cache
from ipaddress import ip_address, ip_network
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlparse

from dotenv import dotenv_values
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.core.auth.dashboard_mode import DashboardAuthMode, normalize_dashboard_auth_proxy_header
from app.core.utils.proxy_env import outbound_proxy_env_configured

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[3]
ENV_FILES = (BASE_DIR / ".env", BASE_DIR / ".env.local")

# OAuth protocol constants. These values identify codex-lb to OpenAI's OAuth
# endpoints exactly like the Codex CLI; they are protocol constants, not
# deployment tunables, and changing any of them breaks login
# (PRINCIPLES.md P2, issue #1340).
AUTH_BASE_URL = "https://auth.openai.com"
OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OAUTH_ORIGINATOR = "codex_chatgpt_desktop"
OAUTH_SCOPE = "openid profile email"
OAUTH_REDIRECT_URI = "http://localhost:1455/auth/callback"
OAUTH_CALLBACK_PORT = 1455  # Do not change the port. OpenAI dislikes changes.

# Env names of settings removed from the Settings surface (issue #1340,
# PRINCIPLES.md P2). ``extra="ignore"`` already makes them harmless; startup
# emits one WARN for one release as a courtesy to operators who still set them.
_REMOVED_SETTINGS: tuple[str, ...] = (
    # Phase 1 (reduce-settings-surface-phase-1)
    "CODEX_LB_AUTH_BASE_URL",
    "CODEX_LB_OAUTH_CLIENT_ID",
    "CODEX_LB_OAUTH_ORIGINATOR",
    "CODEX_LB_OAUTH_SCOPE",
    "CODEX_LB_OAUTH_REDIRECT_URI",
    "CODEX_LB_OAUTH_CALLBACK_PORT",
    "CODEX_LB_AUTH_GUARDIAN_INTERVAL_SECONDS",
    "CODEX_LB_AUTH_GUARDIAN_MAX_REFRESH_AGE_SECONDS",
    "CODEX_LB_AUTH_GUARDIAN_BATCH_SIZE",
    "CODEX_LB_AUTH_GUARDIAN_CONCURRENCY",
    "CODEX_LB_AUTH_GUARDIAN_JITTER_SECONDS",
    "CODEX_LB_AUTH_GUARDIAN_FAILURE_BACKOFF_BASE_SECONDS",
    "CODEX_LB_AUTH_GUARDIAN_FAILURE_BACKOFF_MAX_SECONDS",
    "CODEX_LB_LOG_PROXY_REQUEST_SHAPE",
    "CODEX_LB_LOG_PROXY_REQUEST_SHAPE_RAW_CACHE_KEY",
    "CODEX_LB_LOG_PROXY_REQUEST_PAYLOAD",
    "CODEX_LB_LOG_PROXY_SERVICE_TIER_TRACE",
    "CODEX_LB_LOG_UPSTREAM_REQUEST_SUMMARY",
    "CODEX_LB_LOG_UPSTREAM_REQUEST_PAYLOAD",
    "CODEX_LB_BULKHEAD_PROXY_HTTP_LIMIT",
    "CODEX_LB_BULKHEAD_PROXY_WEBSOCKET_LIMIT",
    "CODEX_LB_BULKHEAD_PROXY_COMPACT_LIMIT",
    "CODEX_LB_TOKEN_REFRESH_CLAIM_WAIT_SECONDS",
    "CODEX_LB_TOKEN_REFRESH_CLAIM_POLL_SECONDS",
    # Phase 2 (reduce-settings-surface-phase-2)
    "CODEX_LB_QUOTA_PLANNER_TICK_SECONDS",
    "CODEX_LB_AUTOMATIONS_SCHEDULER_INTERVAL_SECONDS",
    "CODEX_LB_MODEL_REGISTRY_REFRESH_INTERVAL_SECONDS",
    "CODEX_LB_STICKY_SESSION_CLEANUP_INTERVAL_SECONDS",
    "CODEX_LB_CODEX_FINGERPRINT_OS",
    "CODEX_LB_CODEX_FINGERPRINT_ARCH",
    "CODEX_LB_CODEX_FINGERPRINT_TERMINAL",
    "CODEX_LB_LIVE_USAGE_WRITE_MIN_INTERVAL_SECONDS",
    "CODEX_LB_LIVE_USAGE_QUEUE_SIZE",
    "CODEX_LB_REQUEST_LOG_COUNT_CACHE_TTL_SECONDS",
    "CODEX_LB_CIRCUIT_BREAKER_FAILURE_THRESHOLD",
    "CODEX_LB_CIRCUIT_BREAKER_RECOVERY_TIMEOUT_SECONDS",
    "CODEX_LB_MEMORY_WARNING_THRESHOLD_MB",
    "CODEX_LB_IMAGES_HOST_MODEL",
    "CODEX_LB_IMAGES_MAX_PARTIAL_IMAGES",
)


def warn_removed_settings(environ: Mapping[str, str] | None = None) -> list[str]:
    """Log one WARN listing removed ``CODEX_LB_*`` env vars still set.

    Scans the process environment plus the same env files Settings loads
    (``ENV_FILES``), so removed names lingering in ``.env``/``.env.local``
    are reported too. Returns the removed names found so the startup caller
    and tests share one source of truth. Values are never logged.
    """
    if environ is None:
        source: Mapping[str, str | None] = _effective_environ()
    else:
        source = environ
    found = [name for name in _REMOVED_SETTINGS if name in source]
    if found:
        logger.warning(
            "removed setting(s) ignored: %s — values are now fixed; see PRINCIPLES.md P2 / issue #1340",
            ", ".join(found),
        )
    return found


DOCKER_DATA_DIR = Path("/var/lib/codex-lb")
DOCKER_CALLBACK_HOST = "0.0.0.0"


def _in_container() -> bool:
    return Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()


def _default_home_dir() -> Path:
    env_dir = os.getenv("CODEX_LB_DATA_DIR")
    if env_dir and env_dir.strip():
        return Path(env_dir.strip())
    home_dir = Path.home() / ".codex-lb"
    if home_dir.exists():
        return home_dir
    if _in_container():
        return DOCKER_DATA_DIR
    return home_dir


def _default_oauth_callback_host() -> str:
    if _in_container():
        return DOCKER_CALLBACK_HOST
    return "127.0.0.1"


def _default_http_bridge_instance_id() -> str:
    hostname = socket.gethostname().strip()
    return hostname or "codex-lb"


def _default_upstream_websocket_trust_env() -> bool:
    return outbound_proxy_env_configured(_effective_environ())


def _effective_environ() -> dict[str, str | None]:
    environ: dict[str, str | None] = {}
    for env_file in ENV_FILES:
        environ.update(dotenv_values(env_file))
    environ.update(os.environ)
    return environ


DEFAULT_HOME_DIR = _default_home_dir()
DEFAULT_DB_PATH = DEFAULT_HOME_DIR / "store.db"
DEFAULT_ENCRYPTION_KEY_FILE = DEFAULT_HOME_DIR / "encryption.key"
DEFAULT_CONVERSATION_ARCHIVE_DIR = DEFAULT_HOME_DIR / "conversation-archive"
DEFAULT_DATABASE_URL = f"sqlite+aiosqlite:///{DEFAULT_DB_PATH}"
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
        env_file=ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_dir: Path = Field(default_factory=_default_home_dir)
    database_url: str = DEFAULT_DATABASE_URL
    database_pool_size: int = Field(default=15, gt=0)
    database_max_overflow: int = Field(default=10, ge=0)
    database_background_pool_size: int | None = Field(default=None, gt=0)
    database_background_max_overflow: int | None = Field(default=None, ge=0)
    database_pool_timeout_seconds: float = Field(default=30.0, gt=0)
    database_pool_recycle_seconds: int = Field(default=1800, gt=0)
    database_migrate_on_startup: bool = True
    database_sqlite_pre_migrate_backup_enabled: bool = True
    database_sqlite_pre_migrate_backup_max_files: int = Field(default=5, ge=1)
    database_sqlite_startup_check_mode: Literal["quick", "full", "off"] = "quick"
    database_alembic_auto_remap_enabled: bool = True
    database_migration_lock_timeout_seconds: float = Field(default=300.0, gt=0)
    upstream_base_url: str = "https://chatgpt.com/backend-api"
    upstream_stream_transport: Literal["http", "websocket", "auto"] = "auto"
    http_downstream_transport_policy: Literal["smart", "always_http", "always_websocket", "pinned"] = "smart"
    upstream_connect_timeout_seconds: float = 8.0
    upstream_compact_timeout_seconds: float | None = None
    upstream_websocket_trust_env: bool = Field(default_factory=_default_upstream_websocket_trust_env)
    proxy_request_budget_seconds: float = Field(default=600.0, gt=0)
    http_responses_stream_request_budget_seconds: float = Field(default=7200.0, gt=0)
    compact_request_budget_seconds: float = Field(default=180.0, gt=0)
    stream_idle_timeout_seconds: float = Field(default=7200.0, gt=0)
    sse_keepalive_interval_seconds: float = Field(default=10.0, ge=0)
    proxy_downstream_websocket_idle_timeout_seconds: float = Field(default=120.0, gt=0)
    # Applies to both upstream SSE event buffering and upstream websocket message
    # frames. Keep the default aligned with the common 16 MiB websocket ceiling so
    # large built-in tool payloads (for example image_generation outputs) do not
    # fail locally with a 1009 before upstream completion.
    max_sse_event_bytes: int = Field(default=16 * 1024 * 1024, gt=0)
    upstream_response_create_max_bytes: int = Field(default=15 * 1024 * 1024, gt=0)
    oauth_timeout_seconds: float = 30.0
    oauth_callback_host: str = _default_oauth_callback_host()
    token_refresh_timeout_seconds: float = 8.0
    # Cross-replica token-refresh claim (account_refresh_claims table).
    # The TTL bounds how long a crashed claimant can block refresh for one
    # account; it is validated to stay >= proxy_admission_wait_timeout_seconds
    # + 2x token_refresh_timeout_seconds because the claim is held across the
    # refresh-admission wait AND the OAuth exchange, and a healthy claimant
    # must not lose its claim mid-work.
    token_refresh_claim_ttl_seconds: float = Field(default=30.0, gt=0)
    auth_guardian_enabled: bool = False
    transcription_request_budget_seconds: float = Field(default=120.0, gt=0)
    token_refresh_interval_days: int = 8
    usage_fetch_timeout_seconds: float = 10.0
    usage_fetch_max_retries: int = 2
    usage_refresh_enabled: bool = True
    usage_refresh_interval_seconds: int = Field(default=60, gt=0)
    live_usage_ingestion_enabled: bool = True
    rate_limit_reset_credits_refresh_interval_seconds: int = Field(default=60, gt=0)
    openai_cache_affinity_max_age_seconds: int = Field(default=1800, gt=0)
    warmup_model: str = "gpt-5.4-mini"
    openai_prompt_cache_key_derivation_enabled: bool = True
    http_responses_session_bridge_enabled: bool = True
    http_responses_session_bridge_request_budget_seconds: float = Field(default=7200.0, gt=0)
    http_responses_session_bridge_idle_ttl_seconds: float = Field(default=120.0, gt=0)
    http_responses_session_bridge_codex_idle_ttl_seconds: float = Field(default=900.0, gt=0)
    http_responses_session_bridge_codex_prewarm_enabled: bool = False
    http_responses_session_bridge_codex_prewarm_canary_percent: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
    )
    http_responses_session_bridge_codex_prewarm_allow_api_key_ids: Annotated[list[str], NoDecode] = Field(
        default_factory=list
    )
    http_responses_session_bridge_codex_prewarm_deny_api_key_ids: Annotated[list[str], NoDecode] = Field(
        default_factory=list
    )
    http_responses_session_bridge_stuck_gate_retire_after_seconds: float = Field(default=300.0, gt=0)
    http_responses_session_bridge_max_sessions: int = Field(default=256, gt=0)
    http_responses_session_bridge_queue_limit: int = Field(default=8, gt=0)
    http_responses_session_bridge_gateway_safe_mode: bool = False
    http_responses_session_bridge_instance_id: str = Field(default_factory=_default_http_bridge_instance_id)
    http_responses_session_bridge_instance_ring: Annotated[list[str], NoDecode] = Field(default_factory=list)
    http_responses_session_bridge_advertise_base_url: str | None = None
    sticky_session_cleanup_enabled: bool = True
    # Data retention (0 = disabled). Non-zero values have safety floors so
    # every in-product consumer window stays inside retained data.
    request_log_retention_days: int = Field(default=0, ge=0, le=3650)
    usage_history_retention_days: int = Field(default=0, ge=0, le=3650)
    quota_planner_scheduler_enabled: bool = True
    automations_scheduler_enabled: bool = True
    encryption_key_file: Path = DEFAULT_ENCRYPTION_KEY_FILE
    # Startup cross-replica encryption-key consistency check against the shared
    # database sentinel: "enforce" refuses startup on mismatch, "warn" logs an
    # ERROR and continues, "off" disables the check.
    encryption_key_fingerprint_mode: Literal["enforce", "warn", "off"] = "enforce"
    database_migrations_fail_fast: bool = True
    # Incident-debugging trace channels (env ``CODEX_LB_TRACE``), a
    # comma-separated list. Empty (the default) disables all trace logging.
    # Channels: ``shape`` (request shape), ``shape_raw_cache_key`` (include the
    # raw prompt cache key in shape logs), ``payload`` (downstream request
    # payload), ``service_tier`` (service-tier trace), ``upstream_summary``
    # (upstream request summary/completion), ``upstream_payload`` (upstream
    # request payload). Interactive incident use only, not steady-state config.
    trace: str = ""
    conversation_archive_enabled: bool = False
    conversation_archive_dir: Path = DEFAULT_CONVERSATION_ARCHIVE_DIR
    conversation_archive_queue_max_bytes: int = Field(default=256 * 1024 * 1024, gt=0)
    max_decompressed_body_bytes: int = Field(default=32 * 1024 * 1024, gt=0)
    max_decompressed_responses_body_bytes: int = Field(default=128 * 1024 * 1024, gt=0)
    image_inline_fetch_enabled: bool = True
    image_inline_allowed_hosts: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # OpenAI Images API compatibility (POST /v1/images/{generations,edits})
    # ``images_default_model`` is the public model returned to clients when
    # they omit ``model``; it must remain in the ``gpt-image-*`` family. The
    # internal Responses host model used to invoke the ``image_generation``
    # tool is a fixed constant in ``app/modules/proxy/api.py``.
    images_default_model: str = "gpt-image-2"
    # NOTE: there is intentionally no ``images_max_n`` setting. The
    # upstream ``image_generation`` tool path accepts only a single
    # image per call and codex-lb does not yet implement client-side
    # fan-out, so ``n > 1`` is hard-rejected at the API boundary. The
    # cap is lifted in the same change that introduces fan-out.
    model_registry_enabled: bool = True
    # Fallback Codex client version used when the live release lookup fails.
    # Must stay >= the highest ``minimal_client_version`` in the bootstrap
    # catalog (GPT-5.6 requires 0.144.0) or a degraded-startup refresh would
    # receive an upstream catalog without those models.
    model_registry_client_version: str = "0.144.0"
    # Persisted registry snapshots older than this are ignored at load time
    # (bootstrap catalog remains the floor until the next leader refresh).
    model_registry_snapshot_max_age_seconds: int = Field(default=86400, gt=0)
    model_context_window_overrides: Annotated[dict[str, int], NoDecode] = Field(default_factory=dict)
    proxy_unauthenticated_client_cidrs: Annotated[list[str], NoDecode] = Field(default_factory=list)
    firewall_trust_proxy_headers: bool = False
    firewall_trusted_proxy_cidrs: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["127.0.0.1/32", "::1/128"]
    )
    firewall_ip_cache_ttl_seconds: int = Field(default=30, gt=0)
    dashboard_auth_mode: DashboardAuthMode = DashboardAuthMode.STANDARD
    dashboard_trust_loopback_host_header_for_long_sessions: bool = False

    def upstream_websocket_proxy_env(self) -> Mapping[str, str | None]:
        return _effective_environ()

    dashboard_auth_proxy_header: str = "Remote-User"

    # --- Multi-replica & production settings ---
    # Prometheus metrics
    metrics_enabled: bool = False
    metrics_port: int = 9090

    # Logging
    log_format: str = "text"  # "text" or "json"

    # Leader election
    leader_election_enabled: bool = True
    leader_election_ttl_seconds: int = Field(default=60, ge=5)

    # Circuit breaker (failure threshold and recovery timeout are fixed
    # constants in ``app/core/resilience/circuit_breaker.py``)
    circuit_breaker_enabled: bool = False

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

    # Per-class proxy bulkhead limits (http/websocket/compact) always derive
    # from this single limit; see ``BulkheadSemaphore`` for the derivation.
    bulkhead_proxy_limit: int = Field(default=512, ge=0)
    bulkhead_dashboard_limit: int = Field(default=50, ge=0)
    dashboard_bootstrap_token: str | None = None
    proxy_token_refresh_limit: int = Field(default=64, ge=0)
    proxy_upstream_websocket_connect_limit: int = Field(default=128, ge=0)
    proxy_response_create_limit: int = Field(default=256, ge=0)
    proxy_compact_response_create_limit: int = Field(default=64, ge=0)
    proxy_admission_wait_timeout_seconds: float = Field(default=10.0, gt=0)
    proxy_account_response_create_limit: int = Field(default=4, ge=0)
    proxy_account_stream_limit: int = Field(default=8, ge=0)
    proxy_account_stream_recovery_reserve: int = Field(default=1, ge=0)
    proxy_account_inflight_penalty_pct: float = Field(default=2.5, ge=0)
    proxy_account_lease_token_weight: float = Field(default=1.0, ge=0)
    proxy_account_lease_ttl_seconds: float = Field(default=900.0, gt=0)
    proxy_account_caps_scope: Literal["partitioned", "replica"] = "partitioned"
    proxy_account_cap_partition_scale_down_seconds: int = Field(default=60, ge=30)
    # Explicit operator declaration of how many worker processes
    # (uvicorn/gunicorn) this instance runs behind a single bridge-ring instance
    # id. Only ``1`` (the default) is supported: per-account concurrency caps are
    # partitioned per REPLICA via the bridge ring, and intra-pod multi-worker
    # cap partitioning cannot be made reliable (there is no portable per-worker
    # index — standard multi-worker launches inherit the same environment into
    # every child). A declared value greater than 1 is rejected at startup by
    # ``_validate_workers_per_instance``; operators scale horizontally via
    # replicas instead. Default 1 is a no-op requiring zero operator action.
    workers_per_instance: int = Field(default=1, ge=1)
    proxy_refresh_failure_cooldown_seconds: float = Field(default=5.0, ge=0.0)
    usage_refresh_auth_failure_cooldown_seconds: float = Field(default=300.0, ge=0.0)

    # Local memory-pressure guard (0 = disabled). Requests are rejected with
    # 503 once RSS reaches the threshold; a warning is logged from 80% of it
    # (``app/core/resilience/memory_monitor.py`` derives the warning level).
    memory_reject_threshold_mb: int = 0

    # OpenTelemetry
    otel_enabled: bool = False
    otel_exporter_endpoint: str = ""

    # Shutdown drain
    shutdown_drain_timeout_seconds: int = 30

    # HTTP connector limits
    http_connector_limit: int = 100
    http_connector_limit_per_host: int = 50

    @field_validator("request_log_retention_days")
    @classmethod
    def _validate_request_log_retention(cls, value: int) -> int:
        if value != 0 and value < 30:
            raise ValueError("request_log_retention_days must be 0 (disabled) or >= 30")
        return value

    @field_validator("usage_history_retention_days")
    @classmethod
    def _validate_usage_history_retention(cls, value: int) -> int:
        if value != 0 and value < 45:
            raise ValueError("usage_history_retention_days must be 0 (disabled) or >= 45")
        return value

    @field_validator("data_dir", mode="before")
    @classmethod
    def _expand_data_dir(cls, value: str | Path) -> Path:
        if isinstance(value, Path):
            return value.expanduser()
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return _default_home_dir()
            return Path(stripped).expanduser()
        raise TypeError("data_dir must be a path")

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

    @field_validator("conversation_archive_dir", mode="before")
    @classmethod
    def _expand_conversation_archive_dir(cls, value: str | Path) -> Path:
        if isinstance(value, Path):
            return value.expanduser()
        if isinstance(value, str):
            return Path(value).expanduser()
        raise TypeError("conversation_archive_dir must be a path")

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

    @field_validator(
        "http_responses_session_bridge_codex_prewarm_allow_api_key_ids",
        "http_responses_session_bridge_codex_prewarm_deny_api_key_ids",
        mode="before",
    )
    @classmethod
    def _normalize_http_bridge_prewarm_api_key_ids(cls, value: StringListInput) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            entries = [entry.strip() for entry in value.split(",")]
            return [entry for entry in entries if entry]
        if isinstance(value, list):
            normalized: list[str] = []
            for entry in value:
                if isinstance(entry, str):
                    api_key_id = entry.strip()
                    if api_key_id:
                        normalized.append(api_key_id)
            return normalized
        raise TypeError("prewarm api key ids must be a list or comma-separated string")

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

    @field_validator("warmup_model", mode="before")
    @classmethod
    def _normalize_warmup_model(cls, value: object) -> str:
        if not isinstance(value, str):
            raise TypeError("warmup_model must be a string")
        normalized = value.strip()
        if not normalized:
            raise ValueError("warmup_model must not be blank")
        return normalized

    @model_validator(mode="after")
    def _apply_data_dir_defaults(self) -> "Settings":
        if self.data_dir == DEFAULT_HOME_DIR:
            return self
        explicitly_set = self.model_fields_set
        if "database_url" not in explicitly_set and self.database_url == DEFAULT_DATABASE_URL:
            self.database_url = f"sqlite+aiosqlite:///{self.data_dir / 'store.db'}"
        if "encryption_key_file" not in explicitly_set and self.encryption_key_file == DEFAULT_ENCRYPTION_KEY_FILE:
            self.encryption_key_file = self.data_dir / "encryption.key"
        if (
            "conversation_archive_dir" not in explicitly_set
            and self.conversation_archive_dir == DEFAULT_CONVERSATION_ARCHIVE_DIR
        ):
            self.conversation_archive_dir = self.data_dir / "conversation-archive"
        return self

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

    @cached_property
    def trace_channels(self) -> frozenset[str]:
        """Parsed ``trace`` channels; empty set (the default) disables all."""
        return frozenset(entry.strip().lower() for entry in self.trace.split(",") if entry.strip())

    @model_validator(mode="after")
    def _validate_token_refresh_claim_ttl(self) -> "Settings":
        # The claim is acquired BEFORE the refresh-admission wait and held
        # through the OAuth exchange, so the TTL floor must cover both: the
        # admission wait ceiling plus the HTTP exchange (2x for margin). A TTL
        # sized only around the HTTP timeout can expire under a healthy
        # claimant stuck in admission, letting another replica claim the same
        # account and reuse the single-use refresh token.
        minimum_ttl = self.proxy_admission_wait_timeout_seconds + 2.0 * self.token_refresh_timeout_seconds
        if "token_refresh_claim_ttl_seconds" not in self.model_fields_set:
            # The operator has not opted into the new setting. Derive the
            # default from the related timeouts so a deployment that only
            # raised the refresh/admission timeouts before this setting
            # existed still boots with a TTL that satisfies the invariant,
            # instead of crashing at startup against the fixed 30s default.
            self.token_refresh_claim_ttl_seconds = max(self.token_refresh_claim_ttl_seconds, minimum_ttl)
            return self
        if self.token_refresh_claim_ttl_seconds < minimum_ttl:
            raise ValueError(
                "token_refresh_claim_ttl_seconds must be at least proxy_admission_wait_timeout_seconds "
                f"+ 2x token_refresh_timeout_seconds ({minimum_ttl}s) so a healthy claimant cannot lose "
                "its claim while waiting for refresh admission or mid-exchange"
            )
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

    @model_validator(mode="after")
    def _validate_workers_per_instance(self) -> "Settings":
        # Only one worker process per instance is supported. Per-account
        # concurrency caps are partitioned per REPLICA via the bridge ring, which
        # is correct only when a single process runs behind each ring instance
        # id. Running multiple worker processes per instance cannot be made
        # reliable for shared caps: there is no portable per-worker index, and a
        # standard uvicorn/gunicorn multi-worker launch inherits the SAME
        # environment into every child, so the workers cannot self-partition. Fail
        # fast on the explicit declaration rather than silently over-admitting.
        if self.workers_per_instance > 1:
            raise ValueError(
                "workers_per_instance (CODEX_LB_WORKERS_PER_INSTANCE="
                f"{self.workers_per_instance}) is not supported: running more than one worker "
                "process per instance would multiply per-account concurrency caps, because those "
                "caps are partitioned per replica via the bridge ring and intra-pod worker "
                "partitioning cannot be made reliable. Run ONE worker per pod/container and scale "
                "horizontally via replicas (the bridge ring partitions caps per replica); set "
                "CODEX_LB_WORKERS_PER_INSTANCE=1 (the default)."
            )
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
