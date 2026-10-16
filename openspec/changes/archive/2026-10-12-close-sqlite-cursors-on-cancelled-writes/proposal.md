# Proposal

## Why

Cancellation during a SQLite write with RETURNING can retain an unfinished cursor through an exception traceback. Closing its connection without finalizing the cursor leaves the database writer lock held, blocking bridge persistence and scheduler leader election until process restart or garbage collection.

## What Changes

- Explicitly close SQLAlchemy-created SQLite cursors before closing file-backed connections, on the existing aiosqlite worker thread.
- Cover cancelled writes and terminal transcript timeouts with real SQLite regression tests that retain cancellation tracebacks.
- Preserve cancellation, transaction semantics, in-memory SQLite, and PostgreSQL behavior.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `database-backends`: file-backed SQLite teardown releases unfinished statement locks independently of traceback collection.

## Impact

Shared main/background SQLite connection factory and database/bridge tests. No migrations, new settings, or dependency changes. A replacement wheel includes the existing dashboard assets.
