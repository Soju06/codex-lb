## Why

An HTTP bridge admission waiter can time out while the exact creator that owns
the shared in-flight marker is still running. Evicting the marker immediately
lets another request start a replacement before the original creator has
finalized, so two creators can race for the same bridge identity.

## What Changes

- Record the current owner task on each in-flight bridge creation marker.
- When a bridge capacity waiter or same-key in-flight waiter reaches the
  configured admission timeout, signal cancellation only to the exact recorded
  owner for the current non-handoff marker.
- Keep the aborted marker capacity-owned until that owner finalizes.
- Wait no longer than one additional configured admission-wait interval for the
  owner to terminate; retry admission only after it does.
- Preserve the existing structured local-overload HTTP 429 when the owner does
  not terminate within that bounded wait.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-admission-control`: HTTP bridge startup timeout recovery waits for the
  aborted owner before retrying admission.
