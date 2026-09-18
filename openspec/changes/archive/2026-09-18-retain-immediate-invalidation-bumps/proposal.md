## Why

A committed API-key policy or account-pool mutation could exhaust the immediate cache-invalidation write retry budget and return successfully while losing the peer notification. The mutating replica cleared its own policy cache, but other replicas could keep enforcing the old usage-share percentage, account assignment, or plan capacity until the 60-second cache TTL expired.

The invalidation bus already had durable retry behavior for coalesced `request_bump()` calls, but not for direct awaited `bump()` calls. That split made callers compensate inconsistently: some had no fallback, while the upstream-route cache added its own pending marker and settings changes wrote both `settings` and `upstream_route` solely to survive one failed bump.

## What Changes

- Make `CacheInvalidationPoller.bump()` the single reliable publication primitive. It consumes an older pending marker before its immediate write, preserves a marker added while the write is in flight, and restores the namespace after a failed, cancelled, or otherwise ambiguous write.
- Keep mutations non-failing when invalidation publication cannot land immediately; the running poller retries the retained namespace on later cycles.
- Remove call-site fallback glue and the redundant `upstream_route` bump from upstream-routing settings changes. The local route cache is cleared synchronously after commit, while the durable `settings` bump clears peers.
- Add direct race/failure coverage and feature-level regressions for API-key policy updates, account-routing/allocation changes, and settings-driven route invalidation.

## Capabilities

### Modified Capabilities

- `query-caching`: awaited invalidation bumps gain the same retained-retry guarantee as coalesced bumps, and upstream-routing settings use the existing `settings` namespace without a duplicate signal.

## Impact

The change is confined to the shared invalidation primitive, deletion of redundant route-cache publication glue, cache-invalidation tests, and the query-caching contract. It adds no schema, setting, background worker, compatibility path, or new abstraction.
