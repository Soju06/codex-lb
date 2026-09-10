## Context

Stream reservation settlement is detached from the response path. A failed
settlement schedules one release task in the existing tracked background-task
set, but that release currently catches its own exception and returns normally.
The done callback consequently removes the task, so the persistence drain can
report success while the reservation remains active.

Reservation release is already transactional and idempotent: once another
settler has changed the reservation from `reserved`, a later release is a
no-op. The existing stale sweep remains a last-resort repair, but its six-hour
age threshold is too slow for a known live cleanup chain.

## Goals / Non-Goals

**Goals:**

- Keep transiently failing fallback release work visible to the existing task
  drain.
- Retry until the idempotent release succeeds, with bounded retry pressure.
- Preserve detached response latency, settlement-before-health ordering, and
  exactly-once accounting.

**Non-Goals:**

- Changing reservation amounts, quota admission, or stale-sweep timing.
- Adding a durable job queue, setting, migration, or new public API.
- Refactoring request-log persistence or unrelated cleanup ownership.

## Decisions

1. **Retry inside the already tracked release task.** The release coroutine
   stays pending between attempts, so the current task registry and recursive
   drain remain the single source of cleanup ownership. Creating a second
   registry or a durable retry row would duplicate state for a narrow failure.

2. **Use capped exponential delay plus a shared retry gate for every persistence
   exception.** The outer retry covers transient PostgreSQL/session failures
   that the API-key service's SQLite-lock-specific retry does not classify. A
   fixed delay cap prevents each task from retrying rapidly, while a per-service
   concurrency gate prevents many failed streams from opening repository
   sessions simultaneously. Waiting tasks stay tracked without holding a
   database connection.

3. **Rely on reservation transition idempotency.** A retry cannot double
   decrement quota: release only claims a reservation still in `reserved`
   state, and a concurrent finalizer or release makes subsequent attempts
   no-ops.

4. **Let the existing drain deadline bound shutdown waiting.** A recovered
   release completes normally. A release still retrying at the deadline remains
   pending, so `drain_persistence_tasks` returns `False` instead of claiming
   durability. No separate retry-count terminal state is introduced.

## Risks / Trade-offs

- **A permanent persistence error leaves a task alive during normal runtime.**
  → Retries use capped backoff; the task accurately represents unfinished
  cleanup, and stale recovery remains the final repair path.
- **Many simultaneous failures could retry together after an outage.**
  → A shared four-attempt gate bounds aggregate repository pressure in each
  service instance; exponential delay also bounds each task's retry frequency,
  and the change adds no inline request-path work.
- **Cancellation can stop a retry after shutdown has already timed out.**
  → The drain first reports incomplete, so process termination cannot be
  mistaken for successful settlement.
