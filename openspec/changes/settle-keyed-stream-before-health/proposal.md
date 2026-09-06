## Why

Keyed HTTP Responses streams can still write account health while their API-key
reservation remains open when an upstream failure is rewritten as
`previous_response_owner_unavailable` or when a terminal bridge queue drains
empty. Settlement must be confirmed before health so quota state and account
recovery state cannot become observably inconsistent.

## What Changes

- Settle keyed stream usage before account-health writes on all three
  owner-unavailable rewrite paths.
- Finalize terminal keyed settlement before empty-queue health or success
  writes.
- Withhold health when neither ordered settlement nor fail-safe release confirms
  cleanup.
- Preserve the public/logged owner-unavailable envelope while health uses the
  original upstream recovery code.
- Preserve stale-anchor shape matching and source-ownership routing behavior.

## Capabilities

### New Capabilities

### Modified Capabilities

- `api-keys`: require confirmed stream reservation settlement before rewrite and
  empty-queue terminal health writes.
- `model-source-routing`: preserve owner-unavailable routing and original-code
  health semantics while adding settlement ordering.
- `responses-api-compat`: preserve the client and request-log error envelope and
  stale-anchor recovery shape across the ordered terminal paths.

## Impact

The change is limited to HTTP SSE Responses streaming settlement order in the
existing stream settlement state, `streaming/mixin.py`, and
`streaming/retry.py`, focused helper and
`/v1/responses` regressions, and the three supporting OpenSpec deltas. It does
not change usage-missing policy, unary routes, WebSocket/compact behavior, or
client-visible error shapes.
