## Why

A native HTTP Responses stream can finish while its tracked API-key settlement remains pending. Global request and bridge counters correctly reach zero, but operators lack a separate observation of outstanding request persistence during reversible drain.

## What Changes

- Add pending, drained and unknown request-persistence state plus a known count to the loopback drain response.
- Use canonical registered persistence ownership, retaining completed tasks through their callback handoff.
- Preserve detached settlement, bridge counters and admission behavior; cover the real HTTP settlement path and fallback/unknown cases.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-runtime-observability`: distinguish request-persistence ownership from request scopes and bridge activity during drain.

## Impact

Proxy persistence classification and internal health/drain response only. No settings, schema migration, dependencies, deployment-controller changes or live instrumentation. The new observation does not retrofit the running predecessor or certify historical write success.
