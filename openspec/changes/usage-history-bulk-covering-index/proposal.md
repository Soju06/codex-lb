## Why

The dashboard projections bulk history fetch
(`UsageRepository.bulk_history_since`) reads six narrow columns
(`id, account_id, used_percent, recorded_at, reset_at, window_minutes`)
from `usage_history`, but every index that matches its predicate shape —
`idx_usage_window_account_time` for the coalesced-primary path and
`idx_usage_window_raw_account_latest` for the raw-window path — is a pure
key index. PostgreSQL therefore visits the heap for every matched row.
On the reference deployment (15 accounts, high-frequency usage snapshots)
`pg_stat_statements` shows this SELECT averaging 2.0 s, and the heap
fetches compete for I/O with the write-side fsync path that dominates the
slow-query log.

## What Changes

- Add two covering indexes on `usage_history` whose key columns mirror the
  bulk fetch's two predicate shapes and whose `INCLUDE` payload carries the
  remaining selected columns, so the read can be served entirely from the
  index (index-only scan) on PostgreSQL:
  - `idx_usage_window_account_time_covering
    (coalesce("window",'primary'), account_id, recorded_at)
    INCLUDE (used_percent, reset_at, window_minutes, id, "window")` —
    serves the `window="primary"` (coalesced) path. The raw `"window"`
    column rides in the payload because PostgreSQL only considers an
    index-only scan when every column referenced by the query is
    returnable from the index, and an expression key column cannot return
    its underlying raw column — without it the `coalesce(...)` qual
    disqualifies the index-only path (verified on PostgreSQL 18).
  - `idx_usage_window_raw_account_time_covering
    ("window", account_id, recorded_at)
    INCLUDE (used_percent, reset_at, window_minutes, id)` — serves the
    explicit raw-window path (`window="secondary"` is a live production
    path via the dashboard projections builder), whose only current match,
    `idx_usage_window_raw_account_latest`, is a DESC latest-probe index
    with no payload.
- PostgreSQL builds them with `CREATE INDEX CONCURRENTLY` inside an
  autocommit block, preceded by the invalid-leftover repair probe
  (mirroring `20260717_000000_optimize_dashboard_hot_path_indexes`).
- Non-PostgreSQL backends create the same-named indexes with key columns
  only (no `INCLUDE`), exactly as `20260717_000000` did for
  `idx_logs_dash_usage_covering`: SQLite serves this read from the
  snapshot cache (`_bulk_history_since_sqlite`) and never runs this SQL
  shape, so the SQLite twins exist for schema/drift parity with the
  `models.py` declarations, not for performance.
- Declare both indexes in `app/db/models.py` (with
  `postgresql_include=[...]`) and register them in
  `_MANUAL_DRIFT_INDEX_REQUIREMENTS` so a missing index fails schema-drift
  checks fast on both backends.
- Tune `usage_history` insert-driven autovacuum on PostgreSQL
  (`autovacuum_vacuum_insert_scale_factor = 0.02`,
  `autovacuum_vacuum_insert_threshold = 50000`,
  `autovacuum_analyze_scale_factor = 0.02`, mirroring `20260717_000000`
  for `request_logs`/`additional_usage_history`): the table is
  append-heavy, and with the default insert trigger the visibility map
  goes stale enough that the covering "index-only" scans degrade into
  per-row heap fetches (observed on the reference deployment; resolved
  there by the same settings applied manually — the migration codifies
  them and is idempotent over that hotfix).
- No query text changes; results are byte-identical.

### Deliberately kept (write-amplification note)

`idx_usage_window_account_time` becomes a full key-prefix duplicate of the
new primary covering index, and on SQLite the primary covering twin is an
exact duplicate of it. It is retained conservatively in this change —
dropping it (and re-evaluating `usage_history` write amplification, now
five → seven indexes) is deferred to a follow-up once the covering path
has soaked in production.

### Known limitation

The per-account-cutoff fetch shape (`OR` of
`(account_id = X AND recorded_at >= tX)` arms, from
`bound-projection-history-fetch`) may still be planned as a
`BitmapOr` + bitmap heap scan under default planner settings, which cannot
be index-only. The covering index guarantees an index-only plan for the
shared-floor shape (`account_id IN (...) AND recorded_at >= since`) and
makes an index-only filter plan available for the cutoff shape (verified
with bitmap scans disabled); restructuring the cutoff shape (e.g.
per-account `UNION ALL` arms) to force per-arm index-only descents is out
of scope here. Consequently the measured 2.0 s regression is only proven
resolved for the shared-floor and raw-window shapes; since the cutoff
shape is the dashboard's default since #1613, the follow-up below is
load-bearing for the headline perf claim and must be verified on the
reference deployment (via `pg_stat_statements`) before the regression is
declared closed.

### Follow-ups (tracked, load-bearing)

- Measure the cutoff-shape plan and latency on the reference deployment
  after this change soaks; if it still plans `BitmapOr` + heap, rewrite it
  as per-account `UNION ALL` arms so each arm descends the covering index
  index-only.
- Re-evaluate `usage_history` write amplification (five → seven indexes on
  a high-frequency insert path): `idx_usage_window_account_time` is now a
  full key-prefix duplicate of the primary covering index and should be
  dropped once the covering path has soaked in production.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `query-caching`: the projections bulk usage-history fetch on PostgreSQL
  MUST be servable entirely from an index (index-only scan) without heap
  fetches for its selected columns.

## Impact

Two Alembic revisions (two covering indexes via `CREATE INDEX
CONCURRENTLY` on PostgreSQL with invalid-leftover repair; `usage_history`
insert-driven autovacuum tuning), `app/db/models.py` index declarations,
`app/db/migrate.py` manual drift index list, `Makefile`
`POSTGRES_PYTEST_TARGETS` registration for the PostgreSQL-only tests. No
API or query change; read results are unchanged.
