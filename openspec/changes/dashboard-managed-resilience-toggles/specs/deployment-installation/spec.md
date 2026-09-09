## MODIFIED Requirements

### Requirement: Removed tunables are fixed constants or derived values

Values that are protocol constants or internal tuning details SHALL NOT be
operator-configurable. When a previously supported `CODEX_LB_*` setting is
removed from the configuration surface, its environment variable MUST be
ignored without failing startup, and for at least one release after removal,
startup MUST emit a single warning log listing every removed setting name
found in the process environment (never the values), referencing the
simplicity principle that motivated the removal. Each subsystem affected by
a removal MUST retain at most one enable/disable setting, and the Helm chart
MUST NOT render environment variables for removed settings.

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

Incident-debugging trace logging SHALL be controlled by the single
`CODEX_LB_TRACE` comma-separated channel list, whose empty default disables
all trace channels. The Codex HTTP-bridge prewarm rollout scoping SHALL NOT
be operator-configurable: prewarm eligibility MUST be the
`CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_CODEX_PREWARM_ENABLED` flag alone,
with no canary sampling percent and no API-key allow/deny cohort lists (the
removed `..._PREWARM_CANARY_PERCENT`, `..._PREWARM_ALLOW_API_KEY_IDS`, and
`..._PREWARM_DENY_API_KEY_IDS` variables are covered by the
removed-settings warning). `database_pool_size` and `database_max_overflow`
MUST remain operator-configurable settings, and `soft_drain_enabled` and
`deterministic_failover_enabled` MUST remain the failover subsystem's only
enable switches. Those two switches and `circuit_breaker_enabled` are
managed from the dashboard (`dashboard_settings` columns of the same name,
`account-routing` / `outbound-http-clients`); their `CODEX_LB_*` variables
are deprecated aliases that apply only while the dashboard value is unset
and are removed in the next minor release via the removed-settings warning.

#### Scenario: Removed env vars are ignored with one startup warning

- **GIVEN** a deployment whose environment still sets removed settings such
  as `CODEX_LB_AUTH_BASE_URL` and `CODEX_LB_TOKEN_REFRESH_CLAIM_WAIT_SECONDS`
- **WHEN** the application starts
- **THEN** startup succeeds and the fixed built-in values are used
- **AND** exactly one warning log lists both removed names without their
  values

#### Scenario: Clean environment starts without removal warnings

- **GIVEN** a deployment that sets no removed setting names
- **WHEN** the application starts
- **THEN** no removed-settings warning is logged

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

#### Scenario: Removed scheduler and images env vars are ignored with one startup warning

- **GIVEN** a deployment whose environment still sets removed settings such
  as `CODEX_LB_QUOTA_PLANNER_TICK_SECONDS` and `CODEX_LB_IMAGES_HOST_MODEL`
- **WHEN** the application starts
- **THEN** startup succeeds and the fixed built-in values are used
- **AND** exactly one warning log lists both removed names without their
  values

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
- **THEN** it contains no `CODEX_LB_CIRCUIT_BREAKER_FAILURE_THRESHOLD`,
  `CODEX_LB_CIRCUIT_BREAKER_RECOVERY_TIMEOUT_SECONDS`, or
  `CODEX_LB_STICKY_SESSION_CLEANUP_INTERVAL_SECONDS` entries
- **AND** startup emits no removed-settings warning

#### Scenario: Removed pool and drain env vars are ignored with one startup warning

- **GIVEN** a deployment whose environment still sets removed settings such
  as `CODEX_LB_DATABASE_POOL_RECYCLE_SECONDS` and
  `CODEX_LB_DRAIN_PRIMARY_THRESHOLD_PCT`
- **WHEN** the application starts
- **THEN** startup succeeds and the fixed built-in values are used
- **AND** exactly one warning log lists both removed names without their
  values

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

#### Scenario: Removed prewarm canary env vars are ignored with one startup warning

- **GIVEN** a deployment whose environment still sets
  `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_CODEX_PREWARM_CANARY_PERCENT` or
  the allow/deny list variables
- **WHEN** the application starts
- **THEN** startup succeeds and the values are ignored
- **AND** exactly one warning log lists the removed names without their
  values

#### Scenario: Prewarm eligibility is the enabled flag alone

- **GIVEN** `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_CODEX_PREWARM_ENABLED=true`
- **WHEN** a first-turn Codex bridge request arrives on a session that has
  not been prewarmed
- **THEN** the session prewarm is attempted for that request
- **AND** no request is excluded by canary sampling or an allow/deny cohort

#### Scenario: Prewarm stays off by default

- **GIVEN** a default install with no prewarm variables set
- **WHEN** Codex bridge requests are served
- **THEN** no session prewarm is attempted and visible requests record
  `prewarm_status=not_applicable`
