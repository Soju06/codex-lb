## ADDED Requirements

### Requirement: Removed tunables are fixed constants, derived values, or dashboard settings

Values that are protocol constants or internal tuning details SHALL NOT be
operator-configurable, and values the dashboard runtime settings own SHALL NOT
also be operator-configurable through the environment. When a previously
supported `CODEX_LB_*` setting is removed from the configuration surface, its
environment variable MUST be ignored without failing startup, and for at
least one release after removal, startup MUST emit a single warning log
listing every removed setting name found in the process environment or the
loaded env files (never the values), referencing the simplicity principle
that motivated the removal. Once a removed name has had its warning release,
it MUST be pruned from the warning list while staying inert (`extra="ignore"`);
the warning list therefore covers only the most recent removal batch. Each
subsystem affected by a removal MUST retain at most one enable/disable
setting, and the Helm chart MUST NOT render environment variables for removed
settings.

The following values MUST be fixed at their previously documented defaults:

- The OAuth protocol identity values (authorization base URL, client id,
  originator, scope, redirect URI, and callback port): they identify
  codex-lb to OpenAI exactly like the Codex CLI, and changing any of them
  breaks login.
- Background scheduler cadences (quota planner tick, automations poll,
  model registry refresh, sticky-session cleanup).
- The Codex client fingerprint (OS, architecture, terminal).
- Live-usage write coalescing (minimum write interval and queue size).
- The request-log count-cache TTL.
- Circuit-breaker tuning (failure threshold and recovery timeout).
- The images-route internals (internal host model and partial-images cap).
- The PostgreSQL pool checkout timeout (30 seconds) and pooled-connection
  recycle window (1800 seconds).
- The soft-drain/probe thresholds (primary drain threshold 85%, secondary
  drain threshold 90%, error window 60 seconds, error count 2, probe quiet
  window 60 seconds, probe success streak 3), fixed in
  `app/core/balancer/logic.py`.

The following values MUST be derived rather than configured:

- The memory-pressure warning threshold: 80% of the configurable reject
  threshold (`CODEX_LB_MEMORY_REJECT_THRESHOLD_MB`), with both disabled
  when the reject threshold is 0.
- The background-task database engine's pool size and max overflow: always
  taken from `database_pool_size` and `database_max_overflow`.

The following values MUST be owned by the dashboard runtime settings alone,
with the first-created settings row taking the column defaults (`smart`,
`1800`, `gpt-5.4-mini`, `false`) instead of an environment seed:
`http_downstream_transport_policy`, `openai_cache_affinity_max_age_seconds`,
`warmup_model`, and `http_responses_session_bridge_gateway_safe_mode`. The
retention windows (`request_log_retention_days`,
`usage_history_retention_days`) MUST be dashboard runtime settings with no
environment alias (see `data-retention`).

Incident-debugging trace logging SHALL be controlled by the single
`CODEX_LB_TRACE` comma-separated channel list, whose empty default disables
all trace channels. The Codex HTTP-bridge prewarm rollout scoping SHALL NOT
be operator-configurable: prewarm eligibility MUST be the
`CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_CODEX_PREWARM_ENABLED` flag alone,
with no canary sampling percent and no API-key allow/deny cohort lists.
`database_pool_size` and `database_max_overflow` MUST remain
operator-configurable settings, and `soft_drain_enabled` and
`deterministic_failover_enabled` MUST remain the failover subsystem's
enable switches.

#### Scenario: Removed env vars are ignored with one startup warning

- **GIVEN** a deployment whose environment still sets removed settings such
  as `CODEX_LB_REQUEST_LOG_RETENTION_DAYS` and `CODEX_LB_WARMUP_MODEL`
- **WHEN** the application starts
- **THEN** startup succeeds and the dashboard runtime values are used
- **AND** exactly one warning log lists both removed names without their
  values

#### Scenario: Clean environment starts without removal warnings

- **GIVEN** a deployment that sets no removed setting names
- **WHEN** the application starts
- **THEN** no removed-settings warning is logged

#### Scenario: Names past their warning release are silently inert

- **GIVEN** a deployment whose environment still sets names removed in an
  earlier batch, such as `CODEX_LB_AUTH_BASE_URL`,
  `CODEX_LB_QUOTA_PLANNER_TICK_SECONDS`, or
  `CODEX_LB_DATABASE_POOL_RECYCLE_SECONDS`
- **WHEN** the application starts
- **THEN** startup succeeds, the fixed built-in values are used
- **AND** no removed-settings warning is logged for those names

#### Scenario: Trace channels default to off

- **GIVEN** a default install with `CODEX_LB_TRACE` unset
- **WHEN** the proxy serves requests
- **THEN** no request-shape, payload, service-tier, or upstream trace logs
  are emitted

#### Scenario: A trace channel can be enabled for an incident

- **GIVEN** `CODEX_LB_TRACE=shape,upstream_payload`
- **WHEN** the proxy serves requests
- **THEN** request-shape and upstream-payload trace logs are emitted while
  all other trace channels stay off

#### Scenario: Memory warning threshold derives from the reject threshold

- **GIVEN** `CODEX_LB_MEMORY_REJECT_THRESHOLD_MB=100`
- **WHEN** process RSS reaches 80 MiB
- **THEN** a memory warning is logged while requests continue to be served
- **AND** requests are rejected with 503 only once RSS reaches 100 MiB

#### Scenario: Memory guard stays fully disabled by default

- **GIVEN** a default install with `CODEX_LB_MEMORY_REJECT_THRESHOLD_MB`
  unset (0)
- **WHEN** the proxy serves requests under any memory usage
- **THEN** no memory warning is logged and no request is rejected for
  memory pressure

#### Scenario: Helm chart renders no removed settings

- **GIVEN** a Helm install using the chart's default values
- **WHEN** the config map is rendered
- **THEN** it contains no `CODEX_LB_OPENAI_CACHE_AFFINITY_MAX_AGE_SECONDS`,
  `CODEX_LB_CIRCUIT_BREAKER_FAILURE_THRESHOLD`, or
  `CODEX_LB_STICKY_SESSION_CLEANUP_INTERVAL_SECONDS` entries
- **AND** startup emits no removed-settings warning

#### Scenario: Dashboard-owned columns seed from their defaults

- **GIVEN** a fresh database and `CODEX_LB_WARMUP_MODEL=gpt-5.4-nano` still
  set in the environment
- **WHEN** the dashboard settings row is created for the first time
- **THEN** `warmup_model` is `gpt-5.4-mini`, `http_downstream_transport_policy`
  is `smart`, and `openai_cache_affinity_max_age_seconds` is `1800`
- **AND** the startup warning names `CODEX_LB_WARMUP_MODEL`

#### Scenario: Fresh database bootstrap ignores a removed variable

- **GIVEN** an empty database and
  `CODEX_LB_OPENAI_CACHE_AFFINITY_MAX_AGE_SECONDS=64` still set in the
  environment
- **WHEN** the Alembic chain is upgraded to head
- **THEN** the seeded `dashboard_settings` row has
  `openai_cache_affinity_max_age_seconds` `1800`
- **AND** no migration reads the removed variable

#### Scenario: Removed names are matched case-insensitively

- **GIVEN** a deployment whose environment sets `codex_lb_warmup_model`
  in lowercase (which the former field honoured)
- **WHEN** the application starts
- **THEN** the startup warning lists `CODEX_LB_WARMUP_MODEL`

#### Scenario: Background pool sizing derives from the main pool settings

- **GIVEN** `CODEX_LB_DATABASE_POOL_SIZE=12` and
  `CODEX_LB_DATABASE_MAX_OVERFLOW=4` on a PostgreSQL deployment
- **WHEN** the application creates the background-task database engine
- **THEN** the background engine uses pool size 12 and max overflow 4
- **AND** no separate background pool sizing can be configured

#### Scenario: Drain and probe thresholds are fixed constants

- **GIVEN** a deployment with `soft_drain_enabled` left at its default
- **WHEN** an account's primary window usage reaches 85%
- **THEN** the account enters the draining health tier
- **AND** a drained account enters the probing tier only after the fixed
  60-second quiet window, regardless of any `CODEX_LB_PROBE_QUIET_SECONDS`
  value still present in the environment

#### Scenario: Prewarm stays off by default

- **GIVEN** a default install with no prewarm variables set
- **WHEN** Codex bridge requests are served
- **THEN** no session prewarm is attempted and visible requests record
  `prewarm_status=not_applicable`

#### Scenario: Prewarm eligibility is the enabled flag alone

- **GIVEN** `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_CODEX_PREWARM_ENABLED=true`
- **WHEN** a first-turn Codex bridge request arrives on a session that has
  not been prewarmed
- **THEN** the session prewarm is attempted for that request
- **AND** no request is excluded by canary sampling or an allow/deny cohort

## REMOVED Requirements

### Requirement: Removed tunables are fixed constants or derived values

**Reason**: The requirement's scenarios pinned the removed-settings startup warning to the July 2026 phase 1-4 names (`CODEX_LB_AUTH_BASE_URL`, `CODEX_LB_QUOTA_PLANNER_TICK_SECONDS`, `CODEX_LB_DATABASE_POOL_RECYCLE_SECONDS`, the prewarm canary variables). Their one-release warning window has passed (v1.22-v1.24 shipped), so those names are pruned from `_REMOVED_SETTINGS` and the scenarios can no longer hold as written.

**Migration**: Replaced by "Removed tunables are fixed constants, derived values, or dashboard settings" below, which keeps every fixed/derived value list, generalizes the warning scenarios to the current batch, and adds the pruning rule and the dashboard-owned columns.
