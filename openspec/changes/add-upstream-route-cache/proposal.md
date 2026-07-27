## Why

Every proxy request pays an upstream-route resolution before the first upstream byte: `_resolve_upstream_route_for_account` opens a fresh `SessionLocal` and issues 1–3 uncached SELECTs (account proxy binding, dashboard settings row, proxy pool + members + endpoints). Route inputs change only through rare admin mutations, yet the hot path re-reads them on every turn. This is the last unaddressed item from the 2026-07-12 performance audit.

## What Changes

- Add a per-account upstream-route resolution cache consulted by the proxy hot path. A cache hit skips the session open and all resolver SELECTs.
- Cache the resolver's outcome **verbatim**: a resolved route, `None` (direct egress permitted), or the fail-closed `UpstreamProxyRouteError` reason. A hit can therefore never change the degradation path the resolver chose — fail-closed outcomes keep failing closed and never fall back to the default pool or direct egress.
- Add an `upstream_route` cache-invalidation namespace. Account-binding upserts and pool-member additions clear the local cache and durably bump the namespace before their HTTP response returns. Dashboard settings changes already bump the durable `settings` namespace; the route cache also subscribes to it, and the settings update path clears the local cache immediately when either upstream-proxy field changed.
- Add `CODEX_LB_UPSTREAM_ROUTE_CACHE_TTL_SECONDS` (default 60, `0` disables caching) as a TTL backstop for out-of-band database edits; invalidation bumps remain the binding freshness mechanism (~0.5 s cross-replica).

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `query-caching`: upstream-route resolution on the proxy hot path becomes invalidation-driven with a TTL backstop, mirroring the API-key auth cache contract.
- `upstream-proxy-routing`: cached resolution MUST preserve fail-closed semantics — a cache hit reproduces the resolver's outcome exactly and mutations of route inputs invalidate before the mutating response returns.

## Impact

- **Code**: new `app/core/upstream_proxy/cache.py`; `app/core/cache/invalidation.py` (namespace), `app/core/config/settings.py` (TTL setting), `app/modules/proxy/_service/streaming/helpers.py` (hot-path consult), `app/main.py` (poller wiring), `app/modules/settings/api.py` (mutation invalidation hooks). No schema change.
- **Behavior**: resolution outcomes are unchanged; only their freshness window changes. Admin mutations through the API invalidate locally before the response returns and cross-replica within one poll interval; direct database edits are bounded by the 60 s TTL backstop.
