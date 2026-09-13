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

- Sample the final pending-state liveness under `session.pending_lock` immediately before taking `_http_bridge_lock`, and make the registry-side decision (identity, close claim, event generation, fences) under `_http_bridge_lock` alone. Nesting `pending_lock` inside the global registry lock would stall bounded cleanup for every other session; the pending snapshot is advisory, and the generation re-check under the registry lock closes the gap for events that arrive between the two holds.
- Compute the final response-event signal from the current pending request states, while retaining the caller's explicit signal as a lower bound for callers that already observed an event. If any current request has a response id or response-created latency, treat it as healthy too.
- Only then set `session.closed`, unregister the session, and claim the upstream close. If liveness is present, clear only the entry-time retirement flags and return; preserve any fence raised during suspension.
- Retirement entered from the reader-failure funnel (and deferred retirement of an already-closed session after the last admission waiter cancels) opts out of the revive entirely (`allow_liveness_revive=False`). Those callers have already terminally failed every pending turn and condemned the reader, so there is no turn left for the revive to save — and the completed-response signal can be advanced by durable-anchor rehydration, which copies registry state without any upstream evidence. Reviving there would leave a condemned, readerless session registered and reusable.
- Capture the upstream-event generation baseline at entry, in the same pending-lock hold that snapshots the completed-response baseline. A prelude-only upstream event during the retry-circuit await advances the generation without touching per-request response counters; an entry-time baseline makes it count as post-suspension liveness.
- Gate the revive on registry identity: only clear retirement flags while `self._http_bridge_sessions.get(session.key) is session` under `_http_bridge_lock`. The acquisition loop can detach a `retiring_with_visible_requests` generation with `mark_closed=False` while the retire coroutine is suspended; reviving that detached generation would orphan it (no close scheduled, drain-retirement a permanent no-op) and leak its socket, durable/account leases, and capacity slot. A detached generation falls through to the bounded close instead, preserving main's detached-lifecycle ownership.
- Snapshot the retirement fences (`closed`, `reconnect_requested`, `retire_after_drain`) at entry and block the revive when any fence was newly raised during the suspension. Fence owners (durable stale-owner rejection, never-graft replacement rejection, previous-response-owner unavailability, durable alias-registration failure) condemn the session while leaving it registered and the upstream close unclaimed, so neither the identity gate nor the close-claim guard can see them — and the same event dispatch that raises the fence also bumps the event generation that proves liveness. Entry-time retirement state belongs to this retirement and is still cleared on revive; a newly fenced session falls through to the bounded close, which conservatively satisfies the fence owner's intent.

## Risks / Trade-offs

- [Lock ordering] The final decision deliberately avoids nesting `pending_lock` inside `_http_bridge_lock`: the pending snapshot is taken and released before the registry lock is acquired, so no new lock-order edge is introduced and bounded cleanup for other sessions is never blocked on a session-local lock.
- [Completed request removed] A terminal event removed before the final sample is no longer available in pending state; this change does not invent durable session-wide event history.
- [Anchor poison clear] Eventless retirement may now also await a durable anchor-poison clear (issue #1830). The liveness sample is taken after that await, so late events are still observed, but a session resurrected by this check can have had its durable anchor abandoned by the poison clear. That degrades continuity for the surviving session; it does not corrupt it, and it only happens at the consecutive-failure threshold.

## Migration Plan

Deploy as a normal application change. Rollback is a revert of the single commit; no data migration is required.

## Open Questions

None.
