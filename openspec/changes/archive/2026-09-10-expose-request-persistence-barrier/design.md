## Context

See proposal.md and context.md. The proxy owns request-log and background-cleanup task sets; shutdown uses one existing task-name classifier. Those sets retain completed tasks until their done callbacks run.

## Goals / Non-Goals

Expose that ownership synchronously without touching the DB, cancelling tasks or invoking shutdown draining. Preserve both existing registries and callback behavior. Do not certify successful writes or change deployment logic.

## Decisions

Add a synchronous proxy observation that counts the union of canonical registered persistence tasks, including done tasks. Reuse the shutdown classifier rather than add a separate list or counter. Same-loop observation cannot interleave a synchronous callback's remove/register handoff. Keep waiting/drain behavior unchanged.

Map a valid nonnegative integer to pending/drained and a decimal count at the health route. Missing, noncallable, throwing or invalid observation yields unknown without a count. Existing bridge activity observation remains independent. A separate persisted counter or a timed drain call would create new state or side effects and is unnecessary.

## Risks / Trade-offs

Completed owners can conservatively remain pending for one callback turn. This is required to avoid false zero. A callback's unexpected logged failure can leave no owner; drained describes ownership only and never claims historical persistence success. Counting is proportional to the already registered owner set and does not wait for work.

## Migration Plan

Additive loopback response fields require no migration or setting. Consumers must treat absent/unknown fields as unsupported; predecessor deployment remains a separate reviewed operation.
