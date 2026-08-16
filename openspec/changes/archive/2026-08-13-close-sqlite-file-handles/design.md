## Context

The standard-library SQLite connection context manager commits on success and
rolls back on failure, but it deliberately does not call `close()`. The backup,
integrity-check, and recovery modules currently rely on object finalization to
release native file handles. That lifetime is not deterministic, especially
for GC-tracked extension objects on CPython 3.13.

The bug is externally observable on Windows:

- backup rotation cannot unlink the oldest retained snapshot;
- `python -m app.db.recover --replace` cannot rename the source database or
  recovered output into place.

## Goals / Non-Goals

**Goals:**

- Retain the existing transaction commit/rollback semantics.
- Close every synchronous SQLite connection deterministically on success and
  failure.
- Prove closure at the backup-rotation and recovery CLI seams on every test
  platform.

**Non-Goals:**

- No retry loop or Windows-specific filesystem workaround.
- No change to backup/recovery contents or operator-facing arguments.
- No change to async database sessions.

## Decisions

- Add `sqlite_connection()` to `app.db.sqlite_utils`. It opens the connection,
  delegates transaction handling to `with connection`, and closes in a
  `finally` block.
- Reuse the helper in all four synchronous connection sites rather than
  duplicating nested `contextlib.closing` blocks. This keeps the transaction
  and lifetime contract identical across backup, integrity, dump, and restore.
- Exercise real product functions with a tracked `sqlite3.Connection`
  subclass. Tests retain strong references to opened connections, so the
  pre-fix code fails deterministically on POSIX as an explicit-close assertion
  and on Windows at the actual delete/rename operation. The tests close tracked
  connections during cleanup even when the assertion fails.

## Risks / Trade-offs

- A shared helper adds one small abstraction, but prevents the same
  transaction-versus-lifetime mistake from recurring across adjacent modules.
- Closing can surface an SQLite close error that deferred finalization
  previously hid. Surfacing that error at the operation boundary is safer than
  continuing to mutate files while their connection state is unknown.
- No garbage collection calls are added to production code; correctness does
  not depend on interpreter-specific finalization timing.
