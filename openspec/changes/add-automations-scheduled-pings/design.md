## Context

We are adding scheduled "ping" automations that execute a configured prompt against a chosen model on selected accounts at a fixed local time (daily schedule).

This is a cross-cutting change across:
- data model (new jobs/runs tables + cycle snapshot tables),
- backend API + scheduler/runtime execution,
- frontend CRUD and run visibility.

Constraints:
- multi-replica runtime already exists (leader election available),
- existing account auth/refresh and proxy client should be reused,
- sticky thread/session behavior must remain untouched.

## Goals / Non-Goals

**Goals:**
- provide dashboard-managed automation jobs (`CRUD + run-now + run history`),
- execute at-most-once per schedule slot across replicas,
- support ordered account failover for retryable account-level failures,
- expose run outcomes and failures in GUI.

**Non-Goals:**
- cron-like arbitrary expressions (only `daily` + `HH:MM` + timezone),
- replaying missed historical slots after downtime,
- custom per-job retry policies beyond account failover chain,
- changing sticky routing semantics for normal proxy traffic.

## Decisions

### 1) Persist automations in dedicated tables

Decision:
- add `automation_jobs`, `automation_job_accounts`, `automation_runs`,
- add `automation_run_cycles` and `automation_run_cycle_accounts` to freeze one cycle's eligible accounts and planned dispatch times.

Why:
- durable schedule ownership and observability require persistent state,
- ordered account preference is first-class and queryable,
- slot-level idempotency is enforceable with DB constraints,
- cycle-level snapshots prevent late account reactivation or mid-cycle threshold edits from mutating an already created run plan.

Alternative considered:
- in-memory scheduler config.
Rejected because it is non-durable and unsafe across restarts/replicas.

### 2) Enforce at-most-once using deterministic `slot_key` + unique index

Decision:
- each scheduled run claims a slot by inserting `automation_runs(slot_key)` with unique constraint.

Why:
- works naturally across replicas/races without distributed locks,
- provides audit trail for exactly what was claimed and when.

Alternative considered:
- external lock service for slot claims.
Rejected as unnecessary complexity for existing DB-backed architecture.

### 3) Run scheduler only on leader, poll at short interval

Decision:
- integrate scheduler in app lifespan, guarded by leader election checks.

Why:
- avoids duplicate work in healthy conditions,
- still safe under leadership transitions because DB claim is final guardrail.

Alternative considered:
- all replicas execute scheduler and rely only on DB uniqueness.
Rejected to reduce noisy duplicate attempts and overhead.

### 4) Reuse existing account auth + compact responses path for execution

Decision:
- execute ping via existing auth refresh (`AuthManager`) and proxy compact responses client.

Why:
- keeps execution path consistent with production request behavior,
- inherits existing error contracts and token handling.

Alternative considered:
- direct new OpenAI client path just for automations.
Rejected to avoid divergence and duplicated auth logic.

### 5) Fail over across configured accounts only for retryable account-level errors

Decision:
- retry next account for known retryable codes (`usage_limit_reached`, quota/rate-limit/auth/deactivated classes), stop for non-retryable request semantics.

Why:
- preserves user intent (same job prompt/model) while maximizing completion probability,
- prevents masking unrelated request bugs.

Alternative considered:
- retry on all failures.
Rejected because it could hide prompt/model validation errors and waste quota.

### 6) Frontend as dedicated `Automations` section in dashboard nav

Decision:
- add `/automations` route with list/form/history UX.

Why:
- isolates automation operations from live request logs/settings,
- clear operator workflow for configuration + inspection.

Alternative considered:
- embed inside existing settings tabs.
Rejected due to scope growth and degraded task-focused UX.

## Risks / Trade-offs

- [Scheduler drift / delayed trigger due to polling interval] -> keep interval short and compute due-slot deterministically per timezone.
- [Race during leadership switch] -> rely on DB unique slot claims to make duplicates harmless.
- [Account list update can violate unique position in ORM flush ordering] -> use explicit delete+flush+reinsert strategy.
- [Cycle composition mutates while scheduler keeps polling] -> persist cycle snapshots before claiming per-account runs.
- [Timezone/DST edge ambiguity] -> normalize `HH:MM`, validate timezone names, and cover DST in unit tests.
- [Operational noise from non-critical frontend warnings] -> keep warnings visible but non-blocking; enforce test pass gates.

## Migration Plan

1. Apply Alembic migration creating automation tables, indexes, and cycle snapshot tables.
2. Deploy backend with API + scheduler (enabled by config).
3. Deploy frontend with `Automations` route/navigation.
4. Verify:
   - `ruff`, `ty`, backend pytest suite, frontend vitest suite,
   - OpenSpec validation for specs/tasks.
5. Rollback:
   - disable scheduler via config flag if runtime issue occurs,
   - rollback app deployment first; DB tables can remain safely unused until full rollback migration is required.

## Open Questions

- Should we later support cron-like schedules or weekly patterns?
- Do we need per-job backoff/retry windows beyond account failover chain?
- Should automation runs emit dedicated metrics panels in dashboard overview?
