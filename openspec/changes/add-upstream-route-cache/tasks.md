## 1. Implementation

- [x] 1.1 Add `NAMESPACE_UPSTREAM_ROUTE` to the cache-invalidation bus (constant + log label)
- [x] 1.2 Add `upstream_route_cache_ttl_seconds` setting (default 60, ge=0, 0 disables)
- [x] 1.3 Add `app/core/upstream_proxy/cache.py`: outcome-verbatim per-account cache with generation guard, TTL backstop, `invalidate()` (local clear + durable bump)
- [x] 1.4 Consult the cache in `streaming/helpers._resolve_upstream_route_for_account`; miss path keeps the facade session/resolver calls
- [x] 1.5 Wire poller callbacks in `app/main.py` (`upstream_route` + `settings` namespaces clear the route cache)
- [x] 1.6 Invalidate on mutations in `app/modules/settings/api.py`: binding PUT and pool-member POST (`invalidate()` after commit), settings PUT (local clear when either upstream field changed)
- [x] 1.7 Disable the cache globally in the test suite (`CODEX_LB_UPSTREAM_ROUTE_CACHE_TTL_SECONDS=0`) and reset it in `_reset_global_state`

## 2. Tests

- [x] 2.1 Unit: cache primitives — TTL expiry, 0-disables, generation guard drops stale repopulation, cached error re-raises the same reason
- [x] 2.2 Unit: hot-path helper — second call served from cache without resolver/session calls; cached fail-closed error keeps raising without default-pool/direct fallback (trapdoor)
- [x] 2.3 Integration: binding PUT and pool-member POST clear the local cache and durably bump `upstream_route`; settings PUT with an upstream field change clears the local cache
- [x] 2.4 Namespace log-label sync test stays green (new namespace labeled)

## 3. Validation & docs

- [x] 3.1 `openspec validate --specs`, ruff, ty, architecture check, pytest (SQLite + PostgreSQL)
- [x] 3.2 Change-level context recorded; sync stable notes into `openspec/specs/query-caching/context.md` at archive time
