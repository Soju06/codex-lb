# Atomic Quota and Warmup Budget Claims

## Why

Warmup execution can double-spend real account quota and operator credit
budgets in multi-replica deployments. The quota-planner daily budget gate is a
non-atomic check-then-act that counts only `executed` decisions and cost rows
written after completion, so concurrent claims (leader scheduler plus
`POST /api/quota-planner/warm-now` on any replica) both pass at budget-1 and
in-flight `executing` probes are invisible. Limit-warmup dedup relies on
`SELECT .. FOR UPDATE` (a no-op on SQLite) plus a per-process lock, so two
processes sharing one SQLite file can insert near-duplicate attempts whose
`reset_at` drifted within the tolerance window and both send real probes.
Finally, `QuotaPlannerRepository.log_decision` is SELECT-then-INSERT on a
UNIQUE `idempotency_key`; concurrent leaders raise an unhandled
`IntegrityError` that aborts the rest of the planning tick, dropping due
warmups for other accounts.

## What Changes

- Make the quota-planner planned->executing claim the single authoritative
  budget enforcement point: one conditional UPDATE whose WHERE clause embeds
  both daily budget guards, serialized on PostgreSQL by
  `pg_advisory_xact_lock` on a fixed warmup-budget key and on SQLite by
  single-statement single-writer atomicity.
- Count in-flight work: the budget count includes `status='executing'`
  decisions in addition to `status='executed'` (by `executed_at >= since`),
  so a probe reserves budget when claimed, not after it finishes. The claim
  stamps its own timestamp into `executed_at` (overwritten with the completion
  time when the probe finishes), and executing rows count against the day they
  were claimed, with a `created_at` fallback for legacy executing rows claimed
  before stamping existed — `created_at` alone would let a decision planned
  before midnight but claimed after midnight escape the new day's budget.
- Replace `LimitWarmupRepository.try_create_attempt`'s FOR UPDATE +
  SELECT-then-INSERT with a single conditional
  `INSERT .. SELECT .. WHERE NOT EXISTS(tolerance-window match)`;
  PostgreSQL additionally takes `pg_advisory_xact_lock` keyed on
  `(account_id, window)`. No new unique constraint.
- Convert `QuotaPlannerRepository.log_decision` to a dialect upsert
  (`INSERT .. ON CONFLICT (idempotency_key) DO NOTHING` plus a fetch of the
  surviving row) so concurrent writers converge instead of raising and
  aborting the tick.
- No Alembic migration: existing tables and columns suffice.

## Impact

- Affected specs: `quota-phase-planner`, `usage-refresh-policy`
- Affected code: `app/modules/quota_planner/repository.py`,
  `app/modules/quota_planner/warmup.py`,
  `app/modules/limit_warmup/repository.py`
- Operator-visible: daily warmup count/credit caps hold under concurrent
  replicas; duplicate-key planner ticks no longer abort mid-tick.
