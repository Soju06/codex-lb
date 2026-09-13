## Why

Production (soju07, 2026-09-07) carried 15 client-cancelled Responses requests for four days. Each kept three tasks alive -- Starlette's streaming body task, the SSE keepalive injector's chunk task, and the startup-probe first-item task -- with `Task.cancelling()` near 1.4 billion, and the profile attributed roughly a third of the saturated core to anyio cancellation delivery plus the cancellation-deferring wait re-entering itself.

A level-cancelled anyio scope re-delivers `Task.cancel()` to its host task on every event-loop iteration until the task leaves the scope. `_prepend_first_task` awaited the probe task directly (`await first_task`) and `inject_sse_keepalives` awaited its pending chunk task directly during teardown (`await pending`). `Task.cancel()` cascades down the `_fut_waiter` chain, so each re-delivery reached the probe task's cancellation-deferring cleanup wait. That wait is shielded with `anyio.CancelScope(shield=True)`, but the shield protects only a task anyio tracks; the probe is a plain `asyncio.create_task`, so the cascaded cancel re-entered the loop once per iteration for as long as the cleanup took. When the cleanup was itself blocked (see `fix-anyio-lock-cancelled-waiter-deadlock`), the spin never ended and starved every other task on the loop.

## What Changes

- `_prepend_first_task` awaits the startup-probe task through `wait_on_shared_future`, so a level-cancelled body task cancels only its own proxy future and the probe receives exactly the single explicit teardown `cancel()`.
- `inject_sse_keepalives` awaits its cancelled pending chunk task through the canonical cancellation-deferring helper during teardown, so the chunk task still settles before the outer finalizers close the iterator chain it drives (`aclose()` on a still-running async generator raises) and its eventual exception is retrieved, while a level-cancelled consumer no longer re-cancels it every iteration.
- `_prepend_first_task` teardown cancels the probe task and then waits for its deferred bridge cleanup through the same helper, preserving the "cancel and await handed-off preflight work" contract without the cascade.
- Document in the canonical defer-cancellation helper that its anyio shield only covers anyio-tracked tasks and that cross-task awaits must go through the proxy future.
- Add regressions that run a level-cancelled Starlette-shaped consumer against a probe whose cleanup is blocked and assert the probe and chunk tasks are cancelled at most once, that teardown still waits for the deferred cleanup, and that the source is closed only after the chunk task settles.
- No public API, configuration, persistence schema, or wire-format changes.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: Require that a cancelled streamed response does not re-cancel deferred startup-probe or keepalive-chunk work on every event-loop iteration, while still waiting for that work to settle before closing the iterator chain it drives.

## Impact

Affected code is `app/modules/proxy/api.py::_prepend_first_task`, `app/core/utils/sse.py::inject_sse_keepalives`, a comment in `app/core/utils/shared_future.py`, and one unit test module. Client-visible streaming behaviour is unchanged; teardown of a cancelled response still waits for the deferred bridge cleanup, but idles on a proxy future instead of spinning the event loop until that cleanup finishes.
