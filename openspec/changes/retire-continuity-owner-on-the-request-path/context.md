# Context

## Purpose and scope

Close the latency gap left by the scheduled retirement sweep: a thread whose owner just became
unroutable should be served now, not in six hours. In scope: one extra question asked at the
connect failure, the CAS that answers it, and the in-request rebind. Out of scope: owners pinned
without a durable row, and bodies that cannot leave their account.

## Decisions

**Why the request's own budget is the deadline.** The plan's rule was "wait if there is evidence
the owner returns in time, otherwise release", and the request budget is the only deadline that
is already agreed by both sides: the client is waiting that long anyway, and
`_http_bridge_request_budget_seconds` is an existing dashboard-managed value. Inventing a second
window would mean another setting to tune and another thing to get wrong.

**Why `reset_at` is the evidence.** It is the horizon the upstream itself gave us for a rate or
quota limit, already stored and already used by the sweep's predicate. `paused`,
`reauth_required` and `deactivated` have no horizon, which is exactly why they are the statuses
that produced permanent 502s in production: nothing about them says "later".

**Why a separate scope value.** `request_path` versus the sweep's global timestamp form costs
nothing and makes the two writers distinguishable in the row itself, which matters when reading
a production table to ask "did the sweep free this, or did a request?". Both read as retired
through the same predicate.

**Why the lock, not just the CAS.** Copied deliberately from
`sticky_repository.abandon_legacy_session_header_owner_if_unavailable`, including its comment:
PostgreSQL evaluates the status subquery from the UPDATE's snapshot, so without locking the
account row first, a concurrent recovery can commit while the statement waits on the session row
and the stale snapshot still authorizes a retirement. The in-UPDATE status predicate is the
second, database-level invariant that keeps a future refactor from turning a prior observation
into an unconditional write.

**Why only a proxy-injected anchor.** When the proxy injected the anchor, the client's own body
is still the full turn, so re-sending it without the anchor is exactly what the client asked for.
When the client supplied the anchor, it named upstream state on that account; dropping it would
silently change the request. Every production trace of this bug carries
`previous_response_id=None`, so the covered shape is the one that actually occurs.

## Constraints

- No schema change: uses the two columns the previous change added.
- No new setting: the deadline is the request's existing budget.
- `request_deadline` is monotonic and `accounts.reset_at` is a wall-clock epoch, so the
  projection is explicit rather than a direct comparison.
- No ceiling-guarded proxy file grows; the helper lives in the uncapped
  `_service/http_bridge/streaming.py` beside the other connect-failure closures.

## Failure modes

- **Retiring an owner that just recovered.** The lock plus the in-UPDATE status predicate make
  the write lose that race; `test_request_path_never_retires_a_healthy_owner` pins it.
- **Retiring after a concurrent rebind.** The CAS requires the row to still name the observed
  owner, so a rebind wins; `test_request_path_retirement_requires_the_expected_owner` pins it.
- **Retrying forever.** The attempt is one-shot per request
  (`owner_retirement_attempted`), so a failure after retirement falls through to the existing
  handling rather than looping.
- **A leaked marker.** The request path writes the scope marker alone, which satisfies neither
  sweep phase: phase 1 wanted both columns NULL, phase 2 wants a timestamp. In the normal case
  the same request re-claims the row and clears it, but a rebind that never lands would have left
  the row in the table forever. Phase 1 now promotes a scope-only marker to its own form once the
  row is stale — the same promotion the sticky sweep performs — and does so without requiring the
  owner to still be unavailable, since an owner that recovered without the thread rebinding would
  otherwise make the marker permanent.
- **A test that proves nothing.** The end-to-end test for the previous change ran over a soft
  prompt-cache key, which falls back to a healthy account by itself — it would have passed
  without the fix. Both end-to-end tests now use a hard `thread_header` key, verified by removing
  the `continuity_abandoned` exemption and watching the sweep test fail.

## Example

An operator pauses an account while a thread is mid-conversation. Before:

```text
Proxy preferred account unavailable error_code=continuity_owner_unavailable
proxy_error_response status=502 code="previous_response_owner_unavailable"
# ... repeated on every resume for up to six hours
```

After:

```text
owner_unavailable_replay_rejected  detail=reason=payload_not_full_resend  key_strength=hard
owner_retired_on_request           detail=outcome=rebind_without_anchor
http_bridge_event event=create     account_id=<healthy>
"POST /backend-api/codex/responses HTTP/1.1" 200 OK
```

The thread loses its upstream conversation state and re-sends its history once. The first line
is the attribution added by the tracing change, which is what showed that this shape — a
proxy-injected anchor on a hard thread key — is the one worth handling.
