# Change: Add safe SQLite compaction

## Why

SQLite retention returns deleted pages to the freelist but does not shrink the
database while `auto_vacuum` is disabled. Large files keep startup integrity
checks and cold reads expensive even after data is pruned. Operators need an
owned, rollback-capable maintenance path instead of ad-hoc `VACUUM` commands.

## What Changes

- Add `codex-lb-db compact --dry-run|--execute` for file-backed SQLite.
- Require explicit stopped-service acknowledgement before execution.
- Build a compacted database beside the source, enable incremental autovacuum,
  verify integrity and schema identity, then replace atomically while retaining
  the original as a timestamped backup.
- Reject unsafe free-space, WAL, live-write, non-SQLite, and corrupt-output
  conditions before source replacement.

## Non-Goals

- Running compaction automatically or against the production database during
  this implementation.
- Pruning rows or choosing retention policy.
- Supporting PostgreSQL compaction.

## Impact

- Affected specs: `database-migrations`, `database-backends`.
- Affected code: a new SQLite maintenance module, migration CLI wiring, docs,
  and focused temporary-database tests.
- No schema migration or application startup behavior change.
