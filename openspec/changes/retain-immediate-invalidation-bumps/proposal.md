## Why

A committed mutation can exhaust the immediate cache-invalidation write retries and lose its peer notification. Coalesced bumps already stay pending, but direct awaited bumps do not, leaving other replicas on stale policy or routing state until a cache backstop or another mutation.

## What Changes

- Retain failed and interrupted direct bumps in the existing process-local pending set for later poll cycles.
- Move pending-marker consumption into the shared bump primitive so immediate and coalesced publication preserve mutations arriving during an in-flight write.
- Preserve short retry budgets, logs/metrics, callback acknowledgement, cancellation propagation, and successful `bump_local()` behavior.
- Add deterministic race/failure tests and real API-key/account-pause mutation regressions with independent peer caches.
- Leave namespace registrations, upstream-route/settings publication, schemas, migrations, and usage-share policy unchanged. The separate publication cleanup from the old bundled patch is not required for this fix.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `query-caching`: awaited invalidation bumps receive the same retained retry and interrupted-write guarantees as coalesced bumps.

## Impact

One shared implementation module (`app/core/cache/invalidation.py`), unit/integration regressions, and the query-caching contract/context. No settings, dependencies, additional background workers, API fields, dashboard changes, or migration. Independent of #2463; extracted in response to its maintainer scope review.
