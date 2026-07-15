# Design: serialize-startup-migrations

## Decisions

### Lock placement — `run_upgrade()`, not `env.py`

`run_upgrade` is the single product entrypoint for both the app startup path
(`init_db -> run_startup_migrations -> run_upgrade`, executed in a worker
thread via `to_thread`) and the container entrypoint
(`python -m app.db.migrate upgrade`). The racy steps outside `command.upgrade`
— `_bootstrap_legacy_history`'s stamp, `_ensure_alembic_version_table_capacity`'s
check-then-CREATE, `_remap_legacy_alembic_revisions`' DELETE+INSERT, and the
`state_before` inspection that computes `codex_lb_fresh_install` — each open
their own engine/connection, so an `env.py`-level lock would cover only
`command.upgrade` and miss them all. Critically, we must NOT also lock in
`env.py`: `run_upgrade` holds the session advisory lock on a dedicated
connection, and Alembic's `env.py` runs on a different connection, so a second
acquire there would self-deadlock.

- Rejected: locking in `env.py` only (misses helpers and bootstrap).
- Rejected: locking in both (self-deadlock).

### PostgreSQL: session-level advisory lock on a dedicated connection

`SELECT pg_try_advisory_lock(hashtext('codex_lb:migrations'))` — the same
`hashtext` idiom as `pg_advisory_xact_lock(hashtext(:key))` in
`app/core/rate_limiter/db_rate_limiter.py`, but session-scoped, because
`run_upgrade` spans many transactions across several short-lived engines (each
`_sync_transaction` creates its own engine) and the Alembic connection is not
ours to wrap. The lock connection runs in AUTOCOMMIT, is opened first, held
open (doing nothing) for the whole upgrade, and released via
`pg_advisory_unlock` + `engine.dispose()` in `finally`; PostgreSQL also
releases session locks automatically if the holder dies — no stale-lock GC.
Acquisition is a try-lock poll loop (2s interval) with periodic wait logging
and a bounded timeout.

- Rejected: `pg_advisory_xact_lock` (transaction-scoped; cannot span the
  multi-connection sequence).
- Rejected: a lease row like `scheduler_leader` (chicken-and-egg — the table it
  needs is created by the very migrations being serialized; lease expiry needs
  clock arbitration the advisory lock gets for free).
- Rejected: `LOCK TABLE alembic_version` (table absent on fresh databases —
  the fresh-install race is exactly the worst case).
- Rejected: reusing the cache-invalidation bus/poller (requires schema and an
  event loop; this is pre-schema, sync, pre-app).

### SQLite: `BEGIN IMMEDIATE` on a sentinel SQLite file, not `flock`

For file-backed SQLite, open `sqlite3.connect("<db_path>.migrate-lock",
timeout=0, isolation_level=None)` and execute `BEGIN IMMEDIATE`; holding that
write transaction is the mutex (SQLite's RESERVED lock is exclusive across
processes sharing the volume). Release = rollback + close; OS file locks
vanish on process death, so a SIGKILLed migrator never wedges peers. Same
poll/timeout/logging as PostgreSQL. The sentinel must be a *separate* file:
`BEGIN IMMEDIATE` on the main database would deadlock against Alembic's own
connection doing DDL and would block concurrent app reads. The sentinel is
never unlinked (avoids unlink/reopen races); it is a harmless zero-row SQLite
file. In-memory SQLite is a no-op — the database is process-private.

- Rejected: `fcntl.flock` on a lockfile (unavailable on Windows — runtime
  portability is a first-class capability — and unreliable on NFS; the
  sentinel inherits exactly the SQLite locking semantics the main DB already
  depends on).
- Rejected: `O_CREAT|O_EXCL` pidfile (needs stale-lock recovery after SIGKILL).
- Rejected: anyio-based `sqlite_writer_section` (process-local only).

### Post-acquire re-check (wait-and-skip)

After acquiring, `run_upgrade` re-runs `inspect_migration_state` on a fresh
connection. If the requested revision is `head`, the `alembic_version` table
exists, and the current revision equals head, it logs at INFO and returns a
`MigrationRunResult` without invoking `command.upgrade`. (When current == head
the legacy bootstrap is structurally a no-op — it only stamps when
`alembic_version` is absent — and no remap is pending, so the simplified skip
condition is equivalent to "no legacy bootstrap/remap pending".) This is what
makes the losing replica's startup succeed instead of crash-looping; the
entrypoint needs no change because `python -m app.db.migrate upgrade` exits 0
on the skip path. Explicit non-head CLI targets never skip. The pre-lock steps
in `init_db` (SQLite integrity check, pre-migration backup) stay outside the
lock — a duplicate backup from a losing replica is harmless and bounded by
`database_sqlite_pre_migrate_backup_max_files`.

### Ahead-of-build detection

`inspect_migration_state` computes `unknown_revisions` = current revisions not
in `_known_revisions(config)` and not keys of `OLD_TO_NEW_REVISION_MAP`, and
`is_ahead = bool(unknown_revisions)`. `needs_upgrade` stays `current != head`
(fail-closed exact-head gating is intentional per the existing spec); only the
*diagnostics* change: `init_db`'s migrate-disabled branch emits "revision(s)
… are not known to this build — the schema was likely migrated by a newer
version; deploy a matching or newer image, or run an Alembic downgrade"
instead of "schema is behind Alembic head", and `run_upgrade` raises the same
guidance as a `MigrationBootstrapError` before
`_remap_legacy_alembic_revisions` can emit its misleading "Unsupported
alembic_version revision ids detected" error. `wait-for-head` timeout behavior
is unchanged (fail-closed by design).

### No CAS, no new schema

Mutual exclusion plus post-acquire re-inspection replaces any compare-and-swap
on `alembic_version`; `CREATE TABLE IF NOT EXISTS` in the capacity helper is
pure hardening for out-of-band `alembic upgrade` invocations. No new Alembic
revision is added, so the single-head invariant is untouched.

### Performance

Zero hot-path impact — the lock exists only during startup migration/stamp.
Cost is one extra connection + one try-lock query per boot on the winner; the
loser waits up to the timeout (default 300s, matching `wait-for-head`).

## Deviations from the approved design

- The wait-and-skip condition is implemented as `revision == "head" and
  has_alembic_version_table and current == head` rather than separately
  re-checking "no legacy bootstrap/remap pending": when `alembic_version`
  exists the bootstrap never stamps, and when current equals head exactly no
  remap is pending, so the extra checks are structurally redundant (see
  post-acquire re-check above).
- `run_upgrade`/`stamp_revision` accept an optional `lock_timeout_seconds`
  override (defaulting to the setting) so tests and future callers can bound
  waits without mutating global settings.
- The pre-existing unit test
  `test_run_upgrade_fails_for_unsupported_alembic_version_id` asserted the old
  misleading "Unsupported alembic_version revision ids" message on the
  `run_upgrade` path; it now asserts the ahead-specific message. The generic
  remap error remains for direct `_remap_legacy_alembic_revisions` callers.
- Likewise, the pre-existing unit test
  `test_ensure_alembic_version_table_capacity_creates_table_when_missing` now
  asserts the `CREATE TABLE IF NOT EXISTS` SQL the design mandates for the
  capacity helper.

## Risks / trade-offs

- PgBouncer/transaction-pooled endpoints break session advisory locks;
  migrations open their own direct sync engine, but operators pointing
  `CODEX_LB_DATABASE_URL` at a transaction-pooling proxy lose the guarantee
  (documented in context.md).
- Advisory-lock key collision via `hashtext` with rate-limiter keys would only
  cause spurious brief serialization (namespaced key; rate-limiter locks are
  xact-scoped and short).
- SQLite sentinel on NFS inherits SQLite's known NFS locking unreliability —
  no worse than the main DB itself (documented in context.md).
- A hung migrator holds peers until the timeout, then they fail fast with an
  actionable error — same crash-loop as today but explicit and tunable.
