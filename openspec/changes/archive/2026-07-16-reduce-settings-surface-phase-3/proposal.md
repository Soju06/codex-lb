# Change: reduce-settings-surface-phase-3

## Why

Phase 3 of issue #1340 (simplicity backlog: settings-surface reduction) and
PRINCIPLES.md P2 ("a setting the operator never needs to touch is a default
in disguise"). After phase 2 (`reduce-settings-surface-phase-2`) the
`Settings` class still carried 127 env-settable fields. This batch covers
audit items 5 and 6: database pool tuning nobody sizes independently of the
main pool, and the soft-drain/probe thresholds, which encode failover
invariants rather than deployment decisions.

## What Changes

Phase 3 removes 10 more fields (127 -> 117), with no behavior change for
default installs.

- **DB pool tuning (4 removed)**: `database_background_pool_size` and
  `database_background_max_overflow` are removed; the background-task
  engine now always derives its pool sizing from `database_pool_size` and
  `database_max_overflow` (the previous default when the overrides were
  unset). `database_pool_timeout_seconds` (fixed 30.0) and
  `database_pool_recycle_seconds` (fixed 1800) become the
  `_POSTGRES_POOL_TIMEOUT_SECONDS` / `_POSTGRES_POOL_RECYCLE_SECONDS`
  constants in `app/db/session.py`. `database_pool_size` (15) and
  `database_max_overflow` (10) stay: PostgreSQL HA operators must be able
  to budget `(pool_size + max_overflow) x replicas` against the server's
  `max_connections`, and the Helm chart pins both
  (`config.databasePoolSize`, `config.databaseMaxOverflow`).
- **Soft-drain/probe thresholds (6 removed)**:
  `drain_primary_threshold_pct` (85.0), `drain_secondary_threshold_pct`
  (90.0), `drain_error_window_seconds` (60.0),
  `drain_error_count_threshold` (2), `probe_quiet_seconds` (60.0), and
  `probe_success_streak_required` (3) are removed. The identical constants
  already existed in `app/core/balancer/logic.py`
  (`DRAIN_PRIMARY_THRESHOLD_PCT` etc.) as `evaluate_health_tier` parameter
  defaults; `app/modules/proxy/load_balancer.py` simply stops forwarding
  settings values and relies on those defaults, making `logic.py` the
  single source of truth. `soft_drain_enabled` and
  `deterministic_failover_enabled` remain the only switches.
- **One-release removal warning**: the phase-3 env names join
  `_REMOVED_SETTINGS`, so startup logs the existing single WARN when any
  of them are still set (`extra="ignore"` already makes them inert).

## Impact

- Affected specs: `deployment-installation` (new requirement covering the
  phase-3 fixed values and the background-pool derivation);
  `database-backends` (three requirements modified to drop the removed
  setting names in favor of the fixed constants and the unconditional
  background-pool derivation); non-normative wording update in
  `openspec/specs/database-backends/context.md`.
- Affected code: `app/core/config/settings.py`, `app/db/session.py`,
  `app/modules/proxy/load_balancer.py`.
- Operator impact: none for default installs. Deployments that set a
  removed env var keep working on the fixed value and see one startup
  WARN. The Helm chart never rendered any of the removed names, so no
  chart changes are needed.
- Proxy-failover invariants: every fixed drain/probe value equals the
  previous settings default, `evaluate_health_tier` keeps its full
  parameter surface for tests and future callers, and drain/probe/failover
  behavior is byte-identical for installs that never overrode the
  thresholds.
- Not in scope: further settings-surface phases tracked in #1340 (the
  issue stays open).
