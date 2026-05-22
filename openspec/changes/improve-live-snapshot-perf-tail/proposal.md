# improve-live-snapshot-perf-tail

## Why

Operators use `scripts/codex_lb_live_snapshot.py` to separate local proxy health from slow request tails. The snapshot already reports request-log percentiles, but the tail view needs to make long generations, websocket instability, local query cost, and malformed live-query failures easier to distinguish in one read-only report.

## What Changes

- Add a perf-tail section grouped by model, coarse route pattern, output-size bucket, reasoning effort, status, and websocket failure class.
- Include average, p95, and max latency for perf-tail groups.
- Include long-generation subgroups and a separate websocket-instability summary with account ids hashed.
- Include request-log query timing metadata so slow snapshot reads are visible separately from slow proxied requests.
- Keep malformed container PostgreSQL JSON output as an explicit snapshot error instead of raising or emitting partial data.

## Impact

- Improves operator diagnostics without changing proxy serving behavior, admission, request routing, or tier selection.
- Keeps the live snapshot read-only and safe to run against the active container or a copied SQLite database.
