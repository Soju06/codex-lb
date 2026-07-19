## Why

HTTP bridge sessions whose owner request has been accepted upstream but never
receives a single response event (no `response.created`, no telemetry, no
downstream-visible output) currently have no account failover of their own.
The owner simply emits keepalives until `stream_idle_timeout_seconds` (default
7200s) elapses and then terminates with a terminal error. Meanwhile, any other
request waiting on that same session's `response_create_gate` already times
out after the much shorter `http_responses_session_bridge_stuck_gate_retire_after_seconds`
(default 300s), retires the session, and — only for a narrow set of
definitively-unsubmitted hard-affinity waiters — transparently resubmits on a
fresh bridge. A waiter that has already been replayed once by its client (for
example because a prior turn's connection briefly dropped) is excluded from
that replacement today even though its replay count says nothing about
whether *this* bridge session made any upstream progress.

The practical effect reported by operators: a single hung upstream connection
to one account can keep a client-visible turn stalled for up to five minutes
before anything happens, and if the retrying waiter happens to have a replay
marker, the client sees a hard failure instead of a transparent retry — with
no attempt to move off the account that appears to be hanging.

## What Changes

- Give the HTTP bridge owner request itself a failover path: when the
  session's own keepalive loop detects that its pending owner request has
  produced zero response events and no downstream-visible output for at least
  the configured stuck-gate retirement threshold, and the request has no
  previous-response account pin, the proxy retires the session, excludes that
  account for this attempt, selects a fresh eligible account, and resubmits
  the same request on a new bridge before yielding anything to the client —
  mirroring the existing `_RetryableStreamError(exclude_account=True)`
  behavior already used for `stream_idle_timeout` on the raw (non-bridge)
  streaming path.
- Broaden the existing waiter-side replacement predicate
  (`_http_bridge_can_replace_retired_gate_session`) to no longer disqualify a
  waiter solely because its client-visible replay counter is non-zero. Replay
  count reflects client-side reconnect attempts, not upstream progress on the
  current bridge; a waiter with `replay_count > 0` that is otherwise
  definitively unsubmitted (no response id, no response events, no downstream
  sequence number, not downstream-visible) is exactly as safe to move as one
  with `replay_count == 0`.
- Preserve every existing safeguard unchanged: previous-response-owner pins
  are never moved to a different account, ambiguously-submitted requests
  (any response id, response event, downstream sequence number, or visible
  output) are never retried transparently, and a request that already showed
  the client any bytes is never silently resubmitted.
- A retried-out account is penalized through the existing error-recording
  path so it does not get silently reselected on the very next attempt.

## Impact

- Affected capability: `proxy-admission-control`.
- A single hung upstream connection to one account self-heals within the
  configured stuck-gate retirement threshold (default 300s) instead of
  stalling the client-visible turn for up to two hours.
- Clients whose prior connection dropped and reconnected (`replay_count > 0`)
  now benefit from the same transparent-replacement path as first-attempt
  waiters, provided the current bridge attempt is still definitively
  unsubmitted.
- No behavior changes for continuity (previous-response-owner) turns, which
  remain pinned to their required account.
- No behavior changes for any request that has already produced a response
  id, response event, downstream sequence number, or visible output.
