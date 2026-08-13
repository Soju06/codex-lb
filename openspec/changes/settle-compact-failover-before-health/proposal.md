## Why

Compact `failover_next` writes account health through `_handle_stream_error`
while the API-key reservation is still reserved. The timeout branch already
settles first. An open reservation plus a health penalty double-charges the
request and can backoff an account that the request has not finished using.

## What Changes

- Classify compact failover without writing health.
- Keep the reservation across `failover_next` and defer the health write.
- Flush deferred health only after `_settle_compact_api_key_usage`.
- If finalize fails but fail-safe release succeeds, flush deferred health
  before surfacing `usage_settlement_failed`.
- Surface paths settle, then write health.

## Capabilities

### Modified Capabilities

- `usage-refresh-policy`: compact failover must settle the reservation
  before any account-health write.

## Impact

Streaming SSE reservation paths and compact timeout/exhaustion terminals stay
on their current settle-then-health order. No second reservation is acquired
mid-request.
