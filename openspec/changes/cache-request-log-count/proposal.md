## Why

Every request-log page load and pagination click runs an exact `COUNT(*)` over the filtered set; with default filters that is effectively the whole table on every 30-second dashboard poll. The count only feeds the display-only "X–Y of N" total and last-page jump, which tolerate short staleness.

## What Changes

- Cache the listing total per filter signature (all filter params except limit/offset) with a configurable TTL: `CODEX_LB_REQUEST_LOG_COUNT_CACHE_TTL_SECONDS`, default 30 s, `0` disables (the test suite disables it so totals stay exact within a test). Bounded LRU-ish eviction at 256 entries; per-instance.
- API contract unchanged: `total` and `has_more` keep their exact semantics up to ≤TTL staleness of a monotonically-growing display figure; new rows always appear on page 1 (newest-first ordering) regardless.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `query-caching`: the request-log listing total MUST be served from a short-TTL per-filter cache instead of re-counting the filtered set on every page request.

## Impact

`app/modules/request_logs/repository.py`, `app/core/config/settings.py`, `tests/conftest.py` (suite disables the TTL). No schema/API change.
