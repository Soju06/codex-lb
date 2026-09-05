## 1. Specification

- [x] 1.1 Specify read-only planning and explicit stopped-service execution.
- [x] 1.2 Specify verified replacement, backup preservation, and rollback.

## 2. Implementation

- [x] 2.1 Add typed compaction plan/outcome and SQLite-only validation.
- [x] 2.2 Implement same-directory compact/verify/replace with lock and rollback.
- [x] 2.3 Wire `compact` into `codex-lb-db` and document the runbook.

## 3. Verification

- [x] 3.1 Cover dry-run, reclaimed space, incremental autovacuum, busy/changed
      source, corrupt output, insufficient disk, and replacement rollback.
- [x] 3.2 Run focused CLI/DB tests, Ruff, and type checks.
- [x] 3.3 Run strict OpenSpec validation and `git diff --check`.
