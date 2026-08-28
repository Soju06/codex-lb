# query-caching Delta

## ADDED Requirements

### Requirement: Request-log listing totals are cached and rollup-served
The request-log listing total MUST be served from a short-TTL per-filter
cache. On a cache miss, filter signatures whose every active filter maps
onto a demand-rollup dimension (time bounds, accounts, api keys,
model/effort pairs, statuses, soft-delete exclusion) MUST be counted as the
demand rollup's folded `SUM(request_count)` under the hourly watermark plus
an exact raw count over the un-folded complement windows; the result MUST
equal the legacy raw `COUNT(*)`. Signatures carrying free-text search or
error-code splits MUST fall back to the exact raw count.

#### Scenario: Default listing total avoids a full history scan once folded
- **GIVEN** request logs folded below the hourly watermark and a live raw tail
- **WHEN** the listing total is computed with default filters
- **THEN** the folded portion MUST be one aggregated read over the demand rollup bounded by the watermark
- **AND** only the un-folded complement windows are counted from raw
- **AND** the total MUST equal the raw `COUNT(*)` over the same filters

#### Scenario: Status splits stay exact through the rollup
- **GIVEN** history containing success, error, and cancelled requests on both sides of the watermark
- **WHEN** the listing total is computed for the default success+error split, a single status, or no status filter
- **THEN** the rollup-served total MUST equal the raw count for the same split

#### Scenario: Non-expressible filters fall back to the raw count
- **GIVEN** a listing filtered by free-text search or an error-code split
- **WHEN** the total is computed
- **THEN** the exact raw `COUNT(*)` path MUST be used

#### Scenario: Retention pruning keeps totals aligned with listable rows
- **GIVEN** retention has pruned folded raw rows while their demand-rollup counts remain
- **WHEN** the listing total is computed for an expressible signature
- **THEN** the rollup window MUST be clamped to the earliest surviving live row
- **AND** the total MUST equal the raw count over the surviving rows, never advertising pages the listing cannot return

#### Scenario: Offset-aware time bounds are accepted
- **GIVEN** the dashboard sends ISO-8601 `Z` (offset-aware) `since`/`until` bounds
- **WHEN** the listing total is computed
- **THEN** the bounds MUST be normalized to the naive-UTC domain before window arithmetic
- **AND** the result MUST equal the naive-UTC equivalent request

#### Scenario: No watermark degrades to the legacy count
- **GIVEN** no hourly fold watermark exists (pre-backfill or after the operator escape hatch)
- **WHEN** the listing total is computed for an expressible signature
- **THEN** the folded sum MUST be empty and the raw windows MUST cover the full range
- **AND** the result MUST be the exact legacy count with no kill switch involved
