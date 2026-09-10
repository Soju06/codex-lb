## ADDED Requirements

### Requirement: Weekly credit pace trailing demand is memoized per process

The dashboard MUST NOT re-run the trailing positive used-percent delta aggregate over `usage_history` on every poll; `GET /api/dashboard/overview` and `GET /api/dashboard/projections` MUST reuse the result for the same trailing window span and `account -> window` signature from a process-local cache within a fixed 60-second TTL (an application constant, not an operator tunable). The window span (`until - since`) is part of the cache identity; the bounds themselves are not, because both callers anchor the window at the current time, so within the TTL the window drifts by at most the TTL. The cached figure is display-only, gates no security, authorization, or routing decision, and its documented maximum cross-replica staleness is the 60-second TTL; it therefore does not register a cache-invalidation namespace. The cache MUST be bounded and MUST return a copy so callers cannot alter the cached value.

#### Scenario: Overview and projections polls share one aggregate within the TTL

- **GIVEN** the latest usage rows yield the same `account -> window` signature for two dashboard requests within 60 seconds
- **WHEN** `/api/dashboard/overview` and then `/api/dashboard/projections` are served
- **THEN** the `weekly_demand_samples` aggregate is executed once and both responses carry the same trailing-demand figure

#### Scenario: A different account signature is aggregated on its own

- **GIVEN** a cached trailing-demand result for one `account -> window` signature
- **WHEN** a request resolves a different signature (an account added or removed, or an account's weekly window remapped)
- **THEN** its trailing demand comes from its own aggregate, not from another signature's cache entry

#### Scenario: A different window span is aggregated on its own

- **GIVEN** a cached trailing-demand result for a seven-day span and one `account -> window` signature
- **WHEN** a caller requests the same signature over a different trailing span
- **THEN** its trailing demand comes from its own aggregate, not from the seven-day entry

#### Scenario: Expired entries are recomputed

- **GIVEN** a cached trailing-demand result whose 60-second TTL has elapsed
- **WHEN** a dashboard request with the same signature arrives
- **THEN** the aggregate is executed again and the cache entry is refreshed
