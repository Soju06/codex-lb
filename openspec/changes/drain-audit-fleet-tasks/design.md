## Context

`AuditService.log_async()` creates an unreferenced task that writes through the main database engine. `POST /api/fleet/refresh` shields refresh work from caller cancellation and retains the task only after cancellation, but its module-level task set has no shutdown consumer. The application already rejects and drains in-flight HTTP requests, drains proxy persistence, and later stops the usage singleflight before closing the shared HTTP clients and both database engines.

The change must preserve the response-latency contract: audit calls remain fire-and-forget, and an ordinary fleet request still waits for its requested refresh. It must also share the existing `shutdown_drain_timeout_seconds` budget rather than add an operator setting.

## Goals / Non-Goals

**Goals:**

- Give every detached audit write and cancelled-request fleet refresh a strong owner until completion.
- Wait for both task classes concurrently during graceful shutdown, before usage singleflight, HTTP-client, and database teardown.
- Consume task outcomes, remove completed tasks deterministically, and isolate one task class's failure from the other drain.
- Bound the normal drain wait with the existing shutdown timeout and identify overdue tasks in logs.

**Non-Goals:**

- Changing audit payloads, fleet refresh policy, or endpoint response schemas.
- Making detached writes durable across process crashes or forced termination.
- Replacing the existing proxy persistence drain or creating a general background-job framework.
- Adding a new timeout or configuration surface.

## Decisions

### Keep module-owned task registries

Audit and fleet each retain a typed `set[asyncio.Task[...]]`. Creation registers the task before control returns to the event loop, and a done callback consumes its outcome before discarding it. This follows the existing fleet and proxy ownership pattern while keeping task-specific failure messages in the owning module.

A queue or process-wide task manager was considered, but it would broaden the change and obscure which shutdown contract applies to each task class.

### Share one small shutdown wait primitive

`app.core.shutdown` will expose a typed helper that repeatedly snapshots unfinished tasks, waits only until one absolute deadline, yields one event-loop turn for done callbacks, and returns any tasks still pending. Rechecking after callbacks avoids a one-shot snapshot race without coupling logging policy to the helper.

Each module wraps that primitive, logs its own overdue task names, and returns whether it drained completely. Tasks are not force-cancelled when the grace period expires: cancellation can itself outlive the deadline because database session rollback/close is shielded, and fleet refresh uses shielded usage singleflight work. The later usage-scheduler stop remains the existing cancellation backstop for overdue fleet refresh work.

### Drain both task classes concurrently and before usage teardown

The lifespan runs the two module drains concurrently with `return_exceptions=True`. This gives both classes the same wall-clock grace period and ensures an unexpected failure in one drain cannot skip the other. The call is placed after in-flight and proxy-persistence draining and after the replica heartbeat has stopped/been marked stale, but before scheduler shutdown. At that point normal request producers have quiesced and the replica no longer extends its active ring lifetime, while fleet work still has its HTTP client and usage singleflight available and audit work still has its database engine.

### Preserve bounded degraded shutdown

If the deadline expires, the owning module logs every still-pending task and shutdown proceeds through the existing teardown. This preserves the configured upper bound and makes the degraded case observable. The normal graceful path removes the database race by finishing these tasks before any shared client or engine is disposed.

## Risks / Trade-offs

- **A task can exceed the configured drain timeout** → log its stable task name and proceed exactly as the existing proxy persistence drain does; forced process termination remains outside the graceful guarantee.
- **A done callback or drain wrapper fails unexpectedly** → consume task exceptions in the owner and use `gather(..., return_exceptions=True)` at the lifecycle boundary so the peer drain still runs.
- **Tasks appear while a drain is finishing** → in-flight requests are quiesced first, and the shared helper yields/rechecks the live registry before declaring success.
- **Shutdown latency increases** → only already-running detached work is awaited, both classes drain concurrently, and the existing timeout caps the normal wait.

## Migration Plan

Code-only and zero-config. Deploy through the normal rolling restart path. Rollback is a source revert; there is no schema or persisted-state migration.

## Open Questions

None.
