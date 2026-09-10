## Why

SQLite recovery can leave WAL, shared-memory, rollback-journal, or
master-journal files beside the source or output. Installing a recovered file
with those sidecars present can attach stale state to the replacement.

## What Changes

- Remove fixed SQLite sidecars around output dump import and source replacement.
- Match master journals literally so glob metacharacters in a database name
  cannot remove another database's journal.
- Reject identical source/output paths and source/output overlap with either
  path's fixed sidecars or master-journal namespace before any cleanup or write.
- Remove pre-existing output sidecars before opening an exclusive SQLite
  recovery transaction. Acquire that lock before exporting the source, generate
  the dump from the lock-holding connection, and keep the transaction through
  output import. Close every recovery connection before final sidecar cleanup
  or either database rename so Windows can perform the file mutations; repeat
  source cleanup after the source move to catch sidecars recreated around that
  move. Fail closed if the lock or any sidecar cleanup cannot complete before
  the source move. If repeat source cleanup fails after the source move, restore
  the original source path before reporting the error and leave the recovered
  output as an uninstalled recovery artifact. If the second replacement rename
  fails, restore the original source path before reporting the error. Closing
  the transaction leaves a bounded post-probe window before cleanup and
  renames, so the operator must keep external writers quiescent throughout it.

The recovery output and backup naming remain unchanged.

## Impact

This is isolated to file-backed SQLite recovery. It adds no migration, setting,
or runtime startup behavior.

## Dependencies

The implementation is self-contained and does not require the SQLite startup
run-state changes from the other local candidate. The hosted Contributors
attribution check is intentionally carried by #1902, which is the sole PR
allowed to change contributor metadata; merge #1902 first (or otherwise satisfy
that check) before merging this PR.
