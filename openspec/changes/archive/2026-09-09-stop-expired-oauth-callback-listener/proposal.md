## Why

Abandoned browser OAuth flows remain in the local store after their TTL because pruning only runs on later requests. The process-local callback listener therefore remains bound indefinitely.

## What Changes

- Add one store-owned expiry task that wakes at the next pending browser-flow deadline and invokes existing idle-listener cleanup.
- Re-arm deadline tracking when a pending browser flow is hydrated from durable state.
- Drain deadline work during store reset.
- Preserve existing callback, persistence, device-code, and Docker behavior.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `replica-operations`: Release the local callback listener after the last pending browser flow expires without a follow-up request.

## Impact

Only OAuth listener expiry scheduling and its regression coverage change. Docker host-port publication is a separate deployment decision; this listener fix does not resolve the Docker collision in #2076 by itself.
