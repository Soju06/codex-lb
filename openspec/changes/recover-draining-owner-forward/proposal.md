## Why

During a blue-green drain, an HTTP bridge owner can reject an owner-forwarded
request with `bridge_drain_active` before accepting it upstream. The origin
already has enough context to retry locally for session/thread bootstrap
requests, but current-main treats turn-state anchored drain rejection like any
other hard anchor and refuses the local rebind.

## What Changes

- Classify explicit `bridge_drain_active` owner-forward errors as receiver
  rejection, not ambiguous dispatch.
- Allow local bootstrap rebind for session/thread headers with a turn-state
  anchor only when the owner-forward failure is proven pre-dispatch.
- Keep previous-response continuations excluded from this bootstrap path.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `sticky-session-operations`: draining owner-forward rejection can recover
  locally only when the owner did not accept the request upstream.
