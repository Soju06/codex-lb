# Design: SQLite Row-Capping & Cutoff Support for `bulk_history_since`

## Problem & Motivation

On SQLite deployments, every dashboard projection poll calls `UsageRepository.bulk_history_since` with:
- `cutoffs`: per-account lookbacks (e.g. 5 hours for standard accounts, up to 7 days for weekly accounts).
- `per_account_row_cap`: 64 rows (sized for projection EWMA tail replay).
- `uncapped_recent_floor`: `now - max(smoothing_window, fleet_burn_window)` (~3 hours).

PostgreSQL executes `_bulk_history_since_capped_postgresql` via `JOIN LATERAL` probes over covering index `idx_usage_window_account_time_covering`.

In contrast, SQLite currently ignores all three parameters (`cutoffs`, `per_account_row_cap`, and `uncapped_recent_floor`). It executes `_bulk_history_since_sqlite`, which:
1. Performs a full range scan across the global 7-day floor (`recorded_at >= since`) across all accounts.
2. Runs the Python-registered SQLite aggregate `clb_bulk_history_digest` over hundreds of thousands of rows on every poll to compute a SHA-256 digest of `(id, account_id, used_percent, recorded_at, reset_at, window_minutes)`.
3. Materializes and caches hundreds of thousands of `UsageHistorySnapshot` instances in `_BULK_HISTORY_SQLITE_CACHE`.
4. Clones all snapshots on each poll.

On a database with 1,000,000 history rows across 20 accounts (e.g. 616 MB `store.db`), this scans all 1,000,000 rows on every poll, taking >1,000 ms, spiking CPU, and driving process RSS from ~40 MB to 200–500 MB+.

## SQLite Index Inventory

SQLite already has composite indexes defined in existing migrations and `app/db/models.py`:
- `idx_usage_window_account_latest` on `(coalesce("window", 'primary'), account_id, recorded_at DESC, id DESC)`
- `idx_usage_window_raw_account_latest` on `("window", account_id, recorded_at DESC, id DESC)`
- `idx_usage_window_account_time_covering` on `(coalesce("window", 'primary'), account_id, recorded_at ASC)`
- `idx_usage_window_raw_account_time_covering` on `("window", account_id, recorded_at ASC)`

Because the index includes `(window, account_id, recorded_at DESC, id DESC)`, SQLite can seek directly into the B-tree by `(window, account_id)` and read backwards from the latest timestamp, terminating as soon as `LIMIT per_account_row_cap` rows are reached. No temporary tables or sort B-trees are created.

## Architectural Decision & Alternatives

### Option A: SQL Window Function Query (`ROW_NUMBER() OVER ...`)
- **Concept**: Execute a single query with `ROW_NUMBER() OVER (PARTITION BY account_id ORDER BY recorded_at DESC, id DESC) AS rn ... WHERE rn <= 64`.
- **Benchmark & Analysis**: SQLite 3.25+ query planner cannot push the row number predicate down into the index scan. It scans every row matching `recorded_at >= since` across all accounts into a temporary table, computes row numbers, and filters afterwards.
- **Latency**: 180.35 ms for 1 account (>3,500 ms for 20 accounts).
- **Verdict**: Rejected due to high CPU and temp storage overhead.

### Option B: Direct Per-Account Indexed Probes in Python/sqlite3
- **Concept**: Follow the pattern established by `_latest_by_account_sqlite` (`app/modules/usage/repository.py:373`). Offload execution to a threadpool via `to_thread.run_sync`.
- **Query Strategy**: For each account:
  - If `uncapped_recent_floor is None`: single backward seek with `LIMIT per_account_row_cap`.
  - If `uncapped_recent_floor is not None` and `uncapped_floor > cutoff`: `UNION ALL` of the capped tail `[cutoff, uncapped_floor)` (`LIMIT per_account_row_cap`) and uncapped recent rows `>= uncapped_floor`.
  - If `uncapped_floor <= cutoff`: single uncapped forward scan `>= cutoff`.
- **Benchmark**: On 1,000,000 rows across 20 accounts, total execution time across all 20 accounts is 3.08 ms, returning 2,280 rows and consuming <1 MB memory.
- **Verdict**: Accepted as the optimal SQLite architecture.

### Option C: Clean Dialect Branching in Repository
PostgreSQL benefits from `JOIN LATERAL` on covering indexes. SQLite benefits from direct per-account B-tree seeks. Attempting a common denominator query would cripple PostgreSQL performance. A clean branch in `UsageRepository.bulk_history_since`:
- Calls `_bulk_history_since_capped_sqlite` when `per_account_row_cap is not None and sqlite_path is not None`.
- Calls `_bulk_history_since_capped_postgresql` when `per_account_row_cap is not None and dialect == "postgresql"`.
- Preserves `_bulk_history_since_sqlite` unchanged when `per_account_row_cap is None`.

## Detailed Query Structure

In `_bulk_history_since_capped_sqlite`:
Connections use `sqlite3.connect` with `detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES`, `PRAGMA query_only=ON`, and `PRAGMA busy_timeout=30000`.

### 1. Window Predicates
```python
if window == "primary":
    window_clause = "coalesce(window, 'primary') = 'primary'"
    window_params = []
else:
    window_clause = "window = ?"
    window_params = [window]
```

### 2. Capped Only Query (`uncapped_recent_floor is None`)
```sql
SELECT id, account_id, used_percent, recorded_at, reset_at, window_minutes
FROM usage_history
WHERE account_id = ?
  AND {window_clause}
  AND recorded_at >= ?
ORDER BY recorded_at DESC, id DESC
LIMIT ?
```
Params: `[account_id, *window_params, cutoff_param, per_account_row_cap]`.

### 3. Compound Floor Query (`uncapped_floor > cutoff`)
```sql
SELECT id, account_id, used_percent, recorded_at, reset_at, window_minutes
FROM (
    SELECT id, account_id, used_percent, recorded_at, reset_at, window_minutes
    FROM usage_history
    WHERE account_id = ?
      AND {window_clause}
      AND recorded_at >= ?
      AND recorded_at < ?
    ORDER BY recorded_at DESC, id DESC
    LIMIT ?
)
UNION ALL
SELECT id, account_id, used_percent, recorded_at, reset_at, window_minutes
FROM usage_history
WHERE account_id = ?
  AND {window_clause}
  AND recorded_at >= ?
```
Params: `[account_id, *window_params, cutoff_param, floor_param, per_account_row_cap, account_id, *window_params, floor_param]`.

### 4. All Recent Query (`uncapped_floor <= cutoff`)
```sql
SELECT id, account_id, used_percent, recorded_at, reset_at, window_minutes
FROM usage_history
WHERE account_id = ?
  AND {window_clause}
  AND recorded_at >= ?
ORDER BY recorded_at ASC, id ASC
```
Params: `[account_id, *window_params, cutoff_param]`.

### 5. Sorting Contract
Python sorts each account's hydrated snapshots oldest-first by `(snapshot.recorded_at, snapshot.id)` before returning, matching the exact contract guaranteed by PostgreSQL's `_bulk_history_since_capped_postgresql`.

## In-Memory Session Fallback

When `sqlite_path is None` (e.g. `:memory:` databases), `UsageRepository.bulk_history_since` executes the existing SQLAlchemy query and applies the cutoff and row cap slicing in Python:
- For `uncapped_recent_floor is None`: `snapshots[-per_account_row_cap:]`
- For `uncapped_recent_floor is not None`: `tail[-per_account_row_cap:] + recent` where `recent` are rows `>= eff_floor` and `tail` are rows `< eff_floor`.

## Verification Strategy

1. Integration parity tests:
   - `test_bulk_history_since_per_account_row_cap_keeps_newest_rows`: asserts `[15.0, 16.0, 17.0]` on both PostgreSQL and SQLite.
   - `test_bulk_history_since_row_cap_respects_per_account_cutoffs_postgresql`: dialect skip removed, verified on SQLite.
   - `test_bulk_history_since_row_cap_exempts_uncapped_recent_floor_postgresql`: dialect skip removed, verified on SQLite.
2. Query plan verification:
   - `test_bulk_history_since_capped_query_plan_is_indexed_sqlite`: executes `EXPLAIN QUERY PLAN` on primary and secondary window queries, verifying `USING INDEX` on `idx_usage_window_*` and ensuring no `USE TEMP B-TREE`.
3. Uncapped cache regression:
   - All existing tests for `_bulk_history_since_sqlite` cache keys, digest invalidation, and incremental append detection continue to pass untouched.
