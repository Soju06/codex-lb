## 1. Pre-implementation evidence

- [ ] 1.1 Measure production hourly distinct dimension-combination cardinality (`bucket, account_id, api_key_id, model, service_tier, request_kind, deleted?`); if >1,000/hour, demote `service_tier` to a satellite before authoring the schema.
- [x] 1.2 Re-verify the Alembic single head is `20260722_000000_backfill_request_log_useragent_families` immediately before authoring the revision.

## 2. Schema and migration (PR1)

- [x] 2.1 Declare `RequestUsageHourlyRollup`, `RequestUsageHourlyErrorRollup`, and `RequestDemandQuarterRollup` models plus the `hourly_folded_through` state column in `app/db/models.py` (BIGINT epoch bucket keys, `''` sentinels for nullable dimensions, no secondary indexes).
- [x] 2.2 Author guarded, idempotent, DDL-only revision `20260724_000000_add_request_usage_time_rollups` (inspector-checked creates, batch_alter_table on SQLite, guarded downgrade); leave `folded_through` and lifetime rollups untouched.
- [x] 2.3 Migration round-trip test: upgrade → schema assertions → downgrade → re-upgrade idempotence on SQLite; confirm the PostgreSQL drift contract covers the new models.

## 3. Hourly fold pass (PR1)

- [x] 3.1 Implement `run_hourly_fold_pass` in a new `app/modules/accounts/usage_time_rollup.py`: fold-state row lock, watermark re-read after lock, unconditional `min(requested_at)` start jump, 48 h slices / ≤20 per pass, half-open hour-aligned windows, defensive DELETE then three INSERT..SELECT statements (hourly, error satellite with the top-error filter reproduced, quarter demand), watermark advance in the same transaction.
- [x] 3.2 Wire the pass into `AccountUsageRollupScheduler._fold_once` directly after `run_fold_pass()` (no new scheduler, leadership, or settings surface).
- [x] 3.3 Fold correctness tests: idempotent re-run equality, crash-resume equality, hour-boundary attribution, exact-watermark row lands in tail, slice boundary never splits a bucket, `FOLD_LAG` respected, hour-aligned watermark invariant; dimension seeds for warmup kinds, soft-deleted, NULL account/key/tier, duplicate rows, reasoning-only, NULL cost, cached>input, multiple error codes.

## 4. Lifecycle mirroring and retention gate (PR1)

- [x] 4.1 Add `lock_fold_state()` to `AccountsRepository.delete()` and mirror soft delete (bucket-wise merge-add to `(account_id='', is_deleted=true)` then delete source rows) and hard delete (row deletion) across all three rollup tables in the same transaction.
- [x] 4.2 Mirror duplicate-account consolidation (bucket-wise merge-add dup→canonical, delete dup rows) in the existing consolidation transaction.
- [x] 4.3 Document the history-rewrite discipline (fold-state lock + rollup mirror for any mutation below the watermark) in the module docstring.
- [x] 4.4 Gate `_prune_request_logs` on `min(folded_through, hourly_folded_through)` including the two-fold-lag currency check; tests for the three branches (hourly missing/behind → skip, both current → prune below min−lag, one stalled → skip) and post-prune statistics invariance.
- [x] 4.5 Lifecycle tests: soft delete preserves totals under the deleted dimension, hard delete removes them, consolidation reattributes them; fold/mirror interleaving is serialized (two sessions).

## 5. Read-path switch (PR2)

- [ ] 5.1 Implement the shared merge helper (single-statement watermark+rollup read, hour-aligned tail partition, three-segment non-aligned `since`, Python dict-add merge) in a new module.
- [ ] 5.2 Switch `request_logs.aggregate_by_bucket`, `aggregate_activity_since/_between` (conversation distinct counts stay raw in a separate statement), `top_error_since/_between`, and `earliest_activity_at` (raw first, hour-precision rollup fallback).
- [ ] 5.3 Switch `quota_planner.aggregate_demand_bins` (quarter satellite, `is_deleted = false`, raw tail; `DemandBinLike` shape preserved with None for unused fields, call sites untouched).
- [ ] 5.4 Switch `api_keys.trends_by_key` (hourly native granularity, `output_or_reasoning_tokens`).
- [ ] 5.5 Parity harness: synthetic 10-day corpus covering every task-3.3 edge, watermark ∈ {epoch, mid-history hour, target} × all six paths, exact dataclass equality vs the legacy raw readers (cost via approx), one API-level JSON equivalence case; retention simulation (physically prune below min watermark − lag) with switched outputs unchanged and conversation metrics asserted as expectedly raw-bound.
- [ ] 5.6 Concurrency tests: fold commit injected between the reader's two statements leaves totals invariant; two concurrent fold passes serialize without double counting.
- [ ] 5.7 Escape-hatch test: full rollup delete + watermark reset in one transaction → immediate legacy-equivalent reads → re-backfill converges.

## 6. Verification and evidence

- [ ] 6.1 Register the new test files in Makefile `POSTGRES_PYTEST_TARGETS`; run the suites on SQLite and PostgreSQL.
- [ ] 6.2 Capture performance evidence (PR body, not a gate): EXPLAIN (ANALYZE, BUFFERS) and timings for overview 30 d and demand 28 d, rollup vs raw, on a ~1M-row PostgreSQL fixture; backfill duration and per-slice memory.
- [ ] 6.3 Run Ruff format/check, `ty`, `scripts/check_proxy_architecture.py`, simplicity gates, and strict OpenSpec validation.
- [ ] 6.4 Verify every scenario in the spec deltas against implementation evidence; adversarial review of the standalone diff.
