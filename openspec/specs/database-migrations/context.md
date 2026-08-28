## Overview

`database-migrations` capability defines how codex-lb evolves schema safely across fresh installs, partially migrated legacy DBs, and ongoing branch development.

## Scope and Non-Goals

- Scope:
  - Runtime startup migration behavior
  - Legacy history bootstrap/remap behavior
  - Revision naming and head governance
  - CI migration guardrails
- Non-goals:
  - Designing rollback SQL for every migration
  - Supporting alternate revision ID formats
  - Maintaining compatibility with unknown third-party Alembic revisions

## Key Decisions

- Alembic is the runtime SSOT for migrations.
- Revision IDs use `YYYYMMDD_HHMMSS_slug` for readability and merge-conflict reduction.
- Legacy IDs are auto-remapped at startup to avoid manual DB patching during cutover.
- CI checks both policy (head/naming) and drift in one command path.
- Schema upgrades and stamps are serialized across processes by a cross-process migration lock (see Operational Notes); losing replicas wait, re-inspect, and skip when the schema is already at head.
- `alembic_version` revisions unknown to the running build are reported as schema-ahead ("not known to this build") rather than "behind Alembic head".

## Constraints

- Legacy `schema_migrations` rows are historical input only.
- One migration executor at a time is enforced by the migration lock (`run_upgrade`/`stamp_revision` paths); direct `alembic` CLI invocations bypass it and remain the operator's responsibility.
- Unsupported `alembic_version` IDs fail fast to avoid silent divergence, with direction-correct diagnostics (schema-ahead vs schema-behind).
- Startup also verifies post-upgrade schema drift before the app begins normal work.

## Failure Modes and Mitigations

- Multiple Alembic heads caused by parallel branches:
  - Mitigation: CI fails; add merge revision before merge/release.
- Legacy revision IDs still present in operator DB:
  - Mitigation: startup auto-remap of known IDs.
- Unknown revision IDs in `alembic_version`:
  - Mitigation: explicit startup failure + manual operator intervention.
- Drift between metadata and migrated schema:
  - Mitigation: CI unified migration check blocks merge.
  - Runtime mitigation: startup drift check logs explicit diffs and fails startup when `database_migrations_fail_fast=true`.

## Operational Notes

- Startup path:
  - (SQLite integrity check, optional SQLite backup) -> acquire migration lock -> inspect state (skip if already at head) -> bootstrap legacy `schema_migrations` -> remap legacy Alembic IDs -> `upgrade head` -> release lock -> schema drift check
- Migration lock (serializes `run_upgrade` and `stamp_revision` across replicas):
  - PostgreSQL: session-level `pg_try_advisory_lock(hashtext('codex_lb:migrations'))` polled every 2s on a dedicated AUTOCOMMIT connection held for the whole upgrade; released explicitly and automatically on holder death. Caveat: transaction-pooling proxies (PgBouncer in transaction mode) break session advisory locks — point `CODEX_LB_DATABASE_URL` at the database directly, or at a session-pooling endpoint, if replicas migrate on startup.
  - File-backed SQLite: an exclusive `BEGIN IMMEDIATE` write transaction on a sentinel SQLite file `<db_path>.migrate-lock` adjacent to the database. The sentinel is created on first use and intentionally never deleted (a harmless zero-row SQLite file); OS-level SQLite locks vanish on process death. Caveat: on NFS this inherits SQLite's known NFS locking unreliability — no worse than the main database itself.
  - In-memory SQLite: no-op (the database is process-private).
  - Direct `alembic upgrade` (bypassing `python -m app.db.migrate`) does not take the lock; `alembic_version` capacity bootstrap uses `CREATE TABLE IF NOT EXISTS` as defense-in-depth, but out-of-band invocations should still be serialized by the operator.
- Lock timeout tuning:
  - `CODEX_LB_DATABASE_MIGRATION_LOCK_TIMEOUT_SECONDS` (default 300, matching `wait-for-head`) bounds how long a replica waits for a peer's migration. On timeout the error names the lock and this setting; the startup path honors `database_migrations_fail_fast`, the CLI always exits non-zero. Raise it for deployments whose migrations legitimately run long.
- Multi-replica deployment contract:
  - Either keep `database_migrate_on_startup=true` on every replica (the lock makes concurrent boots safe: one replica applies, the rest wait and skip), or disable it and run a dedicated migration Job while app replicas use `python -m app.db.migrate wait-for-head` before starting.
- CLI checks:
  - `codex-lb-db check` validates head count, revision naming/filename policy, and schema drift.
- Emergency toggle:
  - `CODEX_LB_DATABASE_ALEMBIC_AUTO_REMAP_ENABLED=false` disables auto-remap.

## Example

Branch A and B each create migration revisions in parallel. After merge, CI detects multiple heads and fails. The resolver adds a merge revision, reruns CI, and proceeds. During deployment, a DB still storing old `013_add_dashboard_settings_routing_strategy` in `alembic_version` is auto-remapped to `20260225_000000_add_dashboard_settings_routing_strategy` before upgrade.
