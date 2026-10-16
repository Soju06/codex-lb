# Tasks

## 1. Connection cleanup

- [x] 1.1 Add the weakly tracked cursor connection factory for file-backed engines; verify cancellation with retained tracebacks, rollback/commit, and repeated close.
- [x] 1.2 Exercise real terminal transcript timeout persistence and verify subsequent bridge and leader writes succeed without garbage collection.

## 2. Validation and delivery

- [x] 2.1 Run focused DB, leader, and bridge tests on both observed SQLAlchemy versions, lint/type checks, and strict change/spec validation; record results and sync stable context.
- [x] 2.2 Build a separate wheel with dashboard assets, verify its contents and isolated runtime, and provide a frozen-dependency uvx launch command.

## 3. Review follow-up

- [x] 3.1 Attempt all cursor closes after an ordinary cleanup exception, preserve the first error, and verify subsequent independent writes, rollback, base close, and idempotency in a real SQLite regression.
- [x] 3.2 Document touched functions and re-run focused tests, lint/type checks, and spec validation before pushing the review fix.

## Workflow follow-up

- Verify and archive the implementation after local checks.
- Restart the foreground service with the repaired wheel and monitor real traffic for recurring locks; record operational validation separately from regression results.
