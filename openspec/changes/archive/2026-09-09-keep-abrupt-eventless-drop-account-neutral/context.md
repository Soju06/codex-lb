A websocket that dies without a close frame before any application-layer
response event is the weakest possible evidence of account ill-health. The
reader failure path nevertheless penalized it because
`close_classification` was computed only for `close_code is not None`, so
the frame-less case fell through to the default `penalize_account=True`.
Meanwhile the strictly stronger signal — a graceful 1000 close with zero
events — was already exempted, and #1718 established the same precedent for
stream idle timeouts.

The fix adds `_is_account_neutral_transport_drop` beside
`_classify_upstream_close` (no close frame AND zero response events) and
consults it in the reader failure path. To avoid masking a genuine account
ban that manifests as repeated drops, the neutral drop is recorded into the
existing `_record_http_bridge_account_timeout_signal` accumulator: three
eventless failures within the 300-second window still apply the minimum
drain penalty. No new settings are introduced.

Incident shape from #1754: three drops ~12 minutes apart never meet the
300-second window, so the owner stays routable and continuity-bound
follow-ups keep working; before the fix they crossed
`ERROR_BACKOFF_THRESHOLD` and produced eight
`previous_response_owner_unavailable` 502s in eight seconds while the other
pool account idled.
