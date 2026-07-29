## Why

An HTTP bridge request can lose its upstream acknowledgement after the
`response.create` send. Retrying a continuation with the same
`previous_response_id` can fork work or duplicate side effects, while waiting
through a retry-circuit cooldown only consumes the client request budget.

## What Changes

- Allow one fresh-upstream replay only when the request state contains a
  proof-gated, unanchored full-resend payload. The proof applies equally to
  client-provided and proxy-injected anchors.
- Fail continuity-bound requests closed when a retry-circuit cooldown is
  active and no safe fresh replay exists.
- Keep ordinary requests and existing session ownership on their current
  recovery paths, and emit the continuity-fail-closed diagnostic for
  observability.

## Impact

- HTTP bridge continuation recovery and idle-timeout behavior.
- No API, database, account-status, or session-owner changes.
