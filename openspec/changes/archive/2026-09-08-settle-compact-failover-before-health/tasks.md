## 1. Implementation

- [x] 1.1 Classify compact failover without writing health, and defer the
  health write when a reservation is still held.
- [x] 1.2 Flush deferred health only after `_settle_compact_api_key_usage`.
- [x] 1.3 Flush deferred health when finalize fails but fail-safe release
  succeeds, then surface `usage_settlement_failed`.
- [x] 1.4 Keep a finalized compact success when deferred health persistence
  fails.
- [x] 1.5 Settle and flush deferred health on cancellation and other
  non-proxy exits.
- [x] 1.6 Shield deferred health flush so cancellation during the write
  still completes the penalty.
- [x] 1.7 Continue remaining deferred health writes if one write fails.
- [x] 1.8 Settle and flush deferred health when a later account selection
  times out.

## 2. Regression coverage

- [x] 2.1 Assert compact `failover_next` with a held reservation settles
  before `_handle_stream_error`.
- [x] 2.2 Assert exhausted HTTP 500 retries defer `_handle_proxy_error`
  until settlement.
- [x] 2.3 Assert `UpstreamProxyRouteError` after failover still flushes
  deferred health.
- [x] 2.4 Assert freshness/connect and post-401 refresh failovers defer
  health until settlement.
- [x] 2.5 Assert a second 401 after forced refresh defers `_handle_proxy_error`.
- [x] 2.6 Assert permanent post-401 refresh settles before
  `mark_permanent_failure`.
- [x] 2.7 Assert fallback-release success still flushes deferred health
  before `usage_settlement_failed`.
- [x] 2.8 Assert fallback-release failure keeps deferred health unapplied.
- [x] 2.9 Assert a deferred health-persistence failure does not replace a
  finalized compact success.
- [x] 2.10 Assert cancellation or another non-proxy exit still settles and
  flushes deferred health.
- [x] 2.11 Assert cancellation during deferred health flush still completes
  the write.
- [x] 2.12 Assert a later deferred health write still runs after an earlier
  write fails.
- [x] 2.13 Assert a post-failover account-selection timeout still flushes
  deferred health.

## 3. Validation

- [x] 3.1 Run the new compact order regression and the existing compact
  timeout settle-before-health test.
- [x] 3.2 Run strict OpenSpec validation for this change.
