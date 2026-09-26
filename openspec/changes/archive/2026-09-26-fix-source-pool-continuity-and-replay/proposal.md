## Why

Review reproduced cross-credential continuation failures, a race between delivered completion and ownership logging, and unsafe replay after a failed redirect. Multi-source Responses must preserve upstream-owned state before clients can reuse it.

## What Changes

- Persist scoped, hashed response/conversation/item/encrypted-content ownership before delivery, independently of usage logs.
- Resolve stateful inputs to their recorded source; balance and fail over only portable requests. Reject unresolved multi-source ownership.
- Disable automatic redirects for Responses forwarding and return a non-retryable upstream error.
- Add route-level continuation, cancellation, storage-failure and redirect regressions.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `model-source-routing`: Durable direct-source ownership and conservative replay eligibility.

## Impact

Responses routing and stream dispatch, a small ownership table and migration, retention cleanup, and tests. No new configuration or client setup is required. Existing request logs remain a fallback for historical response IDs. Subscription overflow pins and other protocol routes keep their existing policies.
