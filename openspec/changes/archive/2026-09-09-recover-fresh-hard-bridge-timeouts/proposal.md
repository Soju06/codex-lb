## Why

Fresh HTTP Responses bridge requests can remain pinned to a hard session-header
owner even after repeated pre-response retries prove that the selected upstream
socket is not acknowledging `response.create`. The retry circuit prevents a
retry storm, but the eventless watchdog eventually returns a stream error after
the 240-second safety cap. A self-contained request with no previous-response,
turn-state, or account-scoped file ownership can safely move to another active
account without breaking continuity.

## What Changes

- Permit pre-response recovery to exclude the failing account for a fresh,
  self-contained hard session-header request.
- Permit a proof-gated client full resend to replay once on its required
  continuity owner, including when that owner's retry circuit is cooling down.
- Keep previous-response, turn-state, file-pinned, and proxy-injected anchored
  requests on their required account.
- Preserve the existing retry circuit and eventless watchdog as bounded
  fallbacks when no alternate account is eligible.
- Add regression coverage proving fresh hard requests can switch accounts while
  anchored requests remain owner-bound.

## Impact

- Affected capabilities: `proxy-admission-control`, `responses-api-compat`.
- Fresh requests recover without waiting for the client-safe eventless timeout
  when another account is available.
- Continuity-sensitive requests retain their existing fail-closed behavior.
