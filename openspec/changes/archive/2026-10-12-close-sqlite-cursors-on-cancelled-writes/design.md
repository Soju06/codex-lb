# Design

## Context

See proposal.md for the incident. The deterministic reproducer fails on SQLAlchemy 2.0.52 and 2.1.4 with Python 3.14: cancellation during INSERT RETURNING can retain a cursor in a traceback after connection invalidation. SQLite connection close alone does not finalize the statement.

## Goals / Non-Goals

Finalize SQLAlchemy-created cursors before closing the raw file-backed connection. Preserve existing async session ownership, cancellation propagation, deadlines, rollback, and settlement fences. No schema changes or retry-policy changes.

## Decisions

Use a small sqlite3.Connection subclass passed through the shared file-backed engine connect_args factory. Track cursors weakly so tracking does not extend cursor lifetime. Close live cursors before delegating to the base connection close; this runs on aiosqlite's worker, preserving SQLite thread affinity. SQLAlchemy creates statements through cursor(), so direct connection shortcut methods are outside this adapter's scope.

Increasing busy_timeout cannot release a stranded statement. Pinning SQLAlchemy alone does not fix either reproduced version. Explicit garbage collection depends on unrelated object lifetime and is not a reliable cleanup contract.

## Risks / Trade-offs

- Retained cursor already closed: sqlite3 cursor close is idempotent; test repeated close.
- Unexpected cursor close failure: attempt all remaining cursors and base connection close before propagating the first cleanup error; test that later unfinished statements release the writer lock.
- Tests accidentally collect the offending traceback: retain cancellation exceptions explicitly until after a second independent writer succeeds; prove regression fails without the factory.
- A worker still executing a statement: this patch finalizes it when queued close executes; existing interruption and teardown bounds remain responsible for actively stuck workers.

## Migration Plan

No migration. Build a separate wheel with dashboard assets and launch with frozen runtime dependencies after stopping the old foreground server. Keep the previous wheel for rollback; never delete SQLite journal files. Observe live traffic after restart; a health endpoint alone is not evidence that the write lock is fixed.
