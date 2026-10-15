# Change: cap-sqlite-bulk-usage-history

## Why

In high-throughput or long-running SQLite deployments (such as containerized deployments where `store.db` grows to hundreds of megabytes), dashboard projection polling drives sustained high memory usage (consuming 200 MB to 500 MB+ of RSS) and CPU spikes.

While PostgreSQL implements `_bulk_history_since_capped_postgresql` via index-only lateral scans that bound rows per account by cutoff and `per_account_row_cap` older than `uncapped_recent_floor`, SQLite currently completely ignores `cutoffs`, `per_account_row_cap`, and `uncapped_recent_floor`. Instead, `UsageRepository.bulk_history_since` delegates to `_bulk_history_since_sqlite`, which:
1. Issues an unconstrained range scan across all accounts for the entire global 7-day floor (`recorded_at >= since`).
2. Evaluates a custom Python SQLite aggregate (`clb_bulk_history_digest`) across hundreds of thousands of rows on every dashboard poll.
3. Retains all history rows in memory in `_BULK_HISTORY_SQLITE_CACHE` and clones them on each poll.

On a 616 MB database with 1,000,000 history rows, this scans 1M rows taking ~1,036 ms per poll, causes lock contention, and allocates hundreds of megabytes of memory.

## What Changes

- `app/modules/usage/repository.py`:
  - Implement `_bulk_history_since_capped_sqlite` executing targeted per-account indexed seeks directly in `sqlite3` via `to_thread.run_sync` with `PRAGMA query_only=ON; PRAGMA busy_timeout=30000;`.
  - Handle `window == "primary"` (`coalesce(window, 'primary') = 'primary'`) and other windows (`window = ?`), utilizing existing composite indexes `idx_usage_window_account_latest` and `idx_usage_window_account_time_covering` (and their raw-window twins).
  - Compute per-account `cutoff = max(cutoffs.get(account_id, since), since) if cutoffs else since`.
  - If `uncapped_recent_floor is None`: execute backward seek ordered `recorded_at DESC, id DESC LIMIT per_account_row_cap`.
  - If `uncapped_recent_floor is not None`:
    - Derive `uncapped_floor = max(cutoff, uncapped_recent_floor)`.
    - If `uncapped_floor <= cutoff`: execute uncapped forward query `>= cutoff`.
    - Else: execute a `UNION ALL` of the capped tail `[cutoff, uncapped_floor)` bounded by `per_account_row_cap` and all uncapped rows `>= uncapped_floor`.
  - Sort each account's slice oldest-first `(snapshot.recorded_at, snapshot.id)` matching the PostgreSQL contract.
  - In `UsageRepository.bulk_history_since`: route to `_bulk_history_since_capped_sqlite` when `per_account_row_cap is not None` and `sqlite_path is not None`.
  - Handle in-memory SQLite sessions (`sqlite_path is None`) by applying cutoff and row-cap slicing to the session fallback.
  - Preserve the existing uncapped `_bulk_history_since_sqlite` snapshot cache path untouched when `per_account_row_cap is None`.

- `tests/integration/test_usage_repository.py`:
  - Update `test_bulk_history_since_per_account_row_cap_keeps_newest_rows` to assert that both PostgreSQL and SQLite return the identical newest three rows oldest-first.
  - Remove dialect skips from `test_bulk_history_since_row_cap_respects_per_account_cutoffs_postgresql` and `test_bulk_history_since_row_cap_exempts_uncapped_recent_floor_postgresql` so they execute on both SQLite and PostgreSQL.
  - Add `test_bulk_history_since_capped_query_plan_is_indexed_sqlite` to verify that SQLite uses composite indexes without temporary B-trees (`USE TEMP B-TREE`).

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `query-caching`: Update requirement "Projection history reads are bounded per account" to mandate that both PostgreSQL and SQLite bound rows per account by cutoff and newest-first per-account row cap older than the uncapped recent floor. Add SQLite scenario asserting composite index utilization without temporary B-trees.

## Impact

- Code: `app/modules/usage/repository.py`.
- Tests: `tests/integration/test_usage_repository.py`.
- Schema: No migrations or schema changes; relies on existing composite indexes `idx_usage_window_account_latest`, `idx_usage_window_raw_account_latest`, `idx_usage_window_account_time_covering`, and `idx_usage_window_raw_account_time_covering`.
- Performance: On 1M rows across 20 accounts, query time drops from ~1,036 ms to ~3.08 ms, reading ~2,280 rows instead of 1,000,000. Memory footprint drops from 200–500 MB to <1 MB per poll, eliminating memory bloat during dashboard polling on SQLite deployments.
