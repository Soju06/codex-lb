# Serialize Startup Migrations

## Why

Every replica runs Alembic `upgrade head` at boot (and `docker-entrypoint.sh` runs `python -m app.db.migrate upgrade` unconditionally) with no cross-process mutual exclusion. Two replicas starting concurrently both read the same current revision and race DDL: the loser dies on duplicate-object errors — including the non-idempotent `CREATE TABLE alembic_version` in the capacity helper and the DELETE+INSERT legacy remap — and crash-loops under the entrypoint's `set -eu`. Separately, migration state inspection is direction-blind (`needs_upgrade = current != head`), so an old binary restarting against a schema migrated by a newer build reports "schema is behind Alembic head" or a misleading "Unsupported alembic_version revision ids" remap error, sending operators the wrong way during rollouts.

## What Changes

- New `app/db/migration_lock.py`: a sync `migration_lock(sync_database_url, *, timeout_seconds)` context manager providing a cross-process mutex — PostgreSQL: session-level `pg_try_advisory_lock(hashtext('codex_lb:migrations'))` polled on a dedicated connection held for the whole upgrade; file-backed SQLite: an exclusive `BEGIN IMMEDIATE` write transaction held on a sentinel SQLite file `<db_path>.migrate-lock` adjacent to the database; in-memory SQLite and unknown dialects: no-op.
- `run_upgrade()` acquires the lock around its entire sequence (state inspection, legacy bootstrap, capacity ensure, remap, `command.upgrade`); after acquiring it re-inspects migration state and, when the target is `head` and the schema is already at head, logs that migrations were already applied and returns without applying — the losing replica's startup succeeds. Covers both the app startup path and the container-entrypoint CLI path, which share `run_upgrade`.
- `stamp_revision()` takes the same lock; `_ensure_alembic_version_table_capacity_for_connection` switches to `CREATE TABLE IF NOT EXISTS` as defense-in-depth for direct `alembic` CLI invocations that bypass `run_upgrade`.
- New setting `database_migration_lock_timeout_seconds` (default `300.0`, env `CODEX_LB_DATABASE_MIGRATION_LOCK_TIMEOUT_SECONDS`); on timeout, a `TimeoutError` names the migration lock and the setting (startup honors `database_migrations_fail_fast` as today; the CLI always fails).
- `MigrationState` gains `unknown_revisions: tuple[str, ...]` and `is_ahead: bool` (any current revision not in the local Alembic script directory and not legacy-remappable). `init_db()`'s migrate-disabled branch and `run_upgrade()` (before the generic unsupported-revision remap error) raise a direction-correct "schema revision(s) are not known to this build; deploy a matching or newer image, or downgrade the schema" message instead of claiming the schema is behind head.
- Spec deltas in `openspec/specs/database-migrations/` (two ADDED requirements) plus `context.md` ops notes documenting the per-backend lock mechanism, sentinel-file lifecycle, PgBouncer/NFS caveats, and the migrate-on-startup vs migration-Job + `wait-for-head` deployment contract.
- No new DB table and no new Alembic revision (the mechanism is a lock, not schema).

## Non-goals

- Serializing direct `alembic upgrade` CLI invocations that bypass `app.db.migrate` (out of the product path; `env.py` deliberately stays lock-free to avoid self-deadlock with the held session lock).
- Changing the exact-head fail-closed gating semantics of `needs_upgrade` or `wait-for-head`.
