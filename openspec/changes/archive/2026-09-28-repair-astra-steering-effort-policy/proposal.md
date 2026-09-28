## Why

Steering currently authorizes a synthesized medium effort when the original
request omitted effort, and the wire-normalized max value when the client
requested ultra. Both can reject a steer after its parent create was admitted.

## What Changes

- Preserve the effective pre-wire reasoning effort on Astra request state and
  do not invent effort on a continuation.
- Bound queued steering by bytes without a second undocumented count cap.
- Clarify the existing fail-before-dispatch aiohttp instrumentation contract
  and avoid parsing retained configuration when it is already present.

## Impact

Responses WebSocket steering policy, retained Astra configuration and its
route/transport tests; no changes to HTTP request policy.
