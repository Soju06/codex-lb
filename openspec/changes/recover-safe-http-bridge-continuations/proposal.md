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
- Record proof-gated fresh-resend attempts in a durable recovery journal so a
  later replica can replay only an unresolved, transport-ambiguous attempt.
- Keep ordinary requests and existing session ownership on their current
  recovery paths, and emit the continuity-fail-closed diagnostic for
  observability.

## Impact

- HTTP bridge continuation recovery and idle-timeout behavior.
- Adds a durable recovery-attempt journal migration and startup schema check;
  no public API or account-status changes.
