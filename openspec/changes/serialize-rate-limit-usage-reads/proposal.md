## Why

Both rate-limit header construction and the Codex usage payload start primary
and secondary usage queries concurrently through one `ProxyRepositories`
bundle. That bundle owns a single SQLAlchemy `AsyncSession`, so PostgreSQL can
raise asyncpg concurrent-operation errors even though SQLite's separate
threaded read path masks the defect in routine tests.

## What Changes

- Await primary, secondary, and subsequent usage-window reads sequentially in
  `_RateLimitMixin._compute_rate_limit_headers()`.
- Apply the same session-safe ordering in
  `_RateLimitMixin.get_rate_limit_payload()`.
- Add deterministic regression coverage whose repository double rejects
  overlapping operations, plus PostgreSQL-facing coverage of the public
  rate-limit surfaces.
- Add a companion query-caching session-ownership requirement for aggregated
  rate-limit reads and repair the stale selection-input test that currently
  claims the already-serialized load-balancer reads run in parallel.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `query-caching`: Add shared-`AsyncSession` serialization requirements for
  rate-limit header and payload usage reads.

## Impact

- Affected code: `app/modules/proxy/_service/rate_limit.py`.
- Affected tests: focused proxy rate-limit unit tests and a PostgreSQL
  integration path for the rate-limit headers and usage payload.
- Response fields, header names and values, caching behavior, query count, and
  public API schemas remain unchanged; only query scheduling changes.
- No dependency, configuration, database-schema, or migration changes.
