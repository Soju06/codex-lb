## Context

`app/core/utils/shared_future.py` already provides `wait_on_shared_future()` (one fan-out callback per shared future, one proxy future per waiter) and `_await_task_deferring_cancellation()` (finish owned cleanup, then surface the caller's cancellation). Both assume the cancellation they defend against arrives at the task that runs them. The 2026-09-07 incident showed a second delivery path: anyio level cancellation of a *different* task that awaits the deferring task directly, cascading through `Task.cancel()` on each loop iteration.

## Decisions

### D1. Break the cascade at the task boundary, not inside the deferring helper

The deferring helper cannot stop a cascade because every await it performs is what the cascade cancels. The fix belongs where an anyio-tracked task awaits an `asyncio` task that may defer cancellation: `_prepend_first_task` (probe task) and the `inject_sse_keepalives` teardown (chunk task). Awaiting through `wait_on_shared_future` gives the tracked task its own proxy future to be cancelled; the awaited task is untouched apart from the explicit teardown `cancel()` the code already performs.

### D2. Teardown still waits for the awaited task to settle

Both sites drive an async-generator chain from the awaited task. Returning from teardown early would let outer finalizers (`_wrap_source_responses_public_stream` and the injector's own `finally`) call `aclose()` on generators the task is still running, which raises `RuntimeError: aclose(): asynchronous generator is already running`, and would leave the task's eventual exception unretrieved. Teardown therefore cancels the task once and then waits through `_await_task_deferring_cancellation`: its anyio shield stops the level re-cancel of the tracked consumer task, its proxy wait absorbs any remaining direct cancel, and it retrieves the task's result or exception. The wait lasts as long as the deferred cleanup, exactly as the old spinning await did, but idles instead of burning the loop.

### D3. No new primitive, no detached tasks

The change reuses `wait_on_shared_future` and `_await_task_deferring_cancellation`; no iterator hand-off flag or background close task is introduced.

## Risks / Trade-offs

- **Teardown still blocks on deferred cleanup**: intentional and unchanged from the old behaviour; when that cleanup is itself wedged (see `fix-anyio-lock-cancelled-waiter-deadlock`) the request tasks stay parked but no longer consume CPU.
- **Other direct awaits of `asyncio` tasks**: the AST cancellation gate does not yet reject `await task` across a task boundary. This change fixes the two sites on the production path and documents the rule; a structural gate is a follow-up.
