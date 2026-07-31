## Context

Stream reservation settlement normally runs as a tracked background task so a
response close does not wait on persistence. The existing WebSocket
account-health path is the deliberate exception: health persistence must follow
API-key settlement. Its wait branch currently awaits the primary task but
discards the task's `False` result, leaving the tracking callback to schedule
fallback release later.

This is a settlement-sensitive concurrency change across the shared stream
settlement helper and the WebSocket finalizer. The design must preserve the
ordinary detached path while giving the ordering-sensitive caller a confirmed
outcome.

## Goals / Non-Goals

**Goals:**

- Confirm primary settlement or fallback release before a WebSocket
  account-health write.
- Prevent duplicate fallback release between the synchronous waiter and the
  detached task tracker.
- Preserve cancellation shielding and tracked shutdown ownership.
- Keep reconnect and connection retirement independent from database
  persistence success.

**Non-Goals:**

- Changing detached settlement retry, drain, or cleanup policy.
- Changing quota cleanup, reservation expiry, or compact settlement.
- Changing which WebSocket errors affect account health.

## Decisions

### Let the ordering-sensitive waiter own failed-settlement fallback

The settlement task remains tracked in every mode. Detached callers keep the
existing callback-owned fallback. An ordering-sensitive caller disables that
callback fallback, observes the task's boolean result, and synchronously runs
the existing fallback release helper after `False`.

This avoids racing two releases while reusing the established release path.
Always leaving fallback with the callback was rejected because awaiting only
the primary task cannot establish when the second-generation cleanup commits.

### Return confirmed settlement state

The fallback release helper reports `True` only after its repository operation
returns successfully and `False` after its existing logged failure handling.
The wait branch returns the primary or fallback result; the ordinary detached
branch continues to return immediately after ownership transfer.

### Gate health persistence, not connection safety

The WebSocket finalizer records account health only when settlement is
confirmed. When neither primary settlement nor fallback release succeeds, the
health write remains unapplied, while reconnect and retire-after-drain flags are
still set so the failed upstream connection is not reused.

### Test the real WebSocket finalizer seam

The regression drives `_finalize_websocket_request_state` with a keyed,
health-penalizing terminal event. The primary release fails, the fallback
blocks on an event, and assertions prove health remains blocked until fallback
commit. A second outcome proves health remains unapplied when fallback also
fails.

## Risks / Trade-offs

- **An ordering-sensitive error path can wait on repository persistence.**
  This is the contract's deliberate exception and is limited to keyed
  WebSocket account-health errors.
- **A persistence outage can omit a legitimate account-health observation.**
  Skipping the write is safer than reversing the settlement/health order;
  reconnect and retirement still protect the active connection.
- **Cancellation can expose the primary task and fallback to concurrent
  mutation.** The existing shielded wait and task tracker retain ownership,
  and only one path is allowed to start fallback release.

## Migration Plan

No data or configuration migration is required. Rollback restores the prior
ordering behavior without changing stored schema.

## Open Questions

None.
