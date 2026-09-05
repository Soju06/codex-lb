## Why

Astra WebSocket clients can send `response.steer` on an owned in-flight
response. The proxy currently treats unknown event types as invalid
payloads. Split from #2089: this PR is only steering plus reservation
extend/reduce.

rust-v0.153.4 Codex and `openai/codex` do not emit `response.steer`.
This is protocol-forward.

## What Changes

- Accept valid `response.steer` on an owned Astra Responses WebSocket.
- Forward steering on the same upstream connection/account.
- Queue additional steers onto one successor reservation via extend;
  failed submissions reduce only their unapplied increment (`FOR UPDATE`
  on that path, lock order reservation → limits).
- Do not pop/release a steering placeholder until the explicit
  continuation `response.create` prepares successfully.
- Out of scope: configuration_update/Ultra (#2097), async tools (#2099),
  catalog (#2085), global input fingerprint rewrite, FOR UPDATE on
  finalize/release.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: steering continuations retain owned WebSocket
  lifecycles.
- `api-keys`: extend/reduce a reserved usage reservation for queued
  steering input.

## Impact

- Code: new websocket steering module, WS mixin/helpers, API-key
  reservation extend/reduce.
- Tests: unit steering dispatch/review/error sanitization/retention plus
  protocol harness scenarios.
- No settings, schema, dashboard, or README changes.
