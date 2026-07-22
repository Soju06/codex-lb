## Context

`AuditService.log_async()` creates an unreferenced task that writes through the main database engine. `POST /api/fleet/refresh` shields refresh work from caller cancellation and originally retained the task only after cancellation, but its module-level task set had no shutdown consumer. The application already rejects and drains in-flight HTTP requests, drains proxy persistence, and later stops the usage singleflight before closing the shared HTTP clients and both database engines.

The in-flight drain is bounded and can legitimately time out while a handler is paused before either producer. A one-shot audit/fleet drain can then observe empty registries before the late handler creates work. Rechecking a live set cannot close this producer race by itself; shutdown needs a linearization point after which new control-plane tasks are not admitted.

The change must preserve the response-latency contract: audit calls remain fire-and-forget, and an ordinary fleet request still waits for its requested refresh. It must also share the existing `shutdown_drain_timeout_seconds` budget rather than add an operator setting.

## Goals / Non-Goals

**Goals:**

- Give every detached audit write and every fleet refresh a strong owner until completion.
- Atomically stop audit/fleet task admission after the in-flight drain attempt so late handlers cannot create resource work after the control-plane drain.
- Wait for both task classes concurrently during graceful shutdown, before usage singleflight, HTTP-client, and database teardown.
- Consume task outcomes, remove completed tasks deterministically, and isolate one task class's failure from the other drain.
- Bound the normal drain wait with the existing shutdown timeout and identify overdue tasks in logs.

**Non-Goals:**

- Changing audit payloads, ordinary fleet refresh policy, or response schemas.
- Making detached writes durable across process crashes or forced termination.
- Replacing the existing proxy persistence drain or creating a general background-job framework.
- Adding a new timeout or configuration surface.

## Decisions

### Close admission synchronously before draining control-plane tasks

`app.core.shutdown` owns a control-plane task-admission boolean separate from the mutable HTTP draining state. Immediately after `wait_for_in_flight_drain()` returns—successfully or by timeout—the lifespan closes admission on the next synchronous line, before logging or awaiting any other shutdown step. The final control-plane drain closes it again defensively.

Audit and fleet producers check the gate before constructing their coroutine. The check, task creation, and registry insertion contain no `await`, and the shutdown cutoff runs on the same event-loop thread. A producer is therefore ordered either before the cutoff, with its task registered for draining, or after the cutoff, with no task or resource coroutine created.

`reset()` reopens admission for a new lifespan. Shutdown no longer calls `reset()` in the resource-teardown tail, so timed-out request handlers cannot regain admission before `close_db()`. Tests reset this process-global state before and after each case.

### Keep module-owned task registries

Audit and fleet each retain a typed `set[asyncio.Task[...]]`. Creation registers the task before control returns to the event loop. The awaiting caller or task-specific callback consumes or observes the outcome, and a done callback discards ownership. Fleet registration happens for every accepted refresh at creation; caller cancellation only adds cancellation-specific exception reporting and does not establish ownership. This follows the existing fleet and proxy ownership pattern while keeping task-specific failure messages in the owning module.

A queue or process-wide task manager was considered, but it would broaden the change and obscure which shutdown contract applies to each task class.

### Share one small shutdown wait primitive

`app.core.shutdown` will expose a typed helper that repeatedly snapshots unfinished tasks, waits only until one absolute deadline, yields one event-loop turn for done callbacks, and returns any tasks still pending. Rechecking after callbacks avoids a one-shot snapshot race without coupling logging policy to the helper.

Each module wraps that primitive, logs its own overdue task names, and returns whether it drained completely. Tasks are not force-cancelled when the grace period expires: cancellation can itself outlive the deadline because database session rollback/close is shielded, and fleet refresh uses shielded usage singleflight work. The later usage-scheduler stop remains the existing cancellation backstop for overdue fleet refresh work.

### Drain both task classes concurrently and before usage teardown

The lifespan runs the two module drains concurrently with `return_exceptions=True`. This gives both classes the same wall-clock grace period and ensures an unexpected failure in one drain cannot skip the other. The coordinator requires two clean passes, yielding once between them, while sharing one absolute `shutdown_drain_timeout_seconds` deadline. The extra pass catches sibling work scheduled by completion callbacks without extending the operator's timeout; an exception or overdue result ends the resweep and preserves degraded shutdown.

The call is placed after the in-flight drain attempt and proxy-persistence draining and after the replica heartbeat has stopped/been marked stale, but before scheduler shutdown. Timed-out handlers may still exist, but the admission barrier prevents them from adding new audit/fleet work; all accepted fleet work was registered at creation. The replica no longer extends its active ring lifetime, while tracked fleet work still has its HTTP client and usage singleflight available and audit work still has its database engine.

### Preserve bounded degraded shutdown

If the deadline expires, the owning module logs every still-pending task and shutdown proceeds through the existing teardown. This preserves the configured upper bound and makes the degraded case observable. The normal graceful path removes the database race by finishing these tasks before any shared client or engine is disposed.

## Risks / Trade-offs

- **A task can exceed the configured drain timeout** → log its stable task name and proceed exactly as the existing proxy persistence drain does; forced process termination remains outside the graceful guarantee.
- **A done callback or drain wrapper fails unexpectedly** → consume task exceptions in the owner and use `gather(..., return_exceptions=True)` at the lifecycle boundary so the peer drain still runs.
- **A handler survives the in-flight timeout** → the synchronous admission barrier either captured its already-registered work or rejects its later producer call before coroutine creation.
- **A task completes while a drain is finishing** → the shared helper yields/rechecks the live registry before declaring success.
- **A completion callback schedules sibling work after both module drains look clean** → the coordinator yields and requires a second clean pass within the original absolute deadline.
- **Shutdown latency increases** → only already-running detached work is awaited, both classes drain concurrently, and the existing timeout caps the normal wait.

## Migration Plan

Code-only and zero-config. Deploy through the normal rolling restart path. Rollback is a source revert; there is no schema or persisted-state migration.

## Open Questions

None.
