## Context

Direct WebSocket lifecycle code clears account-lease ownership before awaiting the corresponding asynchronous release. A cancellation delivered during that await can stop the release after the ownership reference is gone, permanently consuming account-local capacity. The codebase already has `_await_task_deferring_cancellation`, which lets an owned task finish while retaining the caller's cancellation for later propagation.

## Goals / Non-Goals

**Goals:**

- Finish each scoped direct WebSocket lease release exactly once before cancellation escapes.
- Preserve ownership ordering by clearing the owning field before release starts.
- Preserve ordinary release success and failure behavior.
- Prove repeated cancellation with event-gated tests and a lifecycle driver.

**Non-Goals:**

- Changing Live, HTTP bridge, settlement, health, affinity, or routing behavior.
- Adding another cancellation helper, shield loop, timeout, setting, or dependency.
- Protecting release calls whose caller still retains ownership.

## Decisions

1. Move the established cancellation-deferring helper unchanged from the streaming retry module into common service support. This is the smallest dependency-neutral location shared by streaming and direct WebSocket code. Importing from the Live implementation or duplicating its loop would couple sibling transports or create divergent cancellation semantics.
2. At each scoped direct WebSocket seam, clear ownership first, create one owned release task, await it through `_await_task_deferring_cancellation`, and re-raise the returned cancellation only after release completes. A bare `asyncio.shield` is insufficient because repeated or level cancellation can interrupt the outer await while cleanup is still suspended.
3. Keep release failures on their existing path. Cleanup sites that currently log release failures continue to log them; sites that propagate release failures continue to propagate them. Cancellation deferral changes ordering only when cancellation races an in-progress release.
4. Use events that subscribe before cancellation and gate release completion explicitly. Tests assert ownership is already clear at release entry, inject repeated cancellation while release is suspended, and require `release-finish` before `cancel-reraised` with one release call.

## Risks / Trade-offs

- [Risk] Cancellation latency now includes the lease release duration. → Mitigation: only already-required ownership cleanup is deferred; no extra retry or timeout is introduced.
- [Risk] Broad conversion could alter HTTP bridge or non-owned release behavior. → Mitigation: edit only direct WebSocket call sites that clear ownership before release.
- [Risk] A release exception can race cancellation. → Mitigation: retain the established helper's result/exception semantics and existing caller-specific error handling.
