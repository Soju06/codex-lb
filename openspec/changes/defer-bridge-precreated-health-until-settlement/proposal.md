## Why

The HTTP bridge's pre-created retry arms (model-capacity wait, owner-pinned
quota, and the generic retryable arm) wrote load-balancer account health
immediately while the request's API-key reservation was still unsettled, then
suppressed the finalizer's settlement-gated health write. The unordered write
was therefore the only write, a successful retry penalized the account without
any settlement, and an unconfirmed settlement could not leave health unapplied
— violating the settlement-ordering invariant the keyed SSE retry path already
enforces.

## What Changes

- Keyed HTTP-bridge pre-created retry failures queue the classified
  account-health write on the request state instead of applying it
  immediately; unkeyed requests keep the immediate write.
- The queued penalty drains only after the reservation settles or its fallback
  release commits; when neither confirms, it stays unapplied.
- Deferred account-backoff writes and deferred stream-health writes drain on
  independent post-settlement lanes, so a failure in one cannot orphan the
  other.
- A deferred health write that itself fails after a committed settlement is
  logged and dropped without aborting the remaining terminal finalization.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `api-keys`: Extend the stream reservation settlement-ordering requirement to
  the HTTP bridge's pre-created retry handling, including independent
  post-settlement drain lanes and failed-write isolation.

## Impact

- Affected implementation: HTTP-bridge upstream event processing, the
  reservation release/settlement drain helpers, and the websocket finalizer's
  post-settlement drain lanes.
- Affected verification: unit regressions driving the real bridge event
  processor for keyed and unkeyed pre-created failures, settle-then-health
  ordering on release, unconfirmed-release retention, lane independence,
  cancellation, and failed-write isolation.
- No public API, wire contract, database schema, setting, deployment, or
  dashboard change.
