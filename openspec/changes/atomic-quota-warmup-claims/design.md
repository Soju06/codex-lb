# Design: atomic-quota-warmup-claims

## Context

Three verified race conditions in warmup execution paths, all triggered by
multi-replica (or multi-process SQLite) deployments:

1. `QuotaWarmupService._execution_gate` reads
   `count_executed_warmups_since(today)` and `warmup_cost_since(today)` and
   compares them to `max_warmups_per_day` / `max_warmup_credits_per_day`
   before executing. Nothing serializes this across replicas, and decisions
   currently `executing` are invisible to the gate.
2. `LimitWarmupRepository.try_create_attempt` serializes its tolerant
   check-and-insert with `SELECT .. FOR UPDATE` (a no-op on SQLite) plus a
   per-process anyio lock; two processes sharing one SQLite file can both
   insert near-duplicate attempts within the reset_at tolerance window.
3. `QuotaPlannerRepository.log_decision` is SELECT-then-INSERT on a UNIQUE
   `idempotency_key`; a concurrent writer raises an unhandled
   `IntegrityError` that aborts the rest of `run_once`.

## Decisions

### 1. Budget claim = conditional UPDATE with embedded guards (no ledger table)

`QuotaWarmupService.warm_now` claims via a new repo method
`claim_warmup_decision(decision_id, *, since, max_warmups, max_credits)`:

```sql
UPDATE quota_planner_decisions
SET status='executing', reason='warmup_executing', executed_at=:claimed_at
WHERE id=:id AND status='planned'
  AND (SELECT count(*) FROM quota_planner_decisions d
       WHERE d.action='warmup'
         AND ((d.status='executed' AND d.executed_at >= :since)
           OR (d.status='executing'
               AND (d.executed_at >= :since
                 OR (d.executed_at IS NULL AND d.created_at >= :since))))) < :max_warmups
  AND (SELECT coalesce(sum(cost_usd), 0.0) FROM request_logs
       WHERE request_kind='warmup' AND requested_at >= :since
         AND deleted_at IS NULL) < :max_credits
RETURNING id
```

The claim stamps its own timestamp into `executed_at` (no new column; the
completion/failure transition overwrites it with the final execution time), so
in-flight `executing` rows count against the day they were **claimed**. The
scheduler persists future-scheduled decisions whose `created_at` can precede
the daily boundary; counting executing rows by `created_at` let a decision
planned before midnight but claimed after midnight escape the new day's count
budget and reopen the double-spend window. The `executed_at IS NULL AND
created_at >= :since` arm only covers legacy executing rows claimed by a
version that predates claim stamping (e.g. during a rolling upgrade).

Backend split:

- **PostgreSQL**: `SELECT pg_advisory_xact_lock(hashtext('quota_planner:warmup_budget'))`
  executes in the same transaction immediately before the UPDATE. Required
  because under READ COMMITTED two concurrent UPDATEs on *different* decision
  rows each evaluate the count subquery against their own snapshot and could
  both pass; the xact lock serializes claims and releases at commit, so the
  second claimant's statement snapshot sees the first's committed
  `executing` row.
- **SQLite**: no lock needed — the whole UPDATE (subqueries included)
  executes atomically under SQLite's database-level single-writer lock, safe
  across processes sharing the file (same argument as
  `DatabaseRateLimiter.check_and_increment`). The claim is issued at the
  start of a fresh transaction (any open transaction on the session is
  committed first) and committed immediately, matching the existing
  `update_decision_status` commit-per-op pattern and avoiding stale-snapshot
  BUSY errors under WAL.

Rejected alternatives: (a) budget-ledger row with CAS — new table +
migration + midnight-rollover logic for no added safety; (b) advisory lock
around the existing separate gate reads — fixes PG but leaves SQLite
cross-process racy since plain SELECTs take no write lock; (c) counting
`executing` rows by `scheduled_at` — executing rows may have NULL
`scheduled_at` on manual warm-now; (d) counting `executing` rows by
`created_at` — server-defaulted and always present, but keyed to the decision
creation day rather than the claim day, so scheduler-planned decisions
crossing midnight escaped the count guard; (e) a dedicated `claimed_at`
column — needs a migration for a timestamp `executed_at` already represents
once the claim stamps it.

Failure-reason mapping: when the claim UPDATE returns no row, re-read the
decision with `populate_existing` (the identity map may hold a stale
snapshot); if `status != 'planned'` it was a concurrent claim (return that
status, existing behavior); if still `planned`, re-run the count to pick
`daily_warmup_count_budget_exhausted` vs
`daily_warmup_credit_budget_exhausted` and CAS the decision to `skipped`
with `expected_status='planned'`.

`_execution_gate` keeps its cheap non-budget checks and an advisory
(non-authoritative) budget pre-check for friendly early skips; the pre-check
now uses `count_active_warmups_since` (executing + executed) so in-flight
probes are visible even before the claim. `count_executed_warmups_since`
stays for display.

Accepted residuals (documented in context.md):

- A replica that crashes mid-probe leaves an `executing` decision consuming
  one count-budget slot until local midnight — conservative direction
  (under-spend, never over-spend).
- The credit budget still cannot see cost of in-flight probes (rows land
  post-completion); the read-check race is closed on both backends and
  overshoot is bounded by remaining count budget x per-probe cost
  (~32 input / 8 output tokens).

### 2. try_create_attempt = single conditional INSERT..SELECT

Replace the FOR UPDATE + `_existing_attempt` + add with:

```sql
INSERT INTO account_limit_warmups (account_id, window, reset_at, status, model, attempted_at)
SELECT :vals WHERE NOT EXISTS (
  SELECT 1 FROM account_limit_warmups
  WHERE account_id=:a AND window=:w
    AND reset_at BETWEEN :reset_at - :tol AND :reset_at + :tol)
RETURNING id
```

- **SQLite**: atomic under the single-writer lock across processes, fixing
  the FOR UPDATE no-op without relying on the per-process
  `sqlite_writer_section` (kept only as a local write-throttle).
- **PostgreSQL**: a single INSERT..SELECT is NOT self-sufficient under READ
  COMMITTED (two concurrent statements can both pass NOT EXISTS), so take
  `pg_advisory_xact_lock(hashtext('limit_warmup:' || :account_id || ':' || :window))`
  first — replacing the per-account-row FOR UPDATE with the repo's
  established advisory-lock idiom, narrowed to (account, window).

Keep the IntegrityError -> rollback -> None fallback (exact-key constraint
`uq_account_limit_warmups_account_window_reset` remains the backstop).
Rejected: bucketed generated-column unique constraint — needs a migration on
both backends and fails when reset_at values straddle a bucket boundary
while still inside the tolerance (e.g. R=999, R'=1001, tolerance 5); the
BETWEEN guard has no boundary hole.

### 3. log_decision = ON CONFLICT DO NOTHING upsert

Use `sqlalchemy.dialects.postgresql.insert` / `sqlite.insert`
(dialect-dispatched like `aggregate_demand_bins` already does) with
`on_conflict_do_nothing(index_elements=['idempotency_key'])` + RETURNING id;
when no row returns, SELECT the surviving row by `idempotency_key`. Both
backends support this natively (SQLite >= 3.24). Concurrent leaders converge
on one row; `run_once` no longer aborts mid-tick. Rejected: try/except
IntegrityError retry — leaves the session in a rolled-back state mid-tick.

### 4. No migration, no new tables

This change adds no Alembic revision; existing tables/columns suffice, so
there is no chaining or single-head risk with parallel branches.

### 5. Performance

Zero hot-proxy-path impact — all touched code runs in the quota-planner
scheduler tick (300s), the dashboard warm-now route, and the usage-refresh
warmup evaluation. PG adds one advisory-lock round-trip per warmup claim /
per attempt insert; log_decision goes from 2 statements to 1-2.

## Deviations from the reviewed design

- The original reviewed design artifact (scratchpad JSON) was lost when the
  first implementation session was interrupted; this design.md, written by
  that session from the same artifact, is the surviving authoritative record
  and the implementation follows it.
- Test plan item (e) called for an additional service-level limit-warmup
  test asserting only one `_send_warmup` task is spawned; the repository is
  the enforcement point and is covered with two-replica regression tests at
  the repository product path (the scheduler wrapper delegates 1:1), so the
  service-level duplicate-task assertion is left as a follow-up.
- The concurrent count-budget race test lets the second replica be refused
  at the (now in-flight-aware) gate rather than forcing both legs past the
  gate simultaneously; a separate claim-level test patches the gate to
  simulate simultaneous stale gate reads so the UPDATE guard itself is
  exercised for the credit budget, and an in-flight-`executing` test
  exercises the count guard directly at the claim.
- Regression tests simulate two processes by patching the module-level
  `sqlite_writer_section` binding to a no-op (separate processes never share
  that lock); the three product-path tests were verified to fail against the
  pre-change code (two near-duplicate warm-up attempts persisted, both
  replicas' probes sent past the count budget, duplicate idempotency-key
  insert raised).
