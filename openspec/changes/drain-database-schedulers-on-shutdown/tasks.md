## 1. Implementation

- [x] 1.1 Add `stop_task_after_grace` in `app/core/scheduling/task_shutdown.py`: wait up to 2s, then cancel with a WARNING naming the coroutine. Verify with 2.2.
- [x] 1.2 Use it in the lease keeper stop (`_stop_release_keeper`) and in the `stop()` of the model refresh, cache-invalidation, sticky cleanup, usage rollup, metadata refresh, and quota planner schedulers, each after setting its stop event. Verify with 2.1.

## 2. Tests

- [x] 2.1 Process-level integration test: start the real server on SQLite, send SIGTERM immediately after the first healthy `/health`, repeated several times. Assert no pool errors, no `scheduler_leader` row, and a prompt exit. Verify it fails on unmodified `main` and passes with the fix.
- [x] 2.2 Test the fallback: a task busy past the grace is cancelled and the WARNING names it; an idle task exits without waiting. Verify it passes.
- [x] 2.3 Existing shutdown and scheduler suites pass: `tests/integration/test_graceful_websocket_process_shutdown.py`, `tests/integration/test_cache_invalidation_bus.py`, and the full suite.

## 4. Review follow-ups (CodeRabbit on PR #2506)

- [x] 4.1 Bound the post-cancel wait by the same grace; log and track a task still running afterwards, and exclude the clean SQLite shutdown record while any tracked task is still running (`undrained_tasks()` in `app/main.py`'s `database_tasks_drained`). Verify with the deferring-cancellation unit test.
- [x] 4.2 Add a deterministic barrier test: a `sitecustomize` injected into the server holds the cache-invalidation poller's own read via `await_only` until the poller's stop is requested. Verify it fails on `main` (3/3, read cancelled) and passes with the fix (3/3).

- [x] 4.3 Cap both waits in `stop_task_after_grace` by the time left in the shared drain deadline (`remaining_drain_timeout_seconds()`), so the sequential stops never use the 25s post-drain reserve that the lease release (10s), metrics wait (5s) and `close_db()` teardown drain (2 x 5s) need. (A first revision reserved only 15s of the post-drain budget and left `close_db()` unbudgeted.) Verify with `test_sequential_stops_of_wedged_tasks_stay_within_the_shutdown_budget` (14.0s on the previous code against a 0.5s budget; passes now) and `test_exhausted_budget_cancels_immediately_and_still_tracks_deferring_tasks`.

- [x] 4.4 Maintainer review: every stop yields one loop turn before warning or cancelling; the leader-lease keeper uses the plain grace and awaits its cancelled task (`await_cancellation=True`), bounded by `release()`'s 10s deadline; the schedulers wait up to the plain grace after cancelling. Verify with `test_idle_loop_exits_without_warning_when_no_drain_time_left`, `test_promptly_cancelled_task_is_finished_when_stop_returns_with_no_drain_time_left` and `test_release_waits_for_the_keeper_to_finish_when_no_drain_time_left`, which all fail on `7b2e373a` and pass now.

## 3. Validation

- [x] 3.1 `npx --yes @fission-ai/openspec@1.11.0 validate drain-database-schedulers-on-shutdown --strict`, `make lint`, and `uv run ty check` pass.
