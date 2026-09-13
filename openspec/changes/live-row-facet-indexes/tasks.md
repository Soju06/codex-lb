# Tasks

## 1. Schema

- [x] 1.1 Alembic `20260909_130000_add_request_logs_live_facet_indexes`: `idx_logs_live_api_key`, `idx_logs_live_model_effort`, `idx_logs_live_status_error` with `WHERE deleted_at IS NULL`; PostgreSQL `CREATE INDEX CONCURRENTLY IF NOT EXISTS` in an autocommit block with invalid-leftover repair, SQLite plain partial indexes; downgrade drops them.
- [x] 1.2 `app/db/models.py` `Index` declarations with `postgresql_where`/`sqlite_where`; `app/db/migrate.py` manual drift index requirements register the three names.

## 2. Verification

- [x] 2.1 `tests/integration/test_migrations.py`: SQLite upgrade/downgrade/re-upgrade round trip (re-upgrade over an operator-precreated index exercises `IF NOT EXISTS`) asserting the indexes are partial and drift-clean; head index-name set includes the three names; PostgreSQL-only test rebuilds an invalid leftover and keeps a valid precreated index (added to `POSTGRES_PYTEST_TARGETS`).
- [x] 2.2 `tests/unit/test_db_migrate.py`: revision source builds concurrently, repairs invalid leftovers, carries the live-row predicate.
- [x] 2.3 `tests/test_request_logs_options_api.py`: PostgreSQL-only (collection-time skip) plan pin over a production-shaped corpus (3k live rows across models/keys/statuses, 3k soft-deleted rows sharing them, 20k soft-deleted cohort on dead-only values) — captures the real `facet_skip` statements issued by the options call and EXPLAIN ANALYZEs each: every `min()` probe uses its live-row index with no rows removed by filter and one index entry, every `(value, NULL)` existence probe uses a live-row partial index, and the response excludes the dead values; existing skip-scan and soft-delete tests unchanged.
- [x] 2.4 `uv run codex-lb-db upgrade head` + `check` on SQLite and PostgreSQL, `make lint` guards, `uv run ty check`, `openspec validate live-row-facet-indexes --strict`.
