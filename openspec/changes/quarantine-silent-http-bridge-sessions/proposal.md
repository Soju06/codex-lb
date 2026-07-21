## Why

An upstream Responses websocket can accept `response.create` without ever
emitting `response.created` or a terminal event. The HTTP bridge then keeps the
request pending and holds the session response-create gate, so later requests
can appear idle or fail admission even when other accounts have capacity.

Replaying the same request automatically is unsafe because the proxy cannot
know whether upstream accepted it. Recovery must retire the silent bridge
without duplicating work, then let a new client retry use a transport that does
not depend on that bridge.

## What Changes

- Bound the wait from sending `response.create` to receiving
  `response.created`.
- Retire and temporarily quarantine a bridge session that exceeds that
  deadline, failing its current request without internally replaying it.
- Route the next independent client retry for the quarantined affinity key over
  direct HTTP while preserving any `previous_response_id` account owner.
- Ensure closing a downstream streaming response also closes the bridge
  lifecycle iterator and its upstream websocket, including when only the
  initial heartbeat was consumed.
- Keep the timeout and quarantine duration as internal, zero-config settings
  with conservative defaults.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `responses-api-compat`: defines safe recovery from an accepted-but-silent
  bridge submission and downstream stream cancellation.

## Impact

- HTTP Responses session bridge lifecycle and transport selection.
- Codex-native and OpenAI-compatible Responses streaming routes.
- No database migration, dashboard change, required setup step, or public API
  schema change.
