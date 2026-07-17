# Context: reduce-settings-surface-phase-3

## Rationale

Phase 3 of issue #1340, following the same selection rule as phases 1 and
2: every removed field keeps its exact previous default as the new fixed
value, and the only behavioral seam (the removed-settings warning) is
additive. None of this batch's env names were rendered by the Helm chart
or listed in `.env.example`, so no deployment artifacts change.

Capability choice mirrors the earlier phases: `deployment-installation`
owns the operator env-var contract at settings-load time, so the phase-3
fixed-values requirement is added there alongside the phase-1 and phase-2
requirements. Unlike phase 2, this batch also touches normative main-spec
text: three `database-backends` requirements named the removed settings,
so the change carries MODIFIED deltas for them.

## Removed fields (env names)

Database pool tuning (`app/db/session.py`):

- `CODEX_LB_DATABASE_BACKGROUND_POOL_SIZE` (derived: always
  `database_pool_size`; the previous default was already "unset = derive")
- `CODEX_LB_DATABASE_BACKGROUND_MAX_OVERFLOW` (derived: always
  `database_max_overflow`)
- `CODEX_LB_DATABASE_POOL_TIMEOUT_SECONDS` (fixed: 30.0,
  `_POSTGRES_POOL_TIMEOUT_SECONDS`)
- `CODEX_LB_DATABASE_POOL_RECYCLE_SECONDS` (fixed: 1800,
  `_POSTGRES_POOL_RECYCLE_SECONDS`)

Soft-drain/probe thresholds (already constants in
`app/core/balancer/logic.py`; the settings plumbing is deleted):

- `CODEX_LB_DRAIN_PRIMARY_THRESHOLD_PCT` (fixed: 85.0)
- `CODEX_LB_DRAIN_SECONDARY_THRESHOLD_PCT` (fixed: 90.0)
- `CODEX_LB_DRAIN_ERROR_WINDOW_SECONDS` (fixed: 60.0)
- `CODEX_LB_DRAIN_ERROR_COUNT_THRESHOLD` (fixed: 2)
- `CODEX_LB_PROBE_QUIET_SECONDS` (fixed: 60.0)
- `CODEX_LB_PROBE_SUCCESS_STREAK_REQUIRED` (fixed: 3)

Kept deliberately: `CODEX_LB_DATABASE_POOL_SIZE` and
`CODEX_LB_DATABASE_MAX_OVERFLOW` — the one genuine PostgreSQL HA sizing
decision. Operators must budget
`(pool_size + max_overflow) x maxReplicas <= max_connections`, and the
Helm chart pins both values (`config.databasePoolSize=5`,
`config.databaseMaxOverflow`), so removing them would break real
deployments' connection budgets. `CODEX_LB_SOFT_DRAIN_ENABLED` and
`CODEX_LB_DETERMINISTIC_FAILOVER_ENABLED` stay as the failover
subsystem's switches.

## Background-pool decision

`database_background_pool_size` / `database_background_max_overflow`
defaulted to `None` = "use the main pool settings", and nothing in the
repository, Helm chart, docs, or `.env.example` ever set them. The
background engine's purpose is isolation of background-task checkouts
from the request pool (see the detached-session requirements in
`database-backends`), not independent sizing — a smaller auxiliary pool
was a hypothetical tuning nobody exercised. The derivation is now
unconditional, which also collapses the `background` branch out of the
PostgreSQL engine-kwargs helper: both engines are built from one code
path, so pre-ping/recycle regressions (#672) can no longer diverge
between them.

## Drain-threshold decision

The drain/probe thresholds encode the deterministic-failover design
(drain at 85%/90% used, back off after 2 errors in 60 s, probe after a
60 s quiet window, recover after 3 successful probes). They interlock —
raising one without the others degrades failover in non-obvious ways —
and `app/core/balancer/logic.py` already declared the same values as
constants used for `evaluate_health_tier` parameter defaults, so the
settings were a second source of truth for numbers that must not drift.
The function keeps its full parameter surface (tests and the replica-local
health-tier machinery pass explicit values), but production call sites now
rely on the constant defaults. Per-replica drain/probe state semantics
(`account-routing` spec) are unchanged.

## Example

An operator running `CODEX_LB_PROBE_QUIET_SECONDS=30` upgrades: startup
logs

```
removed setting(s) ignored: CODEX_LB_PROBE_QUIET_SECONDS — values are now fixed; see PRINCIPLES.md P2 / issue #1340
```

and drained accounts enter the probing tier after the fixed 60-second
quiet window.
