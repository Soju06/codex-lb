## Why

When a hard-affinity request has no eligible owner, repeated recovery selection can run until the request budget is cancelled. The route then surfaces `upstream_request_timeout` instead of the original deterministic `hard_affinity_saturated` contract, making a local ownership failure look like an upstream timeout.

## What Changes

- Classify a resolved hard owner outside authenticated policy scope as non-recoverable within the request, preserving `hard_affinity_saturated` without entering deadline-sensitive capacity waits.
- Ensure the streaming, HTTP bridge, and WebSocket selection paths release leases and emit their established error envelopes exactly once.
- Add deterministic virtual-clock and externally visible route regressions for API-key-scoped hard-affinity requests.
- Keep genuine budget exhaustion during upstream/network work classified as `upstream_request_timeout`.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: make hard-affinity selection deadline handling deterministic while preserving existing ownership and timeout contracts.

## Impact

Proxy selection/retry lifecycle code, error-envelope mapping, timing-seam tests, and Responses integration tests. No database schema, transport default, deployment topology, or public configuration change is expected.
