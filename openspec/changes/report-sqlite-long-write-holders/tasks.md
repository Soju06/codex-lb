## 1. Watchdog

- [x] 1.1 Track the first write statement per connection transaction and report at commit/rollback when the hold exceeded the busy timeout, with duration, outcome, task name, and first/last write statements
- [x] 1.2 Install it from `_configure_sqlite_engine` so the main, background, and memory engines are all covered

## 2. Tests

- [x] 2.1 A write transaction over the threshold is reported with its statement and outcome
- [x] 2.2 Read-only transactions and fast writes stay silent
