# query-caching Delta

## ADDED Requirements

### Requirement: Request-log listing totals are cached per filter signature

The request-log listing MUST NOT execute an exact `COUNT(*)` over the filtered set on every page request; the total MUST be reused from a per-filter-signature cache within a configurable TTL (default 30 s), with `0` disabling the cache entirely. Cached totals are display-only: page contents themselves MUST remain exact and newest-first.

#### Scenario: Repeated pages reuse the cached total

- **GIVEN** two listing requests with the same filters but different offsets within the TTL
- **WHEN** both pages are served
- **THEN** the filtered set is counted once and both responses report the same total

#### Scenario: Distinct filter signatures count independently

- **WHEN** a listing request arrives with different filters
- **THEN** its total comes from its own count, not another signature's cache entry

#### Scenario: TTL zero disables caching

- **WHEN** the TTL setting is `0`
- **THEN** every listing request executes an exact count
