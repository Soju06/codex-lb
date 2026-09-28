## Why

During a blue-green drain, an HTTP bridge owner can reject an owner-forwarded
request with `bridge_drain_active` before accepting it upstream. The origin
already has enough context to retry locally for session/thread bootstrap
requests, but current-main treats turn-state anchored drain rejection like any
other hard anchor and refuses the local rebind.

## What Changes

- Expose whether an owner-forward failure happened before upstream dispatch
  (not dispatched, or rejected by the receiver) so the origin can tell an
  explicit drain rejection from an ambiguous failure.
- Allow local bootstrap rebind for `bridge_drain_active` only when the failure
  is proven pre-dispatch, for session/thread keys and for turn-state keys that
  carry a session or thread header fallback; turn-state-only requests keep the
  owner's retryable 503 regardless of the turn-state text.
- Keep previous-response continuations excluded from this bootstrap path.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `sticky-session-operations`: draining owner-forward rejection can recover
  locally only when the owner did not accept the request upstream.
