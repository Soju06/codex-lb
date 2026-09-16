<!-- GENERATED — edit scripts/generate_settings_reference.py, not this file. -->

# Settings Reference

**GENERATED** — edit `scripts/generate_settings_reference.py`, not this file.
Regenerate with `uv run python scripts/generate_settings_reference.py`;
`tests/unit/test_settings_reference.py` fails when this page drifts from
`app/core/config/settings.py`.

codex-lb currently exposes 96 settings. Every setting is an environment
variable, normally with the `CODEX_LB_` prefix (process environment or `.env` /
`.env.local` next to the process); aliased settings list every accepted name.
All defaults work with zero configuration —
start from [Configuration](../configuration.md) for the handful that matter,
and treat everything else as advanced operational tunables.

## Tiers

The **Tier** column is the configuration policy for each setting
(`app/core/config/tiers.py`, enforced by `scripts/check_settings_tiers.py`):

- **T0** bootstrap — needed before the database is reachable; env only.
- **T1** instance topology — legitimately differs per replica or deployment; env only.
- **T2** secret — encrypted in the database; env is at most a seed.
- **T3** behaviour tunable / feature flag — the dashboard is the management
  surface. `T3 → dashboard` marks a setting that already has a
  `dashboard_settings` column of the same name: the dashboard value wins and
  the variable is a deprecated fallback. `T3 (env, migrating)` marks the
  remaining env-only backlog.
- **T4** incident debug — env allowed, dashboard toggle recommended.

## `PORT` (special case, no prefix)

The listen port (default `2455`) is read from the bare `PORT` process
environment variable, not a `CODEX_LB_*` setting, and applies to host
(uvx/local) runs only — env files map only prefixed variables. In Docker the
container always listens on 2455 (the entrypoint pins `--port 2455`); change
the host side of the compose `ports` mapping instead.

## `CODEX_LB_ENV_FILE` (special case, bootstrap only)

`.env` / `.env.local` are discovered next to the installed module root (the
repository checkout). `CODEX_LB_ENV_FILE` — an `os.pathsep`-separated list
of paths — overrides that discovery for installs whose module root cannot
contain env files (the Nix package wrapper points it at the launch
directory). It must be set in the process environment, not in an env file:
the env-file locations have to be known before env files are read.

## `CODEX_LB_WORKERS_PER_INSTANCE` (special case, startup guard)

Not a setting: the only supported value is `1` (one worker process per
instance), so there is nothing to configure. If it is set to anything else,
startup fails with a settings validation error — per-account concurrency caps
are partitioned per replica via the bridge ring, and multiple worker processes
inside one instance would silently multiply them. Scale horizontally via
replicas instead.

## Process-level environment variables (not settings)

These are third-party or POSIX conventions codex-lb honors without
making them settings. They are read by their owning launcher, library, or
frozen migration rather than through `Settings`. Together with `Settings`
itself this table is the allowlist of environment reads under `app/`;
anything else belongs in `app/core/config/settings.py`.

| Environment variable(s) | Consumer |
| --- | --- |
| `HOST`, `PORT`, `SSL_CERTFILE`, `SSL_KEYFILE`, `UVICORN_TIMEOUT_KEEP_ALIVE`, `UVICORN_WS_MAX_SIZE` | Uvicorn launch defaults read once by the `codex-lb` CLI (`app/cli.py`); host runs only. |
| `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `WS_PROXY`, `NO_PROXY` (and lowercase) | Outbound proxy conventions honored by httpx/aiohttp/websockets for upstream egress. |
| `REQUEST_METHOD` | CGI marker; when present `HTTP_PROXY` is ignored (httpoxy guard, mirrors the httpx/requests rule). |
| `TZ` | POSIX process timezone; automation schedules with the `server_default` timezone resolve to it (falling back to the host local zone, then UTC). |
| `PROMETHEUS_MULTIPROC_DIR` | prometheus_client multiprocess-mode convention. |
| `GITHUB_TOKEN` | Optional bearer token for the GitHub latest-release version check. |
| `POD_IP`, `POD_NAME`, `HOSTNAME`, `KUBERNETES_SERVICE_HOST` | Kubernetes/pod identity used for multi-replica validation and deployment-kind telemetry. |
| `CODEX_HOME`, `USERPROFILE`, `WSL_DISTRO_NAME` | Codex CLI home discovery for the `codex-lb codex-sessions retag` tool. |
| `CODEX_LB_TEST_DATABASE_URL` | Test-suite/CI only: overrides the database used by the test session factory. |
| `CODEX_LB_ADDITIONAL_QUOTA_REGISTRY_FILE` (inside `app/db/alembic/versions/**` only) | Frozen Alembic migrations read the process environment directly because migrations must not depend on `Settings`; the live setting of the same name is documented in the tables below. |

## Core

| Environment variable | Tier | Type | Default |
| --- | --- | --- | --- |
| `CODEX_LB_DATA_DIR` | T0 | `Path` | `~/.codex-lb` (host) / `/var/lib/codex-lb` (container) |

## Database

| Environment variable | Tier | Type | Default |
| --- | --- | --- | --- |
| `CODEX_LB_DATABASE_ALEMBIC_AUTO_REMAP_ENABLED` | T0 | `bool` | `True` |
| `CODEX_LB_DATABASE_MAX_OVERFLOW` | T1 | `int` | `15` |
| `CODEX_LB_DATABASE_MIGRATE_ON_STARTUP` | T0 | `bool` | `True` |
| `CODEX_LB_DATABASE_MIGRATION_LOCK_TIMEOUT_SECONDS` | T0 | `float` | `300.0` |
| `CODEX_LB_DATABASE_MIGRATIONS_FAIL_FAST` | T0 | `bool` | `True` |
| `CODEX_LB_DATABASE_POOL_SIZE` | T1 | `int` | `25` |
| `CODEX_LB_DATABASE_SQLITE_PRE_MIGRATE_BACKUP_ENABLED` | T0 | `bool` | `True` |
| `CODEX_LB_DATABASE_SQLITE_PRE_MIGRATE_BACKUP_MAX_FILES` | T0 | `int` | `5` |
| `CODEX_LB_DATABASE_SQLITE_STARTUP_CHECK_MODE` | T0 | `'quick' \| 'full' \| 'off'` | `'quick'` |
| `CODEX_LB_DATABASE_URL` | T0 | `str` | `sqlite+aiosqlite:///<data_dir>/store.db` |

## Encryption

| Environment variable | Tier | Type | Default |
| --- | --- | --- | --- |
| `CODEX_LB_ENCRYPTION_KEY_FILE` | T0 | `Path` | `<data_dir>/encryption.key` |
| `CODEX_LB_ENCRYPTION_KEY_FINGERPRINT_MODE` | T0 | `'enforce' \| 'warn' \| 'off'` | `'enforce'` |

## Upstream transport

| Environment variable | Tier | Type | Default |
| --- | --- | --- | --- |
| `CODEX_LB_UPSTREAM_BASE_URL` | T1 | `str` | `'https://chatgpt.com/backend-api'` |
| `CODEX_LB_UPSTREAM_CONNECT_TIMEOUT_SECONDS` | T3 (dashboard) | `float` | `8.0` |
| `CODEX_LB_UPSTREAM_ROUTE_CACHE_TTL_SECONDS` | T1 | `float` | `60.0` |
| `CODEX_LB_UPSTREAM_WEBSOCKET_TRUST_ENV` | T1 | `bool` | auto-detected from outbound proxy env vars |

## HTTP & streaming

| Environment variable | Tier | Type | Default |
| --- | --- | --- | --- |
| `CODEX_LB_COMPACT_REQUEST_BUDGET_SECONDS` | T3 (dashboard) | `float` | `180.0` |
| `CODEX_LB_HTTP_CONNECTOR_LIMIT` | T1 | `int` | `100` |
| `CODEX_LB_HTTP_CONNECTOR_LIMIT_PER_HOST` | T1 | `int` | `50` |
| `CODEX_LB_HTTP_RESPONSES_STREAM_REQUEST_BUDGET_SECONDS` | T3 (dashboard) | `float` | `7200.0` |
| `CODEX_LB_SSE_KEEPALIVE_INTERVAL_SECONDS` | T3 (dashboard) | `float` | `10.0` |
| `CODEX_LB_STREAM_IDLE_TIMEOUT_SECONDS` | T3 (dashboard) | `float` | `7200.0` |
| `CODEX_LB_TRANSCRIPTION_REQUEST_BUDGET_SECONDS` | T3 (dashboard) | `float` | `120.0` |

## HTTP Responses session bridge

| Environment variable | Tier | Type | Default |
| --- | --- | --- | --- |
| `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_ADVERTISE_BASE_URL` | T1 | `str \| None` | `None` |
| `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_CODEX_PREWARM_ENABLED` | T3 (dashboard) | `bool` | `False` |
| `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_ENABLED` | T4 | `bool` | `True` |
| `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_ID` | T1 | `str` | process hostname |
| `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INSTANCE_RING` | T1 | `list[str]` | `[]` |
| `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_MAX_SESSIONS` | T1 | `int` | `256` |
| `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_OPERATION_EVENT_SPOOL_BATCH_SIZE` | T1 | `int` | `32` |
| `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_OPERATION_EVENT_SPOOL_FLUSH_INTERVAL_SECONDS` | T1 | `float` | `0.1` |
| `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_OPERATION_EVENT_SPOOL_MAX_BYTES` | T1 | `int` | `2097152` |
| `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_OPERATION_EVENT_SPOOL_MAX_PENDING_BYTES` | T1 | `int` | `33554432` |
| `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_OPERATION_EVENT_SPOOL_MAX_PENDING_EVENTS` | T1 | `int` | `2048` |
| `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_OPERATION_SPOOL_FORMAT` | T1 | `'rows_v1' \| 'chunks_v2'` | `'rows_v1'` |
| `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_OPERATION_SPOOL_RETENTION_SECONDS` | T3 (dashboard) | `float` | `604800` |
| `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_QUEUE_LIMIT` | T1 | `int` | `8` |
| `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_REQUEST_BUDGET_SECONDS` | T3 (dashboard) | `float` | `7200.0` |

## Proxy admission & account caps

| Environment variable | Tier | Type | Default |
| --- | --- | --- | --- |
| `CODEX_LB_PROXY_ACCOUNT_CAP_PARTITION_SCALE_DOWN_SECONDS` | T1 | `int` | `60` |
| `CODEX_LB_PROXY_ACCOUNT_CAPS_SCOPE` | T1 | `'partitioned' \| 'replica'` | `'partitioned'` |
| `CODEX_LB_PROXY_ACCOUNT_ERROR_RATE_WEIGHTING_ENABLED` | T3 (dashboard) | `bool` | `True` |
| `CODEX_LB_PROXY_ACCOUNT_INFLIGHT_PENALTY_PCT` | T3 (dashboard) | `float` | `2.5` |
| `CODEX_LB_PROXY_ACCOUNT_LEASE_TOKEN_WEIGHT` | T3 (dashboard) | `float` | `1.0` |
| `CODEX_LB_PROXY_ACCOUNT_LEASE_TTL_SECONDS` | T3 (dashboard) | `float` | `900.0` |
| `CODEX_LB_PROXY_ACCOUNT_RESPONSE_CREATE_LIMIT` | T3 (dashboard) | `int` | `4` |
| `CODEX_LB_PROXY_ACCOUNT_STREAM_LIMIT` | T3 (dashboard) | `int` | `8` |
| `CODEX_LB_PROXY_ACCOUNT_STREAM_RECOVERY_RESERVE` | T3 (dashboard) | `int` | `1` |
| `CODEX_LB_PROXY_API_KEY_FAIR_SHARE_CONGESTION_THRESHOLD_PCT` | T3 (dashboard) | `int` | `0` |
| `CODEX_LB_PROXY_DOWNSTREAM_WEBSOCKET_IDLE_TIMEOUT_SECONDS` | T3 (dashboard) | `float` | `120.0` |
| `CODEX_LB_PROXY_OVERLOAD_ISOLATION_SECONDS` | T3 (dashboard) | `int` | `1800` |
| `CODEX_LB_PROXY_REQUEST_BUDGET_SECONDS` | T3 (dashboard) | `float` | `600.0` |
| `CODEX_LB_PROXY_RESPONSE_CREATE_LIMIT` | T1 | `int` | `256` |
| `CODEX_LB_PROXY_UNAUTHENTICATED_CLIENT_CIDRS` | T1 | `list[str]` | `[]` |

## OAuth

| Environment variable | Tier | Type | Default |
| --- | --- | --- | --- |
| `CODEX_LB_OAUTH_CALLBACK_HOST` | T1 | `str` | `127.0.0.1` (host) / `0.0.0.0` (container) |

## Token refresh

| Environment variable | Tier | Type | Default |
| --- | --- | --- | --- |
| `CODEX_LB_AUTH_GUARDIAN_ENABLED` | T3 (dashboard) | `bool` | `True` |

## Usage

| Environment variable | Tier | Type | Default |
| --- | --- | --- | --- |
| `CODEX_LB_ADDITIONAL_QUOTA_REGISTRY_FILE` | T1 | `Path \| None` | `None` |
| `CODEX_LB_RATE_LIMIT_RESET_CREDITS_REFRESH_ENABLED` | T3 (dashboard) | `bool` | `True` |

## Model registry

| Environment variable | Tier | Type | Default |
| --- | --- | --- | --- |
| `CODEX_LB_MODEL_CONTEXT_WINDOW_OVERRIDES` | T3 (dashboard) | `dict[str, int]` | `{}` |
| `CODEX_LB_MODEL_REGISTRY_CLIENT_VERSION` | T1 | `str` | `'0.154.0'` |
| `CODEX_LB_MODEL_REGISTRY_SNAPSHOT_MAX_AGE_SECONDS` | T1 | `int` | `86400` |

## Firewall

| Environment variable | Tier | Type | Default |
| --- | --- | --- | --- |
| `CODEX_LB_FIREWALL_IP_CACHE_TTL_SECONDS` | T1 | `int` | `30` |
| `CODEX_LB_FIREWALL_TRUST_PROXY_HEADERS` | T1 | `bool` | `False` |
| `CODEX_LB_FIREWALL_TRUSTED_PROXY_CIDRS` | T1 | `list[str]` | `['127.0.0.1/32', '::1/128']` |
| `FORWARDED_ALLOW_IPS` (alias `CODEX_LB_FORWARDED_ALLOW_IPS`) | T1 | `str \| None` | `None` |

## Dashboard

| Environment variable | Tier | Type | Default |
| --- | --- | --- | --- |
| `CODEX_LB_CONNECT_ADDRESS` | T1 | `str \| None` | `None` |
| `CODEX_LB_DASHBOARD_AUTH_MODE` | T1 | `'standard' \| 'trusted_header' \| 'disabled'` | `'standard'` |
| `CODEX_LB_DASHBOARD_AUTH_PROXY_GROUPS_HEADER` | T1 | `str` | `'Remote-Groups'` |
| `CODEX_LB_DASHBOARD_AUTH_PROXY_HEADER` | T1 | `str` | `'Remote-User'` |
| `CODEX_LB_DASHBOARD_BOOTSTRAP_TOKEN` | T0 | `str \| None` | `None` |
| `CODEX_LB_DASHBOARD_TRUST_LOOPBACK_HOST_HEADER_FOR_LONG_SESSIONS` | T1 | `bool` | `False` |

## Conversation archive

| Environment variable | Tier | Type | Default |
| --- | --- | --- | --- |
| `CODEX_LB_CONVERSATION_ARCHIVE_DIR` | T1 | `Path` | `<data_dir>/conversation-archive` |
| `CODEX_LB_CONVERSATION_ARCHIVE_ENABLED` | T3 (dashboard) | `bool` | `False` |
| `CODEX_LB_CONVERSATION_ARCHIVE_QUEUE_MAX_BYTES` | T1 | `int` | `268435456` |

## Schedulers

| Environment variable | Tier | Type | Default |
| --- | --- | --- | --- |
| `CODEX_LB_AUTOMATIONS_SCHEDULER_ENABLED` | T3 (dashboard) | `bool` | `True` |

## Multi-replica

| Environment variable | Tier | Type | Default |
| --- | --- | --- | --- |
| `CODEX_LB_LEADER_ELECTION_ENABLED` | T1 | `bool` | `True` |
| `CODEX_LB_LEADER_ELECTION_TTL_SECONDS` | T1 | `int` | `60` |

## Observability

| Environment variable | Tier | Type | Default |
| --- | --- | --- | --- |
| `CODEX_LB_LOG_FORMAT` | T1 | `'text' \| 'json'` | `'text'` |
| `CODEX_LB_METRICS_ENABLED` | T1 | `bool` | `False` |
| `CODEX_LB_METRICS_PORT` | T1 | `int` | `9090` |
| `CODEX_LB_OTEL_ENABLED` | T1 | `bool` | `False` |
| `CODEX_LB_OTEL_EXPORTER_ENDPOINT` | T1 | `str` | `''` |
| `CODEX_LB_TRACE` | T4 | `str` | `''` |

## Resilience & load shedding

| Environment variable | Tier | Type | Default |
| --- | --- | --- | --- |
| `CODEX_LB_BACKPRESSURE_MAX_CONCURRENT_REQUESTS` | T1 | `int` | `0` |
| `CODEX_LB_BULKHEAD_DASHBOARD_LIMIT` | T1 | `int` | `50` |
| `CODEX_LB_BULKHEAD_PROXY_LIMIT` | T1 | `int` | `512` |
| `CODEX_LB_CIRCUIT_BREAKER_ENABLED` | T3 (dashboard) | `bool` | `False` |
| `CODEX_LB_DETERMINISTIC_FAILOVER_ENABLED` | T3 (dashboard) | `bool` | `True` |
| `CODEX_LB_MEMORY_REJECT_THRESHOLD_MB` | T1 | `int` | `0` |
| `CODEX_LB_SHUTDOWN_DRAIN_TIMEOUT_SECONDS` | T1 | `int` | `30` |
| `CODEX_LB_SOFT_DRAIN_ENABLED` | T3 (dashboard) | `bool` | `True` |

## Other

| Environment variable | Tier | Type | Default |
| --- | --- | --- | --- |
| `CODEX_LB_EVENT_LOOP_LAG_WARN_THRESHOLD_SECONDS` | T1 | `float` | `0.5` |
| `CODEX_LB_TELEMETRY_ENABLED` | T3 (dashboard) | `bool \| None` | `None` |
| `CODEX_LB_TELEMETRY_ENDPOINT` | T1 | `str` | `'https://telemetry.tokmaxxing.com'` |
| `CODEX_LB_THREAD_CACHE_IDENTITY_MODE` | T3 (dashboard) | `str` | `'shared'` |
| `CODEX_LB_TIMEOUT_INVARIANT_VALIDATION_STRICT` | T4 | `bool` | `False` |

## Removed

Images and default account probes choose `gpt-5.6-luna`, then `gpt-5.5`,
using registry plan visibility and suppression. If neither qualifies, they
use `gpt-5.6-luna`. Catalog visibility does not guarantee account access.
There is no host-model setting. See the
[Images spec](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/images-api-compat)
and [probe spec](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/usage-refresh-policy).

Removed settings (ignored with a one-release startup warning; each is now a
fixed default or a dashboard runtime setting — see PRINCIPLES.md P2 /
issue [#1340](https://github.com/Soju06/codex-lb/issues/1340)):

- `CODEX_LB_REQUEST_LOG_RETENTION_DAYS`
- `CODEX_LB_USAGE_HISTORY_RETENTION_DAYS`
- `CODEX_LB_HTTP_DOWNSTREAM_TRANSPORT_POLICY`
- `CODEX_LB_OPENAI_CACHE_AFFINITY_MAX_AGE_SECONDS`
- `CODEX_LB_WARMUP_MODEL`
- `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_GATEWAY_SAFE_MODE`
- `CODEX_LB_UPSTREAM_STREAM_TRANSPORT`
- `CODEX_LB_UPSTREAM_COMPACT_TIMEOUT_SECONDS`
- `CODEX_LB_MAX_SSE_EVENT_BYTES`
- `CODEX_LB_UPSTREAM_RESPONSE_CREATE_MAX_BYTES`
- `CODEX_LB_OAUTH_TIMEOUT_SECONDS`
- `CODEX_LB_TOKEN_REFRESH_TIMEOUT_SECONDS`
- `CODEX_LB_TOKEN_REFRESH_CLAIM_TTL_SECONDS`
- `CODEX_LB_PROXY_REFRESH_FAILURE_COOLDOWN_SECONDS`
- `CODEX_LB_PROXY_ADMISSION_WAIT_TIMEOUT_SECONDS`
- `CODEX_LB_USAGE_FETCH_TIMEOUT_SECONDS`
- `CODEX_LB_USAGE_FETCH_MAX_RETRIES`
- `CODEX_LB_USAGE_REFRESH_ENABLED`
- `CODEX_LB_USAGE_REFRESH_INTERVAL_SECONDS`
- `CODEX_LB_USAGE_REFRESH_AUTH_FAILURE_COOLDOWN_SECONDS`
- `CODEX_LB_LIVE_USAGE_INGESTION_ENABLED`
- `CODEX_LB_RATE_LIMIT_RESET_CREDITS_REFRESH_INTERVAL_SECONDS`
- `CODEX_LB_STICKY_SESSION_CLEANUP_ENABLED`
- `CODEX_LB_QUOTA_PLANNER_SCHEDULER_ENABLED`
- `CODEX_LB_MODEL_REGISTRY_ENABLED`
- `CODEX_LB_MAX_DECOMPRESSED_BODY_BYTES`
- `CODEX_LB_MAX_DECOMPRESSED_RESPONSES_BODY_BYTES`
- `CODEX_LB_IMAGE_INLINE_FETCH_ENABLED`
- `CODEX_LB_IMAGE_INLINE_ALLOWED_HOSTS`
- `CODEX_LB_IMAGES_DEFAULT_MODEL`
- `CODEX_LB_OPENAI_PROMPT_CACHE_KEY_DERIVATION_ENABLED`
- `CODEX_LB_PROXY_TOKEN_REFRESH_LIMIT`
- `CODEX_LB_PROXY_UPSTREAM_WEBSOCKET_CONNECT_LIMIT`
- `CODEX_LB_PROXY_COMPACT_RESPONSE_CREATE_LIMIT`
- `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_IDLE_TTL_SECONDS`
- `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_CODEX_IDLE_TTL_SECONDS`
- `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_STUCK_GATE_RETIRE_AFTER_SECONDS`
- `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_ANCHOR_POISON_FAILURE_THRESHOLD`
- `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_SERVER_RECOVERY_MAX_ATTEMPTS`
- `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_CLEAN_CLOSE_RETRY_JITTER_MAX_SECONDS`
- `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_OPERATION_LEDGER_ENABLED`
- `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_AMBIGUOUS_CONTINUATION_RECOVERY_MODE`
- `CODEX_LB_TOKEN_REFRESH_INTERVAL_DAYS`

---

*Specs: [user-documentation](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/user-documentation) · [responses-api-compat](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/responses-api-compat) · [rate-limit-reset-credits](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/rate-limit-reset-credits) · [deployment-installation](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/deployment-installation) · [proxy-runtime-observability](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/proxy-runtime-observability)*
