## ADDED Requirements

### Requirement: Phase-3 removed tunables are fixed constants or derived values

The phase-3 internals SHALL NOT be operator-configurable: the PostgreSQL
pool checkout timeout MUST be fixed at 30 seconds and the pooled-connection
recycle window at 1800 seconds; the background-task database engine MUST
always derive its pool size and max overflow from `database_pool_size` and
`database_max_overflow`; and the soft-drain/probe thresholds (primary drain
threshold 85%, secondary drain threshold 90%, error window 60 seconds,
error count 2, probe quiet window 60 seconds, probe success streak 3) MUST
each be fixed at its previously documented default in
`app/core/balancer/logic.py`. `database_pool_size` and
`database_max_overflow` MUST remain operator-configurable settings, and
`soft_drain_enabled` and `deterministic_failover_enabled` MUST remain the
failover subsystem's enable switches. The removed phase-3 environment
variable names MUST be covered by the existing removed-settings startup
warning: they are ignored without failing startup and reported in the
single warning log (names only, never values) for at least one release.

#### Scenario: Phase-3 removed env vars are ignored with one startup warning

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
