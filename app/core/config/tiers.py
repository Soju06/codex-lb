"""Configuration tiers for every ``Settings`` field (configuration-tiers capability).

Each ``CODEX_LB_*`` field belongs to exactly one tier:

- ``T0`` bootstrap: needed before the database is reachable (data dir, DB URL,
  encryption key, migration policy, first-login token). env only.
- ``T1`` instance topology: legitimately differs per replica or per deployment
  (bridge instance id/ring, bind hosts, trusted proxies, pool/worker sizes,
  leader election, observability endpoints). env only.
- ``T2`` secret: encrypted in the database, env is at most a seed.
- ``T3`` behaviour tunable / feature flag: the dashboard (``dashboard_settings``)
  is the management surface. A T3 field that has no ``dashboard_settings``
  column of the same name MUST either name its existing database home in
  ``DASHBOARD_HOMES`` (``table.column``) or be listed in ``MIGRATING`` until
  it gets one.
- ``T4`` incident debug: env allowed, dashboard toggle recommended.

``scripts/check_settings_tiers.py`` (run by ``make lint``) fails when a field
is missing here, when a T3 field has none of a same-name dashboard column, a
``DASHBOARD_HOMES`` mapping or a ``MIGRATING`` entry, when a ``DASHBOARD_HOMES``
target names a column that does not exist, or when ``.env.example`` mentions a
T2-T4 field. Entries for fields that no longer exist only warn, so removals can
land in either order.
"""

from __future__ import annotations

from typing import Final, Literal

Tier = Literal["T0", "T1", "T2", "T3", "T4"]

TIERS: Final[tuple[Tier, ...]] = ("T0", "T1", "T2", "T3", "T4")

# Declaration order follows ``Settings``; the tier is the policy answer to
# "may this value differ between two replicas / must it exist before the DB?".
SETTING_TIERS: Final[dict[str, Tier]] = {
    "data_dir": "T0",
    "database_url": "T0",
    "database_pool_size": "T1",
    "database_max_overflow": "T1",
    "database_migrate_on_startup": "T0",
    "database_sqlite_pre_migrate_backup_enabled": "T0",
    "database_sqlite_pre_migrate_backup_max_files": "T0",
    "database_sqlite_startup_check_mode": "T0",
    "database_alembic_auto_remap_enabled": "T0",
    "database_migration_lock_timeout_seconds": "T0",
    "upstream_base_url": "T1",
    "upstream_connect_timeout_seconds": "T3",
    "upstream_websocket_trust_env": "T1",
    "proxy_request_budget_seconds": "T3",
    "http_responses_stream_request_budget_seconds": "T3",
    "compact_request_budget_seconds": "T3",
    "stream_idle_timeout_seconds": "T3",
    "sse_keepalive_interval_seconds": "T3",
    "proxy_downstream_websocket_idle_timeout_seconds": "T3",
    # bind host of the OAuth callback listener; policy §2 T1 example
    "oauth_callback_host": "T1",
    "auth_guardian_enabled": "T3",
    "transcription_request_budget_seconds": "T3",
    # path to a replacement quota-key registry; deployment artefact, not behaviour
    "additional_quota_registry_file": "T1",
    "rate_limit_reset_credits_refresh_enabled": "T3",
    # K2 bridge: T4 kill switch for the request-path bridge, not a tunable.
    "http_responses_session_bridge_enabled": "T4",
    "http_responses_session_bridge_request_budget_seconds": "T3",
    "http_responses_session_bridge_codex_prewarm_enabled": "T3",
    "http_responses_session_bridge_max_sessions": "T1",
    "http_responses_session_bridge_queue_limit": "T1",
    "http_responses_session_bridge_operation_event_spool_max_bytes": "T1",
    "http_responses_session_bridge_operation_spool_format": "T1",
    "http_responses_session_bridge_operation_event_spool_batch_size": "T1",
    "http_responses_session_bridge_operation_event_spool_flush_interval_seconds": "T1",
    "http_responses_session_bridge_operation_event_spool_max_pending_events": "T1",
    "http_responses_session_bridge_operation_event_spool_max_pending_bytes": "T1",
    "http_responses_session_bridge_operation_spool_retention_seconds": "T3",
    "http_responses_session_bridge_instance_id": "T1",
    "http_responses_session_bridge_instance_ring": "T1",
    "http_responses_session_bridge_advertise_base_url": "T1",
    "upstream_route_cache_ttl_seconds": "T1",
    "automations_scheduler_enabled": "T3",
    "telemetry_enabled": "T3",
    "telemetry_endpoint": "T1",
    "encryption_key_file": "T0",
    "encryption_key_fingerprint_mode": "T0",
    "database_migrations_fail_fast": "T0",
    "trace": "T4",
    "conversation_archive_enabled": "T3",
    "conversation_archive_dir": "T1",
    "conversation_archive_queue_max_bytes": "T1",
    "model_registry_client_version": "T1",
    "model_registry_snapshot_max_age_seconds": "T1",
    "model_context_window_overrides": "T3",
    # raw socket-peer CIDRs of this replica's network namespace; sibling of the
    # trusted-proxy CIDRs below, not of the firewall allowlist (projected IPs)
    "proxy_unauthenticated_client_cidrs": "T1",
    # trusted-proxy topology; policy §2 T1 example
    "firewall_trust_proxy_headers": "T1",
    # trusted-proxy topology; policy §2 T1 example
    "firewall_trusted_proxy_cidrs": "T1",
    "firewall_ip_cache_ttl_seconds": "T1",
    # reverse-proxy trust list for scope["client"] projection (Uvicorn semantics)
    "forwarded_allow_ips": "T1",
    # reverse-proxy deployment dependent, self-lockout risk from the dashboard (policy D2)
    "dashboard_auth_mode": "T1",
    # last link of the dashboard_auth_mode trust chain: whether a loopback Host
    # header is believed is a per-deployment reverse-proxy fact (policy D2)
    "dashboard_trust_loopback_host_header_for_long_sessions": "T1",
    # header name is fixed by the reverse-proxy deployment (policy D2)
    "dashboard_auth_proxy_header": "T1",
    # the group header the same reverse proxy sets (policy D2)
    "dashboard_auth_proxy_groups_header": "T1",
    "metrics_enabled": "T1",
    "metrics_port": "T1",
    "log_format": "T1",
    "leader_election_enabled": "T1",
    "leader_election_ttl_seconds": "T1",
    "circuit_breaker_enabled": "T3",
    "soft_drain_enabled": "T3",
    "deterministic_failover_enabled": "T3",
    "thread_cache_identity_mode": "T3",
    "backpressure_max_concurrent_requests": "T1",
    "bulkhead_proxy_limit": "T1",
    "bulkhead_dashboard_limit": "T1",
    # first remote login token; policy §2 lists it under T0 bootstrap, not T2
    "dashboard_bootstrap_token": "T0",
    # advertised client-facing address; differs per deployment
    "connect_address": "T1",
    # per-process asyncio.Semaphore capacity, same class as bulkhead_proxy_limit
    "proxy_response_create_limit": "T1",
    "proxy_account_response_create_limit": "T3",
    "proxy_account_stream_limit": "T3",
    "proxy_account_stream_recovery_reserve": "T3",
    "proxy_api_key_fair_share_congestion_threshold_pct": "T3",
    "proxy_account_inflight_penalty_pct": "T3",
    "proxy_overload_isolation_seconds": "T3",
    "proxy_account_error_rate_weighting_enabled": "T3",
    "proxy_account_lease_token_weight": "T3",
    "proxy_account_lease_ttl_seconds": "T3",
    "proxy_account_caps_scope": "T1",
    "proxy_account_cap_partition_scale_down_seconds": "T1",
    "timeout_invariant_validation_strict": "T4",
    "memory_reject_threshold_mb": "T1",
    "event_loop_lag_warn_threshold_seconds": "T1",
    "otel_enabled": "T1",
    "otel_exporter_endpoint": "T1",
    "shutdown_drain_timeout_seconds": "T1",
    "http_connector_limit": "T1",
    "http_connector_limit_per_host": "T1",
}

# T3 fields that still live only in env. Value = target dashboard home or
# "backlog" while none has been designed. Remove the entry in the PR that adds
# the ``dashboard_settings`` column (the checker warns once it is redundant).
#
# Empty since ``constantize-token-refresh-interval``: every T3 field now has a
# dashboard home (a same-name ``dashboard_settings`` column or a
# ``DASHBOARD_HOMES`` mapping), and the last backlog entry
# (``token_refresh_interval_days``) became the fixed
# ``app/core/auth/refresh.TOKEN_REFRESH_INTERVAL_DAYS`` instead of getting a
# card nobody would flip. An empty registry is the intended terminal state and
# is not an error; the registry stays as the declared landing spot for a T3
# field that must ship one release ahead of its column.
MIGRATING: Final[dict[str, str]] = {}

# T3 fields whose database home already exists under a different column name
# (or in another configuration table). Value = ``table.column``; the checker
# fails when the column does not exist. The field's environment variable is the
# fallback while that column holds no decision, per the precedence rule.
DASHBOARD_HOMES: Final[dict[str, str]] = {
    # persisted decision > CODEX_LB_TELEMETRY_ENABLED > default (telemetry spec)
    "telemetry_enabled": "dashboard_settings.telemetry_consent",
    # M4 model catalogue: one row per slug; the env dict is the per-slug fallback
    "model_context_window_overrides": "model_context_window_overrides.context_window",
}
