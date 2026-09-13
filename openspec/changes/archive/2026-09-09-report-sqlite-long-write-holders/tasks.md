## 1. Watchdog

- [x] 1.1 Track from the completion of the first successful write statement (after_cursor_execute — a statement waiting in the busy timeout has not acquired the slot) and report at commit/rollback when the hold exceeded the busy timeout, with duration, outcome, task name, and first/last write statements; the report is deferred to the next begin or pool checkin so a stalled commit/rollback is inside the measured hold
- [x] 1.2 Install it from `_configure_sqlite_engine` so the main, background, and memory engines are all covered

## 2. Tests

- [x] 2.1 A write transaction over the threshold is reported with its statement and outcome
- [x] 2.2 Read-only transactions and fast writes stay silent
- [x] 2.3 A write that fails while waiting for the slot does not report its victim transaction as the holder
- [x] 2.4 A stalled transaction end is included in the measured hold (fails on report-at-event code)
- [x] 2.5 A bare BEGIN IMMEDIATE holder is attributed; a DBAPI commit failure reports commit_failed_rollback
