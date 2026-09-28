## Why

When the server receives SIGTERM shortly after startup, shutdown cancels
background scheduler tasks and the leader-lease keeper while they are inside
database work (issue #2505).
- On SQLite this logs `Exception closing connection` / `Exception during reset`
  with a `CancelledError` through aiosqlite `rollback()` or `close()`.
- The lease keeper's cancellation also leaves the leader lease unreleased and
  the SQLite run-state `running`. Shutdown then waits out the lease-release
  deadline (about 10.5s instead of about 0.5s), and the next startup runs the
  integrity check.

In a harness that sends SIGTERM immediately after the first healthy `/health`,
unmodified `main` reproduced the pool errors in 12/30 and 11/20 runs, and left
the lease or run-state unclean in 5/30 and 2/20.

The lease keeper's own docstring already describes the intended behavior:
"signals the keeper to exit and awaits it … cancels as a fallback". The code
cancelled immediately.

## What Changes

- Stopping a DB-owning background task sets its stop event, then waits up to
  **2 seconds** for it to finish the unit of work it is in, and cancels it only
  if it is still busy after that grace. Cancelling logs a WARNING naming the
  task.
- This applies to the leader-lease keeper and to the scheduler loops observed
  holding a connection when cancelled:
  - model refresh;
  - cache-invalidation poller;
  - sticky-session cleanup;
  - usage rollup;
  - metadata refresh (its missing-cost backfill);
  - quota planner.
- Idle tasks are unaffected. Every one of these loops waits on its stop event
  between ticks, so it exits immediately. The grace only applies to a task
  caught mid-tick.
- No new setting. The grace is a fixed constant.

## Capabilities

### New Capabilities
<!-- none -->

### Modified Capabilities
- `graceful-shutdown`: background DB-owning tasks finish in-flight work within
  a bounded grace before cancellation, and the leader lease is released on a
  prompt shutdown.

## Impact

- New helper `app/core/scheduling/task_shutdown.py`
  (`stop_task_after_grace`).
- `stop()` paths in `app/core/scheduling/leader_election.py`,
  `app/core/openai/model_refresh_scheduler.py`,
  `app/core/cache/invalidation.py`,
  `app/modules/sticky_sessions/cleanup_scheduler.py`,
  `app/modules/accounts/usage_rollup_scheduler.py`,
  `app/core/usage/metadata_scheduler.py`, and
  `app/modules/quota_planner/scheduler.py`.
- Shutdown time: unchanged for idle tasks. The stops run sequentially, so the
  worst case adds up to 2s per task caught mid-tick in slow DB work.
- **PostgreSQL was not tested.** The logged `NullPool`/aiosqlite path is
  SQLite-specific. The code change is backend-independent, and so is the
  lease-keeper fix, but neither the symptom nor the fix has been measured on
  Postgres.
