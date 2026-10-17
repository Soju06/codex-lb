# Change: Bound durable bridge spool cleanup

## Why

Durable HTTP bridge transcript retention currently drains every eligible
operation in one leader-gated scheduler pass. A large SQLite backlog can keep
that pass inside repeated delete transactions long enough to starve ordinary
database work and leader-lease renewal. Cleanup must continue making progress
without monopolizing the shared database.

## What Changes

- Limit each scheduler pass to fixed, small operation batches and a wall-clock
  budget, then resume any remaining backlog on a later tick.
- Emit low-cardinality logs and Prometheus metrics for cleanup duration,
  deleted operations, outcome, and whether the pass stopped with likely
  backlog remaining.
- Preserve the existing retention cutoff, owner fencing, terminal/nonterminal
  eligibility, and sticky-cleanup independence.

## Non-Goals

- Changing transcript format, compression, or the seven-day retention window.
- Changing request-log or usage-history retention defaults.
- Running SQLite vacuum or deleting production data outside normal retention.
- Adding operator configuration for cleanup tuning.

## Impact

- Affected specs: `responses-api-compat`, `proxy-runtime-observability`.
- Affected code: sticky-session cleanup scheduler, durable bridge repository
  call pattern, Prometheus metric declarations, focused unit tests.
- No API, schema, migration, or environment-variable change.
