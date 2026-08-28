## ADDED Requirements

### Requirement: Phase-2 removed tunables are fixed constants or derived values

The phase-2 internals SHALL NOT be operator-configurable: background
scheduler cadences (quota planner tick, automations poll, model registry
refresh, sticky-session cleanup), the Codex client fingerprint (OS,
architecture, terminal), live-usage write coalescing (minimum write
interval and queue size), the request-log count-cache TTL, circuit-breaker
tuning (failure threshold and recovery timeout), and the images-route
internals (internal host model and partial-images cap) MUST each be fixed
at its previously documented default. Each affected subsystem MUST retain at most one enable/disable
setting. The memory-pressure warning threshold MUST be derived as 80% of
the configurable reject threshold (`CODEX_LB_MEMORY_REJECT_THRESHOLD_MB`),
with both disabled when the reject threshold is 0. The removed phase-2
environment variable names MUST be covered by the existing removed-settings
startup warning: they are ignored without failing startup and reported in
the single warning log (names only, never values) for at least one release.
The Helm chart MUST NOT render environment variables for removed settings.

#### Scenario: Phase-2 removed env vars are ignored with one startup warning

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
