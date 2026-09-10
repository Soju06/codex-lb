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

## September overflow and transport merge

The subscription-overflow and transport-sentinel revisions both descended from
the quota-warmup revision. A forward merge joins them without changing their
operations. An upgrade from one parent applies the other parent normally;
downgrading only the merge restores both parent stamps while preserving both
schemas and their data. This is not a rollback to a build that knows only one
branch. See the [repair context](../../changes/archive/2026-09-09-merge-overflow-transport-migration-heads/context.md).

Later revisions can follow this merge. Its regression tests target the historical
merge explicitly for no-op upgrade/downgrade checks. Separate populated upgrades
to the current head verify later descendants against current ORM metadata.

## Example

Branch A and B each create migration revisions in parallel. After merge, CI detects multiple heads and fails. The resolver adds a merge revision, reruns CI, and proceeds. During deployment, a DB still storing old `013_add_dashboard_settings_routing_strategy` in `alembic_version` is auto-remapped to `20260225_000000_add_dashboard_settings_routing_strategy` before upgrade.

## Receipt and spool-retention merge

The receipt/request-log merge and spool-retention revision created separate heads when PR1954 was composed with current main. A new schema-neutral merge joins both existing heads without changing their histories. Upgrading from either parent applies the missing parent and preserves existing receipt and retention values. Naming either immediate parent for a merge-only downgrade restores both parent stamps and leaves both schemas intact; relative `-1` is ambiguous at this merge.

This graph correction does not choose receipt reclamation, lifetime, success settlement or mixed-version activation. See the [verified repair context](../../changes/archive/2026-09-10-join-retry-claim-spool-heads/context.md).

## Guest-session and receipt history composition

Candidate feaa8db312d7e540b306056ad9c981491dd26701 and main d6a7ca662860e2b427e462f434319273bcd240bd compose to heads 20260910_160000_merge_retry_claim_spool_heads and 20260908_000000_add_guest_session_generation. The latter's published parent is 20260910_010000_dashboard_spool_retention. Keep those edges intact.

A database on the receipt/spool join gains guest_session_generation with its incoming zero default and retains its active receipt and spool value. A guest-parent database retains a nonzero generation and spool value while adding nullable receipt fields. Joining or downgrading only the join changes version stamps without changing either schema or its rows. Removing receipt schema remains governed by its existing live-receipt guard.

The original graph-repair evidence stays immutable. Receipt reclaim/reset/settlement and mixed-version decisions stay open. No broader guest authorization changes or other PR graph changes belong here.

## Account, audit and retry-claim history composition

Actual target8e5760726a34332d869aac682a3932170621966b extends guest-session generation through roles000000, users010000, compat-credential020000 and audit030000. The other published head is20260910_170000_merge_guest_retry_claim_heads. The new join targets the complete incoming chain.

A populated retry-parent database retains its live receipt, nondefault spool value and guest generation while receiving the incoming role/user schema and documented credential backfills. An audit-parent database retains custom roles, scoped grants, users, session generations and audit actor snapshots while receiving nullable receipt fields. Existing audit rows retain the incoming migration's SQLite timestamp normalization; new actor/target fields default to null and severity to info.

Downgrading only the join restores both parent stamps without dropping either branch or changing rows. Previous repair/review/hosted records stay immutable. The earlier7325 review applies to9cf only; fe25cf5c is the reviewed8e composition. The200000 revision was introduced locally and adjusted before any publication. Every migration already published on either input remains unchanged.

No sibling PR dependency or auth/receipt policy is introduced. Original receipt and live-activation gates remain open.

## Invite and retry-claim history composition

Main561311ded1d3191cf1ef271d4cd8ea97f8fd17a4 adds040000 after audit030000 while PR1954 already publishes200000 joining170000 with030000. Preserve both histories and append a new join.

An account/retry-parent database gains an empty invite table while retaining its role grants, user/session generations, guest generation, audit rows and receipt/spool values. An invite-parent database retains its token hash, expiry, flags, issuer snapshot and user linkage while gaining nullable receipt columns. After populating both schemas, join-only downgrade changes stamps and nothing else.

Old source/review/hosted receipts remain immutable. Current-target compatibility requires this new join but introduces no invitation or authorization policy change.
