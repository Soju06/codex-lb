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
from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.core.auth.dashboard_mode import DashboardAuthMode, normalize_dashboard_auth_proxy_header
from app.core.clients.codex_version_snapshot import CODEX_VERSION
from app.core.utils.proxy_env import outbound_proxy_env_configured

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[3]


def _resolve_env_files() -> tuple[Path, ...]:
    """Resolve the env files read at settings load.

    Default: ``.env`` / ``.env.local`` next to the module root (the repository
    checkout). ``CODEX_LB_ENV_FILE`` — an ``os.pathsep``-separated list of
    paths — overrides that discovery for installs whose module root cannot
    contain env files: the Nix package's module root lives in the read-only
    store, so its wrapper points this at the launch directory. It is a
    bootstrap environment variable rather than a ``Settings`` field because
    the env-file locations must be known before Settings can read env files.
    Unset (every non-Nix launch path) preserves module-root discovery
    unchanged; launch-directory env files are never loaded implicitly.
    """
    raw = os.getenv("CODEX_LB_ENV_FILE", "")
    files = tuple(Path(entry.strip()).expanduser() for entry in raw.split(os.pathsep) if entry.strip())
    if files:
        return files
    return (BASE_DIR / ".env", BASE_DIR / ".env.local")


ENV_FILES = _resolve_env_files()

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
# Names whose warning release has shipped are pruned from this tuple (the
# July 2026 phases 1-4 shipped in v1.22-v1.24 and were dropped by
# ``remove-dead-env-settings``, the first release after v1.24.0).
_REMOVED_SETTINGS: tuple[str, ...] = (
    # remove-dead-env-settings (first release after v1.24.0): retention is a
    # dashboard runtime setting (Settings -> Advanced -> Data retention);
    # NULL now means disabled.
    "CODEX_LB_REQUEST_LOG_RETENTION_DAYS",
    "CODEX_LB_USAGE_HISTORY_RETENTION_DAYS",
    # remove-dead-env-settings (first release after v1.24.0): dashboard-owned
    # columns that the env value only seeded on first boot (or never read).
    "CODEX_LB_HTTP_DOWNSTREAM_TRANSPORT_POLICY",
    "CODEX_LB_OPENAI_CACHE_AFFINITY_MAX_AGE_SECONDS",
    "CODEX_LB_WARMUP_MODEL",
    "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_GATEWAY_SAFE_MODE",
    # Dashboard-authoritative settings (remove-upstream-stream-transport-env):
    # the dashboard row is the only source of the upstream stream transport.
    "CODEX_LB_UPSTREAM_STREAM_TRANSPORT",
    # constantize-core-tunables (first release after v1.25.0-beta.5): never-tuned
    # core tunables became fixed constants (see the openspec change context).
    "CODEX_LB_UPSTREAM_COMPACT_TIMEOUT_SECONDS",
    "CODEX_LB_MAX_SSE_EVENT_BYTES",
    "CODEX_LB_UPSTREAM_RESPONSE_CREATE_MAX_BYTES",
    "CODEX_LB_OAUTH_TIMEOUT_SECONDS",
    "CODEX_LB_TOKEN_REFRESH_TIMEOUT_SECONDS",
    "CODEX_LB_TOKEN_REFRESH_CLAIM_TTL_SECONDS",
    "CODEX_LB_PROXY_REFRESH_FAILURE_COOLDOWN_SECONDS",
    "CODEX_LB_PROXY_ADMISSION_WAIT_TIMEOUT_SECONDS",
    "CODEX_LB_USAGE_FETCH_TIMEOUT_SECONDS",
    "CODEX_LB_USAGE_FETCH_MAX_RETRIES",
    "CODEX_LB_USAGE_REFRESH_ENABLED",
    "CODEX_LB_USAGE_REFRESH_INTERVAL_SECONDS",
    "CODEX_LB_USAGE_REFRESH_AUTH_FAILURE_COOLDOWN_SECONDS",
    "CODEX_LB_LIVE_USAGE_INGESTION_ENABLED",
    "CODEX_LB_RATE_LIMIT_RESET_CREDITS_REFRESH_INTERVAL_SECONDS",
    "CODEX_LB_STICKY_SESSION_CLEANUP_ENABLED",
    "CODEX_LB_QUOTA_PLANNER_SCHEDULER_ENABLED",
    "CODEX_LB_MODEL_REGISTRY_ENABLED",
    "CODEX_LB_MAX_DECOMPRESSED_BODY_BYTES",
    "CODEX_LB_MAX_DECOMPRESSED_RESPONSES_BODY_BYTES",
    "CODEX_LB_IMAGE_INLINE_FETCH_ENABLED",
    "CODEX_LB_IMAGE_INLINE_ALLOWED_HOSTS",
    "CODEX_LB_IMAGES_DEFAULT_MODEL",
    "CODEX_LB_OPENAI_PROMPT_CACHE_KEY_DERIVATION_ENABLED",
    "CODEX_LB_PROXY_TOKEN_REFRESH_LIMIT",
    "CODEX_LB_PROXY_UPSTREAM_WEBSOCKET_CONNECT_LIMIT",
    "CODEX_LB_PROXY_COMPACT_RESPONSE_CREATE_LIMIT",
    # K2 bridge (constantize-session-bridge-tunables): never-tuned HTTP session
    # bridge tunables are fixed module constants now (see
    # app/modules/proxy/_service/http_bridge/helpers.py, retry_circuit.py,
    # request_submit.py and app/modules/proxy/api.py).
    "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_IDLE_TTL_SECONDS",
    "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_CODEX_IDLE_TTL_SECONDS",
    "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_STUCK_GATE_RETIRE_AFTER_SECONDS",
    "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_ANCHOR_POISON_FAILURE_THRESHOLD",
    "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_SERVER_RECOVERY_MAX_ATTEMPTS",
    "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_CLEAN_CLOSE_RETRY_JITTER_MAX_SECONDS",
    "CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_OPERATION_LEDGER_ENABLED",
    # end K2 bridge
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
    found = [name for name in _REMOVED_SETTINGS if _env_key(source, name) is not None]
    if found:
        logger.warning(
            "removed setting(s) ignored: %s — each is now a fixed default or a dashboard runtime setting; "
            "see PRINCIPLES.md P2 / issue #1340",
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


# Startup guard, not a setting: the only supported value is 1 (see
# ``Settings._reject_multiple_workers_per_instance``).
_WORKERS_PER_INSTANCE_ENV = "CODEX_LB_WORKERS_PER_INSTANCE"


def _effective_environ() -> dict[str, str | None]:
    environ: dict[str, str | None] = {}
    for env_file in ENV_FILES:
        environ.update(dotenv_values(env_file))
    environ.update(os.environ)
    return environ


def _env_key(environ: Mapping[str, str | None], name: str) -> str | None:
    """Return the key under which ``name`` is declared, matching case-insensitively.

    pydantic-settings resolves ``CODEX_LB_*`` names case-insensitively
    (``case_sensitive=False`` is the ``BaseSettings`` default), so the env
    guards that replaced former fields must accept the same spellings a field
    did. An exact-case declaration wins over other casings.
    """
    if name in environ:
        return name
    upper = name.upper()
    for key in environ:
        if key.upper() == upper:
            return key
    return None


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
    # Pool timeout and recycle are fixed constants in ``app/db/session.py``;
    # the background-task engine always derives its pool sizing from the two
    # settings below. Defaults are sized so one replica's two pooled engines
    # cap at (25 + 15) * 2 = 80 PostgreSQL connections, preserving >= 20 raw
    # server slots on PostgreSQL's default max_connections=100 for reserved
    # connections, the migration path's two-connection peak, and operations.
    database_pool_size: int = Field(default=25, gt=0)
    database_max_overflow: int = Field(default=15, ge=0)
    database_migrate_on_startup: bool = True
    database_sqlite_pre_migrate_backup_enabled: bool = True
    database_sqlite_pre_migrate_backup_max_files: int = Field(default=5, ge=1)
    database_sqlite_startup_check_mode: Literal["quick", "full", "off"] = "quick"
    database_alembic_auto_remap_enabled: bool = True
    database_migration_lock_timeout_seconds: float = Field(default=300.0, gt=0)
    upstream_base_url: str = "https://chatgpt.com/backend-api"
    # T3 → dashboard (deprecated env alias, remove next minor)
    upstream_connect_timeout_seconds: float = 8.0
    upstream_websocket_trust_env: bool = Field(default_factory=_default_upstream_websocket_trust_env)
    # T3 → dashboard (deprecated env alias, remove next minor)
    proxy_request_budget_seconds: float = Field(default=600.0, gt=0)
    # T3 → dashboard (deprecated env alias, remove next minor)
    http_responses_stream_request_budget_seconds: float = Field(default=7200.0, gt=0)
    # T3 → dashboard (deprecated env alias, remove next minor)
    compact_request_budget_seconds: float = Field(default=180.0, gt=0)
    # T3 → dashboard (deprecated env alias, remove next minor)
    stream_idle_timeout_seconds: float = Field(default=7200.0, gt=0)
    # T3 → dashboard (deprecated env alias, remove next minor)
    sse_keepalive_interval_seconds: float = Field(default=10.0, ge=0)
    # T3 → dashboard (deprecated env alias, remove next minor)
    proxy_downstream_websocket_idle_timeout_seconds: float = Field(default=120.0, gt=0)
    oauth_callback_host: str = _default_oauth_callback_host()
    # T3 → dashboard (deprecated env alias, remove next minor)
    auth_guardian_enabled: bool = True
    # T3 → dashboard (deprecated env alias, remove next minor)
    transcription_request_budget_seconds: float = Field(default=120.0, gt=0)
    token_refresh_interval_days: int = 8
    # T1 (topology). Path to a JSON registry of additional usage quota keys
    # that replaces the bundled ``config/additional_quota_registry.json``.
    # Unset (or blank) keeps the bundled registry. The Alembic backfill
    # migration ``20260312_000000`` reads the same env name directly because
    # migrations must not depend on ``Settings``.
    additional_quota_registry_file: Path | None = None
    # T3 → dashboard (deprecated env alias, remove next minor)
    rate_limit_reset_credits_refresh_enabled: bool = True
    http_responses_session_bridge_enabled: bool = True
    # T3 → dashboard (deprecated env alias, remove next minor)
    http_responses_session_bridge_request_budget_seconds: float = Field(default=7200.0, gt=0)
    # T3 → dashboard (deprecated env alias, remove next minor): the same-name
    # ``dashboard_settings`` column wins when set (M3 codex prewarm).
    http_responses_session_bridge_codex_prewarm_enabled: bool = False
    http_responses_session_bridge_max_sessions: int = Field(default=256, gt=0)
    http_responses_session_bridge_queue_limit: int = Field(default=8, gt=0)
    # Bound durable replay storage per operation so a long response cannot
    # exhaust the database. An incomplete spool is never replayed.
    http_responses_session_bridge_operation_event_spool_max_bytes: int = Field(default=2 * 1024 * 1024, gt=0)
    # Rollout fence: chunks_v2 must be enabled only after every serving replica
    # runs the dual-reader schema expansion.
    http_responses_session_bridge_operation_spool_format: Literal["rows_v1", "chunks_v2"] = "rows_v1"
    http_responses_session_bridge_operation_event_spool_batch_size: int = Field(default=32, gt=0, le=256)
    http_responses_session_bridge_operation_event_spool_flush_interval_seconds: float = Field(
        default=0.1,
        ge=0.01,
        le=5.0,
    )
    http_responses_session_bridge_operation_event_spool_max_pending_events: int = Field(default=2048, gt=0)
    http_responses_session_bridge_operation_event_spool_max_pending_bytes: int = Field(
        default=32 * 1024 * 1024,
        gt=0,
    )
    # Keep durable transcript material short-lived by default. The transcript
    # is sensitive prompt/output data and is only a recovery aid.
    http_responses_session_bridge_operation_spool_retention_seconds: float = Field(
        default=7 * 24 * 60 * 60,
        gt=0,
    )
    # Recovery-first mode can either ask the client to drop an ambiguous anchor
    # or let the bridge retry that anchored request once on a fresh upstream
    # socket. Both are at-least-once strategies; fail-closed remains default.
    http_responses_session_bridge_ambiguous_continuation_recovery_mode: Literal[
        "fail_closed",
        "client_full_history_once",
        "server_anchored_replay_once",
        "server_indefinite_recovery",
    ] = "fail_closed"
    http_responses_session_bridge_instance_id: str = Field(default_factory=_default_http_bridge_instance_id)
    http_responses_session_bridge_instance_ring: Annotated[list[str], NoDecode] = Field(default_factory=list)
    http_responses_session_bridge_advertise_base_url: str | None = None
    # TTL backstop for the per-account upstream-route resolution cache; 0
    # disables caching. Admin mutations invalidate durably through the
    # cache-invalidation bus, so this only bounds out-of-band database edits.
    upstream_route_cache_ttl_seconds: float = Field(default=60.0, ge=0)
    # T3 → dashboard (deprecated env alias, remove next minor)
    automations_scheduler_enabled: bool = True
    # T3 (dashboard home: dashboard_settings.telemetry_consent). Headless
    # first-boot opt-out fallback; a persisted dashboard decision always wins.
    telemetry_enabled: bool | None = None
    telemetry_endpoint: str = "https://telemetry.tokmaxxing.com"
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
    # T3 → dashboard (deprecated env alias, remove next minor): the
    # ``dashboard_settings.conversation_archive_enabled`` column wins when set;
    # the archive writer resolves it from the settings-cache snapshot.
    conversation_archive_enabled: bool = False
    conversation_archive_dir: Path = DEFAULT_CONVERSATION_ARCHIVE_DIR
    conversation_archive_queue_max_bytes: int = Field(default=256 * 1024 * 1024, gt=0)
    # OpenAI Images API compatibility (POST /v1/images/{generations,edits}):
    # the public default model (``gpt-image-2``) and the internal Responses
    # host model are fixed constants (``app/core/openai/images.py`` /
    # ``app/modules/proxy/api.py``). There is intentionally no ``images_max_n``
    # setting: the upstream ``image_generation`` tool path accepts only a
    # single image per call and codex-lb does not yet implement client-side
    # fan-out, so ``n > 1`` is hard-rejected at the API boundary. The cap is
    # lifted in the same change that introduces fan-out.
    # Fallback Codex client version used when the live release lookup fails.
    # Must stay >= the highest ``minimal_client_version`` in the bootstrap
    # catalog (GPT-5.6 requires 0.144.0) or a degraded-startup refresh would
    # receive an upstream catalog without those models.
    model_registry_client_version: str = CODEX_VERSION
    # Persisted registry snapshots older than this are ignored at load time
    # (bootstrap catalog remains the floor until the next leader refresh).
    model_registry_snapshot_max_age_seconds: int = Field(default=86400, gt=0)
    # T3 → dashboard (deprecated env alias, remove next minor). Per-slug fallback:
    # a ``model_context_window_overrides`` dashboard row wins for its slug; slugs
    # without a row still read this dict.
    model_context_window_overrides: Annotated[dict[str, int], NoDecode] = Field(default_factory=dict)
    # T1 (topology). Raw socket-peer CIDRs allowed to call the proxy without an
    # API key: a fact of this replica's network namespace (sidecar, pod CIDR),
    # like the trusted-proxy CIDRs below.
    proxy_unauthenticated_client_cidrs: Annotated[list[str], NoDecode] = Field(default_factory=list)
    firewall_trust_proxy_headers: bool = False
    firewall_trusted_proxy_cidrs: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["127.0.0.1/32", "::1/128"]
    )
    firewall_ip_cache_ttl_seconds: int = Field(default=30, gt=0)
    # T1 (topology). Uvicorn-compatible proxy-projection trust list consumed by
    # ``TrustedProxyHeadersMiddleware``. Semantics are Uvicorn's, unchanged:
    # unset trusts ``127.0.0.1``, empty trusts no peer, ``*`` trusts every
    # peer, anything else is a comma-separated host/network list. The bare
    # ``FORWARDED_ALLOW_IPS`` env name stays authoritative for compatibility
    # with Uvicorn deployments; ``CODEX_LB_FORWARDED_ALLOW_IPS`` is the
    # prefixed alias. This governs ``scope["client"]``/scheme projection only;
    # ``firewall_trusted_proxy_cidrs`` governs firewall and auth client
    # resolution from the raw socket peer.
    forwarded_allow_ips: str | None = Field(
        default=None,
        validation_alias=AliasChoices("FORWARDED_ALLOW_IPS", "CODEX_LB_FORWARDED_ALLOW_IPS"),
    )
    dashboard_auth_mode: DashboardAuthMode = DashboardAuthMode.STANDARD
    # T1 (topology). Last link of the ``dashboard_auth_mode`` trust chain:
    # whether a loopback ``Host`` header may unlock a >30d session TTL depends
    # on how this deployment's reverse proxy rewrites it (policy D2).
    dashboard_trust_loopback_host_header_for_long_sessions: bool = False

    def upstream_websocket_proxy_env(self) -> Mapping[str, str | None]:
        return _effective_environ()

    dashboard_auth_proxy_header: str = "Remote-User"

    # --- Multi-replica & production settings ---
    # Prometheus metrics
    metrics_enabled: bool = False
    metrics_port: int = Field(default=9090, ge=1, le=65535)

    # Logging
    log_format: Literal["text", "json"] = "text"

    # Leader election
    leader_election_enabled: bool = True
    leader_election_ttl_seconds: int = Field(default=60, ge=5)

    # Circuit breaker (failure threshold and recovery timeout are fixed
    # constants in ``app/core/resilience/circuit_breaker.py``).
    # T3 → dashboard (deprecated env alias, remove next minor): the
    # ``dashboard_settings.circuit_breaker_enabled`` column wins when set.
    circuit_breaker_enabled: bool = False

    # Soft drain & deterministic failover (drain/probe thresholds are fixed
    # constants in ``app/core/balancer/logic.py``).
    # T3 → dashboard (deprecated env alias, remove next minor): the same-name
    # ``dashboard_settings`` columns win when set.
    soft_drain_enabled: bool = True
    deterministic_failover_enabled: bool = True

    # Backpressure
    backpressure_max_concurrent_requests: int = 0  # 0 = unlimited

    # Per-class proxy bulkhead limits (http/websocket/compact) always derive
    # from this single limit; see ``BulkheadSemaphore`` for the derivation.
    bulkhead_proxy_limit: int = Field(default=512, ge=0)
    bulkhead_dashboard_limit: int = Field(default=50, ge=0)
    dashboard_bootstrap_token: str | None = None
    # T1 (topology). Address the dashboard tells operators to point clients at
    # (``GET /api/settings/runtime/connect-address``). Unset derives it from
    # the request host; a blank value collapses to unset.
    connect_address: str | None = None
    # T1 (topology). Capacity of a per-process asyncio.Semaphore, sized with
    # the replica's resources like ``bulkhead_proxy_limit``.
    proxy_response_create_limit: int = Field(default=256, ge=0)
    proxy_account_response_create_limit: int = Field(default=4, ge=0)
    proxy_account_stream_limit: int = Field(default=8, ge=0)
    proxy_account_stream_recovery_reserve: int = Field(default=1, ge=0)
    # Pool-congestion utilization percentage at which per-API-key stream
    # fair-share throttling engages; 0 disables the gate entirely.
    proxy_api_key_fair_share_congestion_threshold_pct: int = Field(default=0, ge=0, le=100)
    # C2-2 routing/overload: the five fields below have a same-name
    # ``dashboard_settings`` column; the environment is only the fallback the
    # dashboard inherits while its column is NULL (``RoutingTunables``).
    # T3 → dashboard (deprecated env alias, remove next minor)
    proxy_account_inflight_penalty_pct: float = Field(default=2.5, ge=0)
    # Upstream overload (``server_is_overloaded``) handling. Soft backoff and
    # the isolation trip level are fixed constants in
    # ``app/modules/proxy/_load_balancer/overload_backoff.py``; this is how long
    # a sustained-overload account is isolated (fresh selection avoids it and
    # soft sticky owners are rerouted while another candidate exists). ``0``
    # disables the isolation stage and keeps the soft backoff only.
    # T3 → dashboard (deprecated env alias, remove next minor)
    proxy_overload_isolation_seconds: int = Field(default=1800, ge=0)
    # Weighted routing strategies (``capacity_weighted``, ``relative_availability``)
    # discount each candidate's draw weight by its recent upstream error rate
    # (window, sample floor and weight floor are fixed constants in
    # ``app/modules/proxy/_load_balancer/error_rate.py``).
    # T3 → dashboard (deprecated env alias, remove next minor)
    proxy_account_error_rate_weighting_enabled: bool = True
    # T3 → dashboard (deprecated env alias, remove next minor)
    proxy_account_lease_token_weight: float = Field(default=1.0, ge=0)
    # T3 → dashboard (deprecated env alias, remove next minor)
    proxy_account_lease_ttl_seconds: float = Field(default=900.0, gt=0)
    proxy_account_caps_scope: Literal["partitioned", "replica"] = "partitioned"
    proxy_account_cap_partition_scale_down_seconds: int = Field(default=60, ge=30)
    timeout_invariant_validation_strict: bool = False

    # Local memory-pressure guard (0 = disabled). Requests are rejected with
    # 503 once RSS reaches the threshold; a warning is logged from 80% of it
    # (``app/core/resilience/memory_monitor.py`` derives the warning level).
    memory_reject_threshold_mb: int = 0

    # Event-loop lag watchdog (0 = disabled). Samples asyncio.sleep drift once
    # per second; lag at or above the threshold emits a rate-limited warning
    # and Prometheus signals (``app/core/resilience/loop_lag_monitor.py``).
    # Default 0.5s: an order of magnitude above healthy scheduling jitter,
    # well below the lag that fails 10s-budget health checks.
    event_loop_lag_warn_threshold_seconds: float = Field(default=0.5, ge=0.0)

    # OpenTelemetry
    otel_enabled: bool = False
    otel_exporter_endpoint: str = ""

    # Shutdown drain
    shutdown_drain_timeout_seconds: int = Field(default=30, gt=0, le=300)

    # HTTP connector limits
    http_connector_limit: int = 100
    http_connector_limit_per_host: int = 50

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

    @field_validator("connect_address", "additional_quota_registry_file", mode="before")
    @classmethod
    def _blank_optional_to_none(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

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
    def _validate_metrics_port(self) -> "Settings":
        http_port = _configured_http_port()
        if self.metrics_port == http_port:
            raise ValueError(f"metrics_port must not match the main application port ({http_port})")
        return self

    @model_validator(mode="after")
    def _validate_firewall_proxy_trust(self) -> "Settings":
        if self.firewall_trust_proxy_headers and not self.firewall_trusted_proxy_cidrs:
            raise ValueError(
                "firewall_trust_proxy_headers=true requires at least one entry in "
                "firewall_trusted_proxy_cidrs; configure a trusted proxy CIDR or disable proxy header trust"
            )
        return self

    @model_validator(mode="after")
    def _validate_dashboard_auth_mode(self) -> "Settings":
        if self.dashboard_auth_mode != DashboardAuthMode.TRUSTED_HEADER:
            return self
        if not self.firewall_trust_proxy_headers:
            raise ValueError("dashboard_auth_mode=trusted_header requires firewall_trust_proxy_headers=true")
        return self

    @model_validator(mode="after")
    def _reject_multiple_workers_per_instance(self) -> "Settings":
        # Only one worker process per instance is supported. Per-account
        # concurrency caps are partitioned per REPLICA via the bridge ring, which
        # is correct only when a single process runs behind each ring instance
        # id. Running multiple worker processes per instance cannot be made
        # reliable for shared caps: there is no portable per-worker index, and a
        # standard uvicorn/gunicorn multi-worker launch inherits the SAME
        # environment into every child, so the workers cannot self-partition.
        # ``CODEX_LB_WORKERS_PER_INSTANCE`` is therefore not a setting (the only
        # accepted value is the default, 1) but a startup guard: an explicit
        # declaration of anything else fails fast rather than silently
        # over-admitting.
        environ = _effective_environ()
        key = _env_key(environ, _WORKERS_PER_INSTANCE_ENV)
        raw = environ.get(key) if key is not None else None
        if raw is None or not raw.strip():
            return self
        try:
            declared = int(raw.strip())
        except ValueError:
            declared = 0
        if declared < 1:
            raise ValueError(
                f"{_WORKERS_PER_INSTANCE_ENV}={raw.strip()!r} is not a positive integer; "
                f"only {_WORKERS_PER_INSTANCE_ENV}=1 (the default) is supported."
            )
        if declared > 1:
            raise ValueError(
                f"workers_per_instance ({_WORKERS_PER_INSTANCE_ENV}="
                f"{declared}) is not supported: running more than one worker "
                "process per instance would multiply per-account concurrency caps, because those "
                "caps are partitioned per replica via the bridge ring and intra-pod worker "
                "partitioning cannot be made reliable. Run ONE worker per pod/container and scale "
                "horizontally via replicas (the bridge ring partitions caps per replica); set "
                f"{_WORKERS_PER_INSTANCE_ENV}=1 (the default)."
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
