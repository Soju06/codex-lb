# Tasks

## 1. Fold lag

- [x] 1.1 Shorten `FOLD_LAG` to 2h and rewrite the sizing comment around the
      actual invariant: `requested_at` is stamped at insert time inside
      `add_log`, so the lag bounds insert-visibility skew (clock skew +
      commit latency + stalls), not request duration; note the measured
      production worst case (7.9s over 6.0M rows) and the fenced post-insert
      mutators
- [x] 1.2 Verify the watermark jump after the lag change is absorbed by the
      ordinary backfill path with totals unchanged (regression test
      `test_fold_absorbs_widened_watermark_gap`)

## 2. Summary TTL cache

- [x] 2.1 Cache `list_request_usage_summary_by_account` results per
      account-id signature for a fixed 30s TTL with a bounded entry count,
      mirroring the request-log COUNT cache; non-positive TTL bypasses
- [x] 2.2 Clear the cache on account deletion and on duplicate-identity
      consolidation (both re-attribute usage), alongside the existing
      `_clear_bulk_history_since_sqlite_cache()` call sites
- [x] 2.3 Zero the TTL for the test suite via an autouse conftest fixture so
      summaries stay exact within a test (same pattern as the COUNT cache)

## 3. Tests

- [x] 3.1 Cache behavior: staleness within TTL, per-signature keying,
      invalidation on delete and on consolidation
- [x] 3.2 Re-anchor the rollup/retention parity corpus on an explicit
      `CORPUS_FOLD_LAG = 24h` pin — its 10-day geometry (TARGET_W, prune
      floor, unaligned windows) was authored against the old lag and the
      parity semantics are lag-independent
- [x] 3.3 Fix the lag-coupled backdated insert in
      `test_account_delete_removes_rollup_row` (`now - FOLD_LAG / 2`)
- [x] 3.4 `uv run pytest`, `uv run ruff check`, `uv run ruff format --check`

## 4. Spec

- [x] 4.1 Correct the fold safety-lag scenario in `query-caching` and add the
      summary-cache allowance to the account summary requirement
- [x] 4.2 `openspec validate bound-account-summary-live-tail --strict`
