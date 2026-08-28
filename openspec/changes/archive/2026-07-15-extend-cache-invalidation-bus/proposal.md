# Extend cache invalidation bus to routing, selection, and settings caches

## Why

Three process-local caches that gate routing and security decisions — the routing-unavailable account set, `AccountSelectionCache`, and `SettingsCache` — are invisible to the existing cross-replica cache-invalidation bus, which only knows the `api_key` and `firewall` namespaces. In multi-replica deployments this leaves account pause/deactivation unenforced on peers' warm bridge sessions, lets peers route to paused/deleted/reauth-required accounts for the 5s selection TTL, lets security toggles (password, API-key auth, TOTP, guest access) lag 5s on peers, and — worst — leaves a re-authenticated account permanently unroutable on the replica that marked it (no TTL, no bus, cleared only by restart), violating the account-routing spec. Additionally `bump()` swallows write failures and the poller swallows poll errors at debug level, silently degrading the bus to TTL-only, and the bus contract is absent from the query-caching SSOT.

## What Changes

- Add three namespaces to the cache-invalidation bus: `account_routing`, `account_selection`, `settings` (constants only; namespaces are rows in the existing `cache_invalidation` table — no schema migration).
- Replace the bare module-level `_routing_unavailable_account_ids` set with a `RoutingAvailabilityCache` that keeps a DB-derived snapshot (`{account_id: status}`), seeded at startup and rebuilt on every `account_routing` bump. Unavailable means status in {PAUSED, REAUTH_REQUIRED, DEACTIVATED} or the id is absent (deleted). Local mark/clear mutate state immediately and enqueue a bump; the public `mark_/clear_/is_account_routing_unavailable` function API is preserved.
- `AccountSelectionCache.invalidate(propagate=True)` enqueues a coalesced `account_selection` bump; all ~25 existing call sites gain cross-replica propagation with no edits.
- `SettingsCache.invalidate(propagate=True)` awaits a durable `settings` bump before the mutation returns.
- Harden the bus: `bump()` retries transient failures with backoff and reports final failure via ERROR log + `codex_lb_cache_invalidation_bump_failures_total{namespace}`; new coalescing `request_bump()` keeps failed namespaces pending across poll cycles; poll failures escalate to WARNING after 3 / ERROR after 10 consecutive with `codex_lb_cache_invalidation_poll_failures_total`.
- Wire the three poller callbacks in `app/main.py` next to the existing `api_key`/`firewall` registrations; pause/reactivate/delete/reauth mutation endpoints await a durable `account_routing` bump.
- Document the bus contract normatively in query-caching and add cross-replica scenarios to account-routing and admin-auth.

## Non-goals

- Fanning out in-memory HTTP-bridge session *closure* on proxy-binding changes (per-account eviction payloads) — follow-up change; this change is the namespace groundwork.
- Changing RATE_LIMITED / QUOTA_EXCEEDED bridge-session reuse semantics (deliberately unchanged).
