# Detached control-plane task lifecycle

## Purpose and scope

Audit writes are deliberately detached so dashboard mutations and authentication responses do not pay for a second database commit. Fleet refreshes are deliberately shielded so a client disconnect does not interrupt a refresh that already owns its own database session. This change preserves those latency and ownership choices while connecting both task classes to graceful shutdown.

It covers only `AuditService.log_async()` and cancelled-request `POST /api/fleet/refresh` work. Proxy persistence, periodic schedulers, OAuth polling, and abrupt process death keep their existing lifecycle contracts.

## Decision rationale and constraints

The existing shutdown timeout is reused because the operator has already chosen how long a graceful drain may wait. Both drains run concurrently, so adding the second task class does not double that configured grace period. Module-owned registries keep audit and fleet failure messages specific and avoid introducing a generic task-management subsystem.

Fleet draining must happen before the usage scheduler stops: that stop cancels the process-wide usage singleflight, including work used by the fleet endpoint. It must also happen before shared HTTP clients close. Audit draining must happen before database disposal. Running both together at the earlier boundary satisfies all three constraints.

## Failure modes

- A completed task that failed is removed and its exception is consumed so asyncio does not emit an unowned-task warning.
- A drain wrapper failure is isolated; the other task class still receives its grace period.
- A task that outlives the configured timeout is named in logs and shutdown proceeds. Forced termination can still lose it, which is unchanged and outside the graceful guarantee.
- The drain helper rechecks the live registry after completion callbacks run so it does not declare success during their scheduling turn.

## Concrete example

An operator restarts codex-lb just after changing a setting while a fleet dashboard disconnects from a manual refresh. The setting response has already returned and its audit INSERT is pending; the fleet refresh continues in a background session. Shutdown first quiesces requests, then waits for both tracked tasks concurrently. If they finish within the configured 30-second default, the audit row is committed and the fleet session closes before usage singleflight, HTTP clients, or database engines are torn down.
