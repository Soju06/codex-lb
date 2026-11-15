## Context and decision

SQLite stores DateTime values as text. A legacy `2026-09-19 10:30:18` parses to the same Python datetime as `2026-09-19 10:30:18.000000`, yet literal SQL equality against the bound value can miss. Selecting the same oldest expired rows forever prevents progress.

Page by increasing primary key and advance the cursor from the last selected row, regardless of update success. Each row is attempted once in one invocation with constant cursor memory; later batches remain reachable. Rows already passed can be reconsidered by the next scheduled sweep. Preserve the exact reset timestamp guard rather than weakening concurrency ownership.

This is a termination fix. It does not normalize legacy data or make timestamp representations compare equal, and does not change lazy reset or admission behavior. The regression writes second-precision timestamps directly through SQL, uses batch sizes one and two, and bounds SELECT attempts deterministically rather than relying on a wall-clock timeout.

## Source

Adapted from the focused fix and regression strategy in https://github.com/Soju06/codex-lb/pull/2654; issue https://github.com/Soju06/codex-lb/issues/2652.
