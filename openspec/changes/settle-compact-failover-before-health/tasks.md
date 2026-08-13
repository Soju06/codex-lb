## 1. Implementation

- [x] 1.1 Classify compact failover without writing health, and defer the
  health write when a reservation is still held.
- [x] 1.2 Flush deferred health only after `_settle_compact_api_key_usage`.

## 2. Regression coverage

- [x] 2.1 Assert compact `failover_next` with a held reservation settles
  before `_handle_stream_error`.
- [x] 2.2 Assert exhausted HTTP 500 retries defer `_handle_proxy_error`
  until settlement.
- [x] 2.3 Assert `UpstreamProxyRouteError` after failover still flushes
  deferred health.
- [x] 2.4 Assert freshness/connect and post-401 refresh failovers defer
  health until settlement.

## 3. Validation

- [x] 3.1 Run the new compact order regression and the existing compact
  timeout settle-before-health test.
- [x] 3.2 Run strict OpenSpec validation for this change.
