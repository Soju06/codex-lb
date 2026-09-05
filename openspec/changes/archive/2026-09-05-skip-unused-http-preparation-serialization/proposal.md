## Why

The Python HTTP preparation path serializes the complete request for consumers that are inactive even after HTTP is selected. A controlled exact-body ablation saved about3 ms at1.06MB and27 ms at8.55MB; these are preparation costs, not an explanation of minute-scale waits.

## What Changes

- Build complete serialized payloads only when the selected transport, payload budget, tracing or native consumer actually needs them.
- Preserve exact upstream body and existing enabled consumers, including HTTP-to-WS decisions.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `outbound-http-clients`: Consumer-driven full-body preparation for Responses HTTP streaming.

## Impact

`app/core/clients/proxy.py` and existing core/route payload tests. No new serializer/dependency, native IPC change, transport policy or trace format.

Partial investigation follow-up for issue #2029; this scope does not independently close the broad performance issue.
