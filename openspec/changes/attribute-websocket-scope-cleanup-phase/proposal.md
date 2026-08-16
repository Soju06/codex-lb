## Why

When WebSocket scope cleanup exceeds its cleanup budget, the warning reports the
timeout and total background cleanup task count but not the operation that is
still blocked. Operators cannot distinguish an upstream-close stall from
reader observation, request finalization, or lease release without reproducing
the incident under instrumentation.

## What Changes

- Track the current WebSocket scope cleanup phase locally while the existing
  finalization sequence runs.
- Add that fixed, low-cardinality phase to the existing timeout warning.
- Keep cleanup ordering, timeout budgets, retries, and ownership unchanged.
- Do not log request ids, account ids, payloads, credentials, or exception
  content in the phase field.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `proxy-runtime-observability`: WebSocket scope cleanup timeout warnings MUST
  identify the blocked cleanup phase with a fixed low-cardinality value.

## Impact

`app/modules/proxy/_service/websocket/mixin.py` and its route-level WebSocket
cleanup regression coverage. No API, schema, setting, timeout, or dashboard
change.
