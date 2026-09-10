## Why

Native Codex receives account-specific `codex.rate_limits` events even when
codex-lb supplies pooled quota headers or hides upstream quotas. The CLI then
warns that the selected account's weekly limit is nearly exhausted while the
pool still has capacity. The privacy setting currently covers headers but not
these stream events.

## What Changes

- Project default Codex rate-limit events from the same aggregate snapshot as
  downstream quota headers on HTTP SSE and WebSocket Responses transports.
- Suppress account-specific quota events when upstream quotas are hidden, no
  aggregate is known, or the event belongs to an unaggregated limit family.
- Preserve original upstream usage ingestion and ordinary response events.
- Reuse the existing quota cache and visibility setting; add no configuration.

## Impact

- Affected specs: `responses-api-compat`, `api-keys`.
- Affected code: downstream Responses SSE normalization and WebSocket relay.
- API-key self-usage and its configured limits keep their existing semantics.
