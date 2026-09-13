# Change: live-row-facet-indexes

## Why

The unfiltered `GET /api/request-logs/options` facets are computed with a recursive `facet_skip` CTE: one `min(column) WHERE deleted_at IS NULL AND column > previous` probe per distinct value (`query-caching`, "Unfiltered request-log filter options avoid full DISTINCT passes"). The indexes those probes run on today — `idx_logs_model_effort_time`, `idx_logs_status_error_time`, `idx_logs_api_key_time` — carry soft-deleted rows too, so a probe whose next value belongs to a soft-deleted cohort walks the whole cohort (heap fetch + `deleted_at IS NULL` filter per row) before reaching the next live value. In production this is the `facet_skip` statement at ~1.4 s per filter-panel load (112 calls/day) after two accounts with large histories were soft-deleted; on a synthetic corpus one probe over a 160k-row dead cohort costs 150-160 ms and 160k buffers versus ~8-19 ms and tens of buffers when the index predicate excludes the cohort. The requirement's "bounded by the facet's distinct-value count, not by the size of `request_logs`" contract no longer holds once soft-deleted rows accumulate.

## What Changes

- Three partial indexes on `request_logs` whose predicate matches the probe's `deleted_at IS NULL`: `idx_logs_live_api_key (api_key_id)`, `idx_logs_live_model_effort (model, reasoning_effort)`, `idx_logs_live_status_error (status, error_code)`. Account ids need none: soft deletion detaches `account_id` (NULL), which an `account_id > previous` probe never walks.
- Alembic `20260909_130000_add_request_logs_live_facet_indexes`: PostgreSQL builds with `CREATE INDEX CONCURRENTLY IF NOT EXISTS` in an autocommit block and rebuilds a leftover invalid index from an interrupted concurrent build (same pattern as `20260717_000000_optimize_dashboard_hot_path_indexes`); SQLite uses plain partial indexes. Downgrade drops them (`CONCURRENTLY` on PostgreSQL).
- ORM `Index` declarations with `postgresql_where`/`sqlite_where`, registered in the manual drift index requirements (partial-index reflection is not consistent across dialects).
- No query change, no settings, no API change: the requirement gains the explicit clause that each successor probe MUST be served by an index whose predicate excludes soft-deleted rows.
- The indexes are unconditional (no opt-in or kill switch). They are pure schema: the facet statements are unchanged and the planner picks them by cost, so there is no behaviour to toggle; the settings budget has zero headroom (130/130), and an `Index`-level toggle would need a runtime DDL path the schema drift check cannot reason about. The migration downgrade is the off switch.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `query-caching`: MODIFIED requirement "Unfiltered request-log filter options avoid full DISTINCT passes" — probe cost MUST be independent of the soft-deleted cohort size; new scenario "Soft-deleted cohorts do not lengthen facet probes".

## Impact

- Schema: three partial btree indexes on `request_logs` (SQLite and PostgreSQL). `request_logs` already carries 14 indexes; the three new ones add per-insert cost on the proxied-request write path. Before merging, record `pg_stat_user_indexes.idx_scan` for `idx_logs_model_effort_time`, `idx_logs_status_error_time` and `idx_logs_api_key_time` so a later change can drop any that the live-row indexes leave unused instead of growing the set indefinitely.
- Operators: none. The concurrent build does not block writers; on a large table it runs for the duration of two table scans.
- Code: `app/db/alembic/versions/20260909_130000_add_request_logs_live_facet_indexes.py` (new), `app/db/models.py`, `app/db/migrate.py`, migration and options tests, `Makefile` (PostgreSQL test target list).
