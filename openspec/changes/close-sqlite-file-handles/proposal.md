# Close SQLite File Handles Before Filesystem Operations

## Summary

Explicitly close synchronous SQLite connections used by integrity checks,
pre-migration backups, and database recovery before backup rotation or recovery
replacement mutates the corresponding files.

## Why

`sqlite3.Connection` uses its context manager for transaction handling; leaving
the `with` block does not close the connection. On CPython 3.13, finalization
may be deferred long enough for codex-lb to delete or rename a database while
its native handle is still open. Windows rejects those operations with
`PermissionError: [WinError 32]`, breaking pre-migration backup rotation and
`app.db.recover --replace`.

## What Changes

- Add one shared context manager that preserves commit/rollback behavior and
  always closes the SQLite connection in a `finally` block.
- Use it for synchronous backup, integrity-check, dump, and restore
  connections.
- Add regressions through the backup-rotation and recovery CLI paths that keep
  connection objects alive and prove every native handle is explicitly closed.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `database-migrations`: Requires SQLite maintenance paths to release native
  handles before deleting or renaming database files.

## Impact

- Affected code: `app/db/backup.py`, `app/db/sqlite_utils.py`,
  `app/db/recover.py`
- Affected tests: SQLite backup rotation and recovery CLI coverage
- No schema, configuration, migration revision, or dashboard change is
  required.

## Non-Goals

- Changing SQLite journal, transaction, or locking modes.
- Changing backup retention counts, filenames, or recovery output naming.
- Managing SQLAlchemy or aiosqlite connection pools.
