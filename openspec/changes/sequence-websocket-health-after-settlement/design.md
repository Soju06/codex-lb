## Context

Stream reservation settlement normally runs as a tracked background task so a
response close does not wait on persistence. The existing WebSocket
account-health path is the deliberate exception: health persistence must follow
API-key settlement. Its wait branch currently awaits the primary task but
discards the task's `False` result, leaving the tracking callback to schedule
fallback release later.

This is a settlement-sensitive concurrency change across the shared stream
settlement helper, the WebSocket finalizer, and the existing retry path that
defers transient account-health penalties until settlement. The design must
preserve the ordinary detached path while giving ordering-sensitive callers a
confirmed outcome.

## Goals / Non-Goals

**Goals:**

- Confirm primary settlement or fallback release before a WebSocket
  account-health write.
- Apply retry-deferred account-health penalties only after the same confirmed
  settlement outcome.
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

### Let the tracked ordering-sensitive task own failed-settlement fallback

The settlement task remains tracked in every mode. Detached callers keep the
existing callback-owned fallback. For an ordering-sensitive caller, the tracked
task runs the existing fallback release helper after primary failure and only
then completes with the confirmed boolean outcome. Its fallback operation is
shielded under that task, so cancellation during primary work or fallback is
reported as unconfirmed only after the release attempt completes. If the task
is cancelled before its coroutine starts, the tracking callback schedules the
established tracked cleanup fallback instead. Any cancellation that still
reaches the done callback likewise transfers release to that tracker. The caller
disables callback fallback for ordinary failure results and awaits the
settlement outcome.

This avoids racing two releases after coroutine startup, keeps
graceful-shutdown ownership across both settlement phases, and reuses the
established release path. The callback retains cancellation fallback for cases
where no coroutine handler completes release, including pre-start cancellation.
Always leaving ordinary failure fallback with the callback was rejected because
awaiting only the primary task cannot establish when the second-generation
cleanup commits.

### Return confirmed settlement state

The fallback release helper reports `True` only after its repository operation
returns successfully and `False` after its existing logged failure handling.
The tracked ordering-sensitive task returns the primary or fallback result; the
ordinary detached branch continues to return immediately after ownership
transfer.

### Gate health persistence, not connection safety

The WebSocket finalizer records account health only when settlement is
confirmed. When neither primary settlement nor fallback release succeeds, the
health write remains unapplied, while reconnect and retire-after-drain flags are
still set so the failed upstream connection is not reused.

The retry consumer uses the same wait mode before applying deferred transient
penalties. A `False` result drops those pending penalties and gates any terminal
health write that immediately follows the wait. Because the settlement helper
has already transferred ownership, that path does not start another settlement.

### Test the real WebSocket finalizer seam

The regression drives `_finalize_websocket_request_state` with a keyed,
health-penalizing terminal event. The primary release fails, the fallback
blocks on an event, and assertions prove health remains blocked until fallback
commit. A second outcome proves health remains unapplied when fallback also
fails.

The existing post-refresh retry regression also exercises an unconfirmed wait
result and proves deferred penalties stay unapplied without another settlement
attempt.

## Risks / Trade-offs

- **An ordering-sensitive error path can wait on repository persistence.**
  This is the contract's deliberate exception and is limited to keyed
  WebSocket account-health errors and the existing retry-deferred health path.
- **A persistence outage can omit a legitimate account-health observation.**
  Skipping the write is safer than reversing the settlement/health order;
  reconnect and retirement still protect the active connection.
- **Cancellation can expose the primary task and fallback to concurrent
  mutation.** The existing shielded wait and task tracker retain ownership;
  a started ordering-sensitive task completes its shielded fallback before
  reporting an unconfirmed result, while pre-start cancellation transfers
  release to the tracker. Only one path starts fallback release.

## Migration Plan

No data or configuration migration is required. Rollback restores the prior
ordering behavior without changing stored schema.

## Open Questions

None.
