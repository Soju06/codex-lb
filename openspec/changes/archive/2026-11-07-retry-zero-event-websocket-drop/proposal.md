## Why

Live observations on 2026-11-07 04:00–06:30 UTC after deploying #2075 report stream_incomplete on 12–15% of requests. Of 1049 bridge drops, 434 had zero response events and 341 had positive counts. Reported classifications included 312 clean closes (1000), 363 transport errors, and 100 transient closes. Many followed pooled socket reuse.

## What Changes

Retry a terminal upstream socket loss once through the existing bridge reconnect path, only before any response event or buffered output. Prefer the original account and retain continuity, cancellation and durable operation guards. Do not deploy or change dependencies.

## Impact

Responses HTTP bridge reader and reconnect admission; regression tests and responses-api-compat contract.
