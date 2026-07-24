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

- [x] 5.1 Implement the shared merge helper (single-statement watermark+rollup read, hour-aligned tail partition, three-segment non-aligned `since`, Python dict-add merge) in a new module.
- [x] 5.2 Switch `request_logs.aggregate_by_bucket`, `aggregate_activity_since/_between` (conversation distinct counts stay raw in a separate statement), `top_error_since/_between`, and `earliest_activity_at` (raw first, hour-precision rollup fallback).
- [x] 5.3 Switch `quota_planner.aggregate_demand_bins` (quarter satellite, `is_deleted = false`, raw tail; folded bins reconstruct the full legacy grain from the satellite dimensions — see D5; call sites untouched).
- [x] 5.4 Switch `api_keys.trends_by_key` (hourly native granularity, `output_or_reasoning_tokens`).
- [x] 5.5 Parity harness: synthetic 10-day corpus covering every task-3.3 edge, watermark ∈ {epoch, mid-history hour, target} × all six paths, exact dataclass equality vs the legacy raw readers (cost via approx), one API-level JSON equivalence case; retention simulation (physically prune below min watermark − lag) with switched outputs unchanged and conversation metrics asserted as expectedly raw-bound.
- [x] 5.6 Concurrency tests: fold commit injected between the reader's two statements leaves totals invariant; two concurrent fold passes serialize without double counting.
- [x] 5.7 Escape-hatch test: full rollup delete + watermark reset in one transaction → immediate legacy-equivalent reads → re-backfill converges.

## 6. Verification and evidence

- [x] 6.1 Register the new test files in Makefile `POSTGRES_PYTEST_TARGETS`; run the suites on SQLite and PostgreSQL.
- [x] 6.2 Capture performance evidence (PR body, not a gate): EXPLAIN (ANALYZE, BUFFERS) and timings for overview 30 d and demand 28 d, rollup vs raw, on a ~1M-row PostgreSQL fixture; backfill duration and per-slice memory. (Measured on postgres:18, 1M rows/60 d, adversarially high per-hour cardinality: overview 30 d raw 15.5 s w/ temp spill → rollup 2.2 s no spill; demand 28 d raw 21.5 s → quarter rollup 0.7 s; one 48 h fold slice ≈ 90 ms, so a 60 d backfill is ~30 slices of DB work. Per-slice RSS not separately profiled — the slice aggregate is a bounded PostgreSQL HashAggregate.)
- [x] 6.3 Run Ruff format/check, `ty`, `scripts/check_proxy_architecture.py`, simplicity gates, and strict OpenSpec validation.
- [ ] 6.4 Verify every scenario in the spec deltas against implementation evidence; adversarial review of the standalone diff. (Scenario-to-test mapping done for the read-path requirements; the adversarial diff review runs with the PR's local codex review.)

## 7. Review-round fixes (local codex, 2026-07-24)

- [x] 7.1 [P1] Quarter demand rollup regains the full legacy grain (`api_key_id`, `model`, `reasoning_effort`, `status` dimensions): `_bin_demand_units` applies `max()` per bin before summing, so the coarser `(slot, account, kind)` fold shrank folded-history forecasts. Schema, migration (with pre-merge-shape rebuild guard), fold, read path, and exact-grain parity updated.
- [x] 7.2 [P2] `update_model_for_request` now takes the fold-state lock and rewrites only rows un-folded by every rollup — `requested_at > folded_through` (lifetime folds `(start, end]`) AND `>= hourly_folded_through` (hourly folds `[start, end)`); round 2 corrected `min` → `max` direction, round 3 corrected the lifetime bound's inclusivity — a client-reused request id can no longer mutate folded history unmirrored.
- [x] 7.3 [P2] `cached_input_tokens_clamped` fold now matches `cached_input_tokens_from_log` exactly: a NULL input keeps the non-negative cached value instead of clamping it to zero (permanent-column undercount).
- [x] 7.4 [P2, round 2] `earliest_activity_at` no longer rounds down pre-retention: the hour-precision rollup fallback applies only when the earliest folded bucket is strictly below the surviving raw row's own bucket.
- [x] 7.5 [P2, round 2 — REBUTTED, no code change] "Partial edge buckets lost after retention": the ≤1 h-per-edge undercount for unaligned windows over retention-pruned history is the documented D7 decision (sub-hour boundary contributions cannot exist in hourly rollups; serving the whole edge hour instead would over-count). Deterministic, bounded, only affects windows older than the retention period, and asserted as the `leadless` expectation in the retention parity test.
