## Context

`_retire_stale_pending_http_bridge_session` awaits retry-circuit persistence before marking the session closed. During that suspension, upstream event handling can update a pending request's response-event count. The current close path never observes that update.

## Goals / Non-Goals

**Goals:**

- Make the close decision use fresh pending-turn liveness after all suspension points.
- Keep registry removal and session close mutually exclusive with concurrent event bookkeeping.
- Retain genuine stale-session retirement and existing retry-circuit behavior.

**Non-Goals:**

- Redesign retry-circuit persistence or bridge event routing.
- Change stale thresholds, retry details, or public response behavior.

## Decisions

- Hold `_http_bridge_lock` while acquiring `session.pending_lock` for the final decision. This keeps registry identity and pending liveness from being observed as a mixed snapshot.
- Compute the final response-event signal from the current pending request states, while retaining the caller's explicit signal as a lower bound for callers that already observed an event. If any current request has a response id or response-created latency, treat it as healthy too.
- Only then set `session.closed`, unregister the session, and claim the upstream close. If liveness is present, return without mutating retirement state.

## Risks / Trade-offs

- [Lock ordering] Nested locks could deadlock if another path acquires them in reverse order. Existing bridge registry cleanup uses bridge-lock-then-pending-lock ordering; keep the new critical section consistent.
- [Completed request removed] A terminal event removed before the final sample is no longer available in pending state; this change does not invent durable session-wide event history.

## Migration Plan

Deploy as a normal application change. Rollback is a revert of the single commit; no data migration is required.

## Open Questions

None.
