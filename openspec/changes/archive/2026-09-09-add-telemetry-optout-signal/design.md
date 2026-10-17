## Context

Snapshot transmission currently resolves consent before building the payload and uses an
instance identity to register, activate, sign, and post through an isolated HTTP client. The
settings PUT persists dashboard decisions through a request-scoped database session. See
`proposal.md` for motivation and `specs/telemetry/spec.md` for the changed contract.

The opt-out signal is unusual because its triggering decision has already made consent inactive.
It therefore cannot depend on the normal consent-gated sender context, and its asynchronous work
cannot retain request-scoped database or HTTP resources.

## Goals / Non-Goals

**Goals:**

- Preserve one authoritative consent resolution for each snapshot and preview.
- Emit an opt-out only from a dashboard-driven effective active-to-inactive transition.
- Keep the settings response independent from all collector network activity.
- Preserve canonical serialization, signing, bounded retries, and debug-only failure handling.

**Non-Goals:**

- No new setting, scheduler cadence, collector implementation, or database migration.
- No historical opt-out backfill or notification when the environment kill switch disables
  telemetry.
- No delivery guarantee beyond the existing bounded best-effort telemetry discipline.

## Decisions

### Pass resolved consent into snapshot construction

The scheduler and preview API will pass their already-resolved active consent state into the
snapshot builder. This keeps the envelope deterministic and prevents a second resolution from
observing a different state. Resolving consent again inside the builder was rejected because it
would duplicate policy and could disagree with the caller's send decision.

### Detect the effective transition around persistence

The PUT handler will resolve consent before persisting the decision and again afterward. It will
schedule an opt-out only when the first resolution is active and the second is inactive. This
naturally excludes disabled-to-disabled writes and both environment override values, while
allowing a later re-enable/re-disable cycle to produce a new event. Comparing only persisted
values was rejected because it would incorrectly notify while an environment override controls
effective behavior.

### Give the background task explicit immutable inputs

Before scheduling, the handler will obtain the instance identity and gather the version and
platform fields needed for registration and activation. The sender will then open and close its
own HTTP client, lazily register and activate, and post the signed canonical opt-out body. A
module-owned task set will retain a strong reference until completion. Reusing the request's
database session or the consent-gated sender context was rejected because those resources and
policy no longer match the task's lifetime.

### Reuse the sender's bounded delivery discipline

Opt-out delivery will share the snapshot path's five-second total timeout, at-most-one retry,
canonical JSON, signing, accepted-status handling, and debug-only exception isolation. The
event body is:

```json
{"app_version":"1.2.3","event":"optout","instance_id":"550e8400-e29b-41d4-a716-446655440000","occurred_at":"2026-08-20T12:00:00+00:00"}
```

The route order for an uninitialized process is registration, activation, then opt-out. Waiting
for network completion in the API handler was rejected because collector latency must not affect
the operator's settings response.

## Risks / Trade-offs

- **Process exit can cancel the best-effort task** → Server-side idempotency and per-transition
  scheduling make retries safe, while avoiding shutdown delay or API coupling.
- **Concurrent duplicate dashboard requests can each observe a transition** → Persisted state
  serialization and transition tests constrain ordinary request behavior; the collector remains
  idempotent for rare delivery duplication.
- **Registration or activation outage prevents the event** → The sender swallows the bounded
  failure at debug level, matching snapshot availability and privacy behavior.
- **A future snapshot caller could pass disabled consent** → The type narrows the payload to the
  two wire-valid states, and callers only build while resolved consent is active.

## Migration Plan

Deploy the additive client contract together with collector support for `/v1/optout`. Existing
instances require no data migration. Rollback removes the new snapshot field and notification;
the collector's idempotent endpoint can remain deployed without affecting older clients.
