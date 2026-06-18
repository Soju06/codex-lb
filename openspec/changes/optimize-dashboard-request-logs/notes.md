# Notes: dashboard API performance evidence

## Production evidence from 10.0.0.113

Read-only service timings after manual `ANALYZE` on 2026-06-17:

```text
request_logs.list_recent_25: 18.620s
request_logs.filter_options: 2.051s
dashboard.overview_7d: 1.256s
dashboard.projections: 4.186s
api_key.trends: 0.012s
api_key.usage_7d: 0.023s
```

`ANALYZE` corrected stale PostgreSQL statistics:

```text
request_logs n_live_tup: ~13K -> 1,380,746
usage_history n_live_tup: ~13K -> 936,752
additional_usage_history n_live_tup: ~13K -> 633,128
api_key_usage_reservations n_live_tup: ~14K -> 1,716,925
```

But the request-log list endpoint stayed slow because the bottleneck is query shape, not only stats.

## Root cause

The old request-log page query added `count(*) OVER()` to the same row query that also had `ORDER BY requested_at DESC, id DESC LIMIT 25`. On production-shaped data PostgreSQL materialized the full filtered set before returning the first page.

Measured SQL comparison:

```text
page-only latest 26 rows: ~0.3ms
separate exact count: ~300-400ms
window-count paginated query: ~8.5s SQL / double-digit seconds service-level
```

## Immediate fix

Keep the public response contract intact, but split the query:

```text
SELECT request_logs ... ORDER BY requested_at DESC, id DESC LIMIT/OFFSET
SELECT count(id) ... same filters
```

## Follow-up optimization direction

The same source-structured principle should be applied to the other dashboard surfaces:

- request-log filter options: cache or incrementally summarize distinct facets
- dashboard overview: maintain request-log bucket/activity summaries by source time bucket
- projections: maintain usage-history window snapshots instead of scanning raw 7d primary/secondary histories every poll

These are intentionally out of scope for this hotfix because they introduce new summary/storage contracts.
