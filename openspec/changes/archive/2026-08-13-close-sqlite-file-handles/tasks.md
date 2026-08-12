## 1. Regression Coverage

- [x] 1.1 Add a backup-rotation regression that retains every opened
  `sqlite3.Connection` and proves handles are closed before retention deletes
  an old snapshot.
- [x] 1.2 Add a recovery CLI regression that proves integrity-check, dump, and
  restore handles are closed before `--replace` renames database files.
- [x] 1.3 Run both regressions against the pre-fix implementation and record
  the expected failure.

## 2. Deterministic SQLite Lifetime

- [x] 2.1 Add a shared transactional SQLite context manager that explicitly
  closes its connection in `finally`.
- [x] 2.2 Adopt the helper in backup, integrity-check, dump, and restore paths
  without changing their transaction or error contracts.

## 3. Verification

- [x] 3.1 Run the focused database migration and recovery tests.
- [x] 3.2 Run formatting, lint, and type checks for the touched Python scope.
- [x] 3.3 Validate the active OpenSpec change strictly and verify the completed
  tasks remain coherent without archiving the change.
