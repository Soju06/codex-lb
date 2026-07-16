# Tasks: serialize-startup-migrations

## 1. OpenSpec

- [x] 1.1 Create change artifacts (proposal, design, tasks, database-migrations spec deltas) and pass `openspec validate serialize-startup-migrations`.

## 2. Cross-process migration lock

- [x] 2.1 Add `database_migration_lock_timeout_seconds: float = 300.0` (env `CODEX_LB_DATABASE_MIGRATION_LOCK_TIMEOUT_SECONDS`) to `app/core/config/settings.py`.
- [x] 2.2 Implement `app/db/migration_lock.py`: `migration_lock(sync_database_url, *, timeout_seconds)` — PostgreSQL `pg_try_advisory_lock(hashtext('codex_lb:migrations'))` poll loop on a dedicated AUTOCOMMIT connection with `pg_advisory_unlock` + dispose in `finally`; file-backed SQLite `BEGIN IMMEDIATE` poll loop on a `<db_path>.migrate-lock` sentinel connection; in-memory/other dialects no-op; INFO wait logging every ~10s; `TimeoutError` naming the lock and the setting.
- [x] 2.3 Wrap `run_upgrade()`'s whole body (state inspection, legacy bootstrap, capacity ensure, remap, `command.upgrade`) in `migration_lock`; add the post-acquire re-inspection that skips `command.upgrade` and returns early when the target is head and the schema is already at head, logging the already-migrated skip; wrap `stamp_revision()` in the same lock.
- [x] 2.4 Change `_ensure_alembic_version_table_capacity_for_connection` to `CREATE TABLE IF NOT EXISTS`.

## 3. Ahead-of-build diagnostics

- [x] 3.1 Extend `MigrationState` with `unknown_revisions`/`is_ahead` computed in `inspect_migration_state` (unknown = not in `_known_revisions` and not in `OLD_TO_NEW_REVISION_MAP`).
- [x] 3.2 Raise the ahead-specific `MigrationBootstrapError` in `run_upgrade` before the legacy remap can emit the generic unsupported-revision error.
- [x] 3.3 Branch `init_db`'s migrate-disabled error message on `is_ahead` with "not known to this build" guidance instead of "behind Alembic head".

## 4. Tests

- [x] 4.1 Concurrent-upgrade race: two barrier-aligned `run_upgrade` calls on a fresh database (SQLite tmp file; PostgreSQL when `CODEX_LB_TEST_DATABASE_URL` is set) — both succeed, single head row, no duplicate-object errors.
- [x] 4.2 Wait-and-skip: hold `migration_lock` on an at-head database, assert a second `run_upgrade` blocks, then returns without applying and logs the skip.
- [x] 4.3 Timeout: held lock + `lock_timeout_seconds=0.5` raises `TimeoutError` naming `database_migration_lock_timeout_seconds` (SQLite and PostgreSQL variants).
- [x] 4.4 Entrypoint path: two concurrent `python -m app.db.migrate upgrade` subprocesses on one tmp SQLite file both exit 0 with head applied once.
- [x] 4.5 Ahead-of-build regression: fabricated future revision in `alembic_version` — `inspect_migration_state.is_ahead`, `init_db()` with `database_migrate_on_startup=false` raises "not known to this build" (not "behind Alembic head"), and `run_upgrade` raises the ahead-specific error.

## 5. Docs & verification

- [x] 5.1 Update `openspec/specs/database-migrations/context.md`: per-backend lock mechanism, sentinel file lifecycle, PgBouncer/NFS caveats, migrate-on-startup vs migration-Job + `wait-for-head` deployment contract, timeout tuning.
- [x] 5.2 Run focused pytest (migration suites), ruff check/format, and strict OpenSpec validation.
