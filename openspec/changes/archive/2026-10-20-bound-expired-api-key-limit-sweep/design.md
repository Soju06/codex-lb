## Context and decision

SQLite compares `DateTime` text literally. For example, stored `2026-09-19 10:30:18` parses to the same Python datetime as `2026-09-19 10:30:18.000000`, but does not equal SQLAlchemy's bound value. The existing sweep can repeatedly select and fail to update the same expired row.

Page by increasing primary key and advance the cursor from the last selected row, not the last successful update. Each selected row is attempted once per invocation; later batches remain reachable even when an entire batch misses. Keep the exact timestamp guard, batch size, commit boundary, window calculation, and successful-update count unchanged.

An exclusion list would grow with the number of skipped rows; a primary-key cursor needs one bound value. Weakening the timestamp guard would change concurrency semantics, so it is out of scope. Legacy timestamp normalization and lazy-reset/reservation compatibility remain separate work. Rows already passed by the cursor can be reconsidered on the next scheduled sweep.

## Verification

Use real SQLite storage to write second-precision timestamps, with batch sizes of one and two, two unupdatable rows, later resettable rows, and an unexpired row. Bound SELECT attempts so the old loop fails deterministically. Unit coverage checks cursor advancement through misses, successful-update counting, commits, and an empty sweep.
