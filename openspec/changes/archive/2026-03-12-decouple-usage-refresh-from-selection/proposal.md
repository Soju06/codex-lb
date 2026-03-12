## Why
`LoadBalancer.select_account()` currently calls `UsageUpdater.refresh_accounts()` inline on the proxy request path while holding the load-balancer runtime lock. When upstream usage fetches are slow or retrying through the outbound proxy, one request can spend tens of seconds refreshing accounts and every concurrent request waits behind the same lock before it can even open the upstream Responses stream.

The service already starts `UsageRefreshScheduler` during app lifespan and invalidates dependent caches after each background refresh cycle. Keeping a second synchronous refresh loop on the request path duplicates work and creates head-of-line blocking for all LLM responses.

## What Changes
- Stop running `UsageUpdater.refresh_accounts()` inside `LoadBalancer.select_account()`.
- Make request-path account selection use the latest cached `usage_history` rows already persisted by the background scheduler.
- Preserve current routing, sticky-session, model filtering, and quota application behavior against the latest cached rows.

## Impact
- First-byte latency for Responses/compact/transcribe requests is no longer coupled to synchronous usage refresh latency.
- Usage freshness for request-path balancing is bounded by the existing background refresh interval instead of per-request refresh attempts.
- API response shapes do not change.
