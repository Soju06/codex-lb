# Tasks

## 1. Fan-out

- [x] 1.1 `_fan_out` reads `shared.exception()` once before the waiter loop and reuses it for every waiter, so the exception is consumed even when the set is empty. Cancellation keeps its own branch and is still checked first.

## 2. Verification

- [x] 2.1 `tests/unit/test_shared_future_waiters.py`: a shared future that fails after its only waiter timed out leaves `_log_traceback` clear — the flag asyncio's destructor checks — and the exception is still readable afterwards. The test waits for the done callback rather than for `done()`, which it races.
- [x] 2.2 `tests/unit/test_shared_future_waiters.py`: the delivering path is unchanged — a failure handed to a live waiter is still consumed.
- [x] 2.3 `tests/unit/test_sse.py`: the product path. A keepalive fires, the consumer stops asking, the source ends in that gap, and no `never retrieved` record reaches the `asyncio` logger after collection. Both new tests fail against the pre-fix fan-out.
- [x] 2.4 Guards: `ruff check`, `ruff format --check`, `ty check`, the repository architecture/simplicity checks, and the full unit suite (10,348 passed).
