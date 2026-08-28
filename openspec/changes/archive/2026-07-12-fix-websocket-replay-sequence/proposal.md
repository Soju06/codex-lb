## Why

A direct Responses WebSocket replay can reuse the original downstream response id after an upstream reconnect while forwarding the new upstream response's sequence numbers from their reset starting point. Clients that enforce monotonic `sequence_number` ordering then fail the turn to prevent stale or interleaved response frames.

## What Changes

- Preserve the client-visible Responses sequence contract across transparent direct-WebSocket replay.
- Prevent replay frames from presenting a lower `sequence_number` under an already-active downstream response id.
- Fail safely rather than mixing response generations when a transparent replay cannot preserve the downstream stream contract.
- Add direct WebSocket regression coverage for upstream close after a sequenced `response.created` frame and for ordinary pre-created replay.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: Define sequence-order safety for direct WebSocket replay after upstream reconnect.

## Impact

- Affected code: direct Responses WebSocket request state, upstream event forwarding, and transparent replay eligibility.
- Affected tests: direct `/backend-api/codex/responses` WebSocket replay integration tests.
- Compatibility: clients no longer receive a regressed sequence under the same response id; unsafe replay attempts surface through the existing retryable stream-failure path.
