## 1. Implementation

- [x] 1.1 Add `stop_task_after_grace` in `app/core/scheduling/task_shutdown.py`: wait up to 2s, then cancel with a WARNING naming the coroutine. Verify with 2.2.
- [x] 1.2 Use it in the lease keeper stop (`_stop_release_keeper`) and in the `stop()` of the model refresh, cache-invalidation, sticky cleanup, usage rollup, metadata refresh, and quota planner schedulers, each after setting its stop event. Verify with 2.1.

## 2. Tests

- [x] 2.1 Process-level integration test: start the real server on SQLite, send SIGTERM immediately after the first healthy `/health`, repeated several times. Assert no pool errors, no `scheduler_leader` row, and a prompt exit. Verify it fails on unmodified `main` and passes with the fix.
- [x] 2.2 Test the fallback: a task busy past the grace is cancelled and the WARNING names it; an idle task exits without waiting. Verify it passes.
- [x] 2.3 Existing shutdown and scheduler suites pass: `tests/integration/test_graceful_websocket_process_shutdown.py`, `tests/integration/test_cache_invalidation_bus.py`, and the full suite.

## 3. Validation

- [x] 3.1 `npx --yes @fission-ai/openspec@1.11.0 validate drain-database-schedulers-on-shutdown --strict`, `make lint`, and `uv run ty check` pass.
