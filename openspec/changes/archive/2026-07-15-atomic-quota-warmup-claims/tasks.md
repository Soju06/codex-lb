# Tasks: atomic-quota-warmup-claims

## 1. Quota planner repository

- [x] 1.1 Add `claim_warmup_decision(decision_id, *, since, max_warmups, max_credits)`:
      one conditional `UPDATE .. SET status='executing'` whose WHERE clause embeds
      the `planned` precondition plus both daily budget guards; PostgreSQL takes
      `pg_advisory_xact_lock` on the fixed warmup-budget key first, SQLite relies
      on single-statement single-writer atomicity; issue the claim in a fresh
      transaction and commit immediately.
- [x] 1.2 Add `count_active_warmups_since(since)` counting `executed` (by
      `executed_at`) plus in-flight `executing` warmup decisions by the claim
      timestamp the claim stamps into `executed_at` (with a `created_at`
      fallback for legacy executing rows without a claim stamp); keep
      `count_executed_warmups_since` for display.
- [x] 1.3 Add `get_decision_fresh(decision_id)` (identity-map bypass via
      `populate_existing`) for post-refusal re-reads.
- [x] 1.4 Convert `log_decision` to a dialect-dispatched
      `INSERT .. ON CONFLICT (idempotency_key) DO NOTHING` upsert that returns
      the surviving row instead of raising `IntegrityError` mid-tick.

## 2. Warmup service

- [x] 2.1 Replace the unguarded `update_decision_status(planned -> executing)`
      in `warm_now` with `claim_warmup_decision`.
- [x] 2.2 Downgrade the `_execution_gate` budget reads to an advisory pre-check
      that uses `count_active_warmups_since` so in-flight probes are visible.
- [x] 2.3 Add `_resolve_refused_claim`: fresh re-read to distinguish a
      concurrent claim from a budget refusal, then CAS the decision to
      `skipped` with `daily_warmup_count_budget_exhausted` vs
      `daily_warmup_credit_budget_exhausted`.

## 3. Limit warmup repository

- [x] 3.1 Replace `try_create_attempt`'s FOR UPDATE + SELECT-then-INSERT with a
      single conditional `INSERT .. SELECT .. WHERE NOT EXISTS(tolerance-window
      match)`; PostgreSQL additionally takes `pg_advisory_xact_lock` keyed on
      `(account_id, window)`; keep the exact-tuple unique constraint
      IntegrityError -> None backstop.

## 4. Tests

- [x] 4.1 Two-replica regression tests over the shared test database:
      concurrent `warm_now` respects the daily count budget (at most one probe),
      claim guard counts in-flight `executing` decisions, claim guard refuses on
      spent credit budget at the service path with the credit-specific reason,
      concurrent `log_decision` on one idempotency key converges without
      aborting, and two-process limit-warmup attempts within the reset_at
      tolerance dedup to a single row.
- [x] 4.3 Cross-midnight regression: a warmup decision created before the
      daily boundary but claimed after it counts against the claim day's
      budget and blocks a second same-day claim.
- [x] 4.2 Update the existing bound-parameter regression test for
      `log_decision`'s new insert construct.

## 5. OpenSpec

- [x] 5.1 Spec deltas for `quota-phase-planner` (atomic budget claim,
      idempotency-key convergence) and `usage-refresh-policy` (cross-process
      warm-up dedup).
- [x] 5.2 `openspec validate atomic-quota-warmup-claims` passes.
