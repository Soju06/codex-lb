## Why

The 2026-09-07 production busy spin (`fix-probe-task-cancel-cascade-spin`) was caused by two bare `await <task>` sites: the SSE keepalive injector awaited its chunk task directly on teardown, and `_prepend_first_task` awaited the startup-probe task directly. Both tasks drive async generators whose cleanup defers cancellation, so a level-cancelled anyio scope re-cancelled them through the `_fut_waiter` cascade on every loop iteration. The existing cancellation safety gate only rejects `asyncio.shield()` retry loops; it did not see this shape, and nothing stops a new direct await of a probe or chunk task from being written tomorrow.

## What Changes

- Extend `scripts/check_cancellation_safety.py` with a second structural rule: a bare `await` of a task that may defer cancellation is rejected. A task may defer cancellation when it is created from an async-iterator step (`anext(...)`, `.__anext__()`), from a module function whose body drives an async iterator (`anext`, `__anext__`, `async for`) or calls a cancellation-deferring helper (including import aliases), from `_create_first_stream_probe_task`, or when it arrives as an `asyncio.Task`-annotated parameter (provenance is lost at the call boundary). Awaits through `wait_on_shared_future`, the deferring helpers, or after settlement via `asyncio.wait(...)` remain allowed, as do bare awaits of tasks whose coroutine neither iterates nor defers.
- Convert the one remaining repository site the rule finds: `_iter_sse_events._cancel_pending_chunk` in `app/core/clients/proxy.py` awaits the cancelled chunk task through `_await_task_deferring_cancellation` instead of directly.
- Add checker regressions for both incident shapes, the task-parameter shape, the deferring-helper and `async for` shapes, the scheduler task factory, and the allowed proxy/settled/plain variants; the CLI reports a distinct reason per violation kind.
- No public API, configuration, persistence schema, or wire-format changes.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-architecture`: Require the repository architecture gate to reject direct awaits of tasks that may defer cancellation.

## Impact

Affected code is the architecture gate script, its unit tests, one `app/core/clients/proxy.py` teardown await, and the `proxy-architecture` spec. The gate passes on current `main`; it rejects the pre-fix `inject_sse_keepalives` and `_prepend_first_task` code.
