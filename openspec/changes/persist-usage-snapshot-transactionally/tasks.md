## 1. Transactional Repository Operation

- [x] 1.1 Add a typed standard-window write model and a session-backed repository operation that stages one account snapshot, commits once, and rolls back failures.
- [x] 1.2 Add the background repository adapter that owns one session for the complete snapshot and detaches returned rows before closing it.

## 2. Usage Refresh Integration

- [x] 2.1 Change `UsageUpdater` to collect normalized standard windows and persist them through one snapshot operation without changing additional-usage behavior.
- [x] 2.2 Preserve `usage_written`, credits, reset metadata, and normalized monthly-window behavior through the batch path.

## 3. Regression Coverage

- [x] 3.1 Add updater coverage proving a multi-window payload is submitted as one shared-timestamp snapshot.
- [x] 3.2 Add repository coverage proving one commit, atomic rollback after a staged partial failure, and caller-session reuse.
- [x] 3.3 Add background-adapter coverage proving one owned session spans the complete snapshot and closes only after returned rows are detached.

## 4. Verification

- [x] 4.1 Run focused usage updater/repository/background tests, then the appropriate broader unit suite.
- [x] 4.2 Run Ruff format/check, `ty`, strict OpenSpec validation, and repository architecture/simplicity/diff gates.
