## 1. Implementation

- [x] 1.1 Alembic revision `20260806_020000_add_usage_history_bulk_covering_indexes`:
      `idx_usage_window_account_time_covering` (payload also carries raw
      `"window"` so the coalesce qual stays index-only-eligible) and
      `idx_usage_window_raw_account_time_covering` (PostgreSQL
      `CREATE INDEX CONCURRENTLY ... INCLUDE (...)` inside an autocommit
      block with invalid-leftover repair; key-column-only twins elsewhere),
      `down_revision = 20260730_000000_add_api_key_fair_share_threshold`
      (re-parented onto current main after #1536 merged with the same
      original parent).
- [x] 1.2 Declare both indexes in `app/db/models.py` with
      `postgresql_include=[...]` (reuse `_PRIMARY_WINDOW_INDEX_EXPR` for the
      coalesced expression) and register both in
      `_MANUAL_DRIFT_INDEX_REQUIREMENTS["usage_history"]`.

## 2. Validation

- [x] 2.1 PostgreSQL integration test: EXPLAIN of the bulk history fetch
      shape reports an Index Only Scan over the covering index (primary
      coalesced path and raw secondary path). The fixture runs
      `VACUUM ANALYZE` first: with an empty visibility map the planner
      prefers a plain Index Scan on the cheaper non-covering key twin and
      the tests are non-deterministic on a clean database.
- [x] 2.2 Migration round-trip test (upgrade creates both indexes,
      downgrade removes them, idempotent re-upgrade), mirroring
      `test_migrations.py` prior art.
- [x] 2.3 PostgreSQL row-equality test: the production
      `bulk_history_since` read returns identical rows with the index-only
      path available and with `enable_indexonlyscan = off`.
- [x] 2.4 PostgreSQL invalid-leftover repair test: plant an invalid
      same-named decoy index below the covering revision and assert the
      re-applied migration rebuilds it as a valid covering index.
- [x] 2.5 Gates: `uv run ruff check .`, `uv run ruff format .`,
      `uv run ty check app`, targeted pytest on SQLite and PostgreSQL.

## 3. Hardening (post-review follow-up)

- [x] 3.1 Alembic revision `20260808_000000_tune_usage_history_autovacuum`:
      insert-driven autovacuum tuning for `usage_history`
      (`autovacuum_vacuum_insert_scale_factor = 0.02`,
      `autovacuum_vacuum_insert_threshold = 50000`,
      `autovacuum_analyze_scale_factor = 0.02`), mirroring
      `20260717_000000` for the other insert-heavy tables; downgrade
      RESETs, non-PostgreSQL no-op, idempotent over the reference
      deployment's manual hotfix.
- [x] 3.2 PostgreSQL round-trip test for the reloptions (set on upgrade,
      reset on downgrade, harmless re-apply over the manual hotfix).
- [x] 3.3 Register the PostgreSQL-only covering-path tests (plan-shape,
      row-equality, migration round-trip/repair, autovacuum reloptions)
      in `POSTGRES_PYTEST_TARGETS` so CI runs them against PostgreSQL.
- [x] 3.4 Grow the plan-shape fixture to hundreds of rows per account so
      the covering index wins the cost comparison deterministically
      (20-row fixtures tie with the non-covering key twin on
      PostgreSQL 16 and flake); verify 3 consecutive PostgreSQL runs.
