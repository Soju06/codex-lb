# Previous-response replay owner fencing

## Purpose

This change distinguishes continuation-anchor recovery from payload
portability. A retry may safely remove a stale anchor yet still be forbidden
from changing accounts because retained request items remain account-scoped.

## Example

Account A first receives:

```json
{
  "previous_response_id": "resp_owner",
  "input": [
    {
      "type": "reasoning",
      "id": "rs_owner",
      "encrypted_content": "owner-bound-ciphertext"
    }
  ]
}
```

If a pre-visible failure triggers stale-anchor recovery, the proxy may remove
`previous_response_id` only as part of a verified replay. Because the retained
encrypted reasoning is not account-neutral, the replacement remains bound to
account A. Account B must never receive it.

An ordinary fresh request containing only portable user input can pass the
canonical predicate and may use normal account selection.

A selected account is not recorded as owner when transport evidence proves the
request failed before dispatch. The body may then make its first real dispatch
on another eligible account. Ambiguous failures remain pinned.

HTTP bridge operation IDs are proxy-owned but still identify an in-flight
operation. A bridge retry carrying an existing operation ID remains on its
current account unless the operation is explicitly rebound before selection.

## Operational Notes

- Owner-unavailable failures are internal retry decisions; they do not add a
  setting or require operator action.
- Existing file ownership remains an independent strict pin.
- Verified fresh-body installation clears the old dispatch owner atomically
  with replacing the request body.
- A bound request may perform one forced authentication refresh on the same
  owner; it does not become eligible for cross-account auth failover.
- API-key reservation settlement still completes before deferred account-health
  writes.
- The change covers HTTP streaming, HTTP bridge, and direct WebSocket paths so
  operators do not observe transport-dependent account ownership.
