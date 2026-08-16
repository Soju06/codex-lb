# Detached control-plane task lifecycle

## Purpose and scope

Audit writes are deliberately detached so dashboard mutations and authentication responses do not pay for a second database commit. Fleet refreshes are deliberately shielded so a client disconnect does not interrupt a refresh that already owns its own database session. This change preserves those latency and ownership choices while connecting both task classes to graceful shutdown.

It covers only `AuditService.log_async()` and `POST /api/fleet/refresh` work. Proxy persistence, periodic schedulers, OAuth polling, and abrupt process death keep their existing lifecycle contracts.

## Decision rationale and constraints

The existing shutdown timeout is reused because the operator has already chosen how long a graceful drain may wait. Both drains run concurrently, so adding the second task class does not double that configured grace period. Module-owned registries keep audit and fleet failure messages specific and avoid introducing a generic task-management subsystem.

Fleet draining must happen before the usage scheduler stops: that stop cancels the process-wide usage singleflight, including work used by the fleet endpoint. It must also happen before shared HTTP clients close. Audit draining must happen before database disposal. Running both together at the earlier boundary satisfies all three constraints.

The in-flight wait is a grace period, not proof that every handler exited. Its timeout must therefore be followed immediately by a synchronous producer cutoff. The gate is independent of the HTTP draining flag: a request that entered before draining may continue, but after cutoff it cannot enqueue a new audit write and cannot start a fleet refresh. Every fleet refresh accepted before cutoff is placed in its registry before the route first awaits it, so later caller cancellation cannot make it invisible to shutdown.

## Failure modes

- A completed task that failed is removed and its exception is consumed so asyncio does not emit an unowned-task warning.
- A late asynchronous audit producer is skipped without constructing its database coroutine and logs the rejected action.
- A late fleet producer receives the dashboard `503 service_unavailable` envelope without starting a refresh task or background session.
- A drain wrapper failure is isolated; the other task class still receives its grace period.
- A task that outlives the configured timeout is named in logs and shutdown proceeds. Forced termination can still lose it, which is unchanged and outside the graceful guarantee.
- The drain helper rechecks the live registry after completion callbacks run so it does not declare success during their scheduling turn.
- The lifecycle coordinator requires two clean audit/fleet passes with one event-loop yield between them, but both passes share the original absolute timeout.

## Concrete example

An operator restarts codex-lb while an account-import handler is paused and a fleet refresh is running. The in-flight grace period expires, so shutdown closes task admission and begins its bounded control-plane drain. The fleet caller disconnects only afterward, but the refresh was already registered at creation and remains owned. When the account-import handler resumes, its late audit call returns immediately and reports that it was rejected rather than opening a database coroutine. If the fleet task finishes within the configured 30-second default, its session closes before usage singleflight, HTTP clients, or database engines are torn down.
