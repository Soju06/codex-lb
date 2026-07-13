# Design: upstream-route resolution cache

## Context

`resolve_upstream_route` reads five tables that only change through rare admin
mutations: `account_proxy_bindings`, `dashboard_settings`
(`upstream_proxy_routing_enabled`, `upstream_proxy_default_pool_id`),
`proxy_pools`, `proxy_pool_members`, `proxy_endpoints`. The proxy hot path
(`streaming/helpers._resolve_upstream_route_for_account`, shared by streaming,
websocket, file ops, compact, transcribe, codex-control, warmup) opens a fresh
session per request purely for this resolution.

## Decisions

### Outcome-verbatim caching (the security invariant)

The cache stores the resolver's terminal outcome per account id: a
`ResolvedUpstreamRoute`, `None` (direct egress permitted), or the
`UpstreamProxyRouteError` reason/pool. A hit reproduces that outcome exactly —
a cached fail-closed error re-raises with the same reason and never falls back
to the default pool or direct egress. This is what keeps the CLAUDE.md
trapdoor ("security/trusted-access routing must degrade only along the
documented path") intact: caching can delay a transition between outcomes
(bounded below), but can never invent a new degradation path.

`OperationalError` (including the tolerated missing-schema rollout path) is
NOT cached: the tolerated branch returns `None` through the resolver, and that
`None` is cached like any direct-egress outcome, bounded by the TTL; a strict
schema error propagates uncached.

### Invalidation map (every resolver input, one mechanism each)

| Mutation | Path | Invalidation |
|---|---|---|
| Account binding upsert | `PUT /settings/upstream-proxy/accounts/{id}/binding` | local clear + durable `upstream_route` bump before response |
| Pool member add | `POST /settings/upstream-proxy/pools/{id}/members` | local clear + durable `upstream_route` bump before response |
| Upstream settings fields | `PUT /settings` | synchronous local clear inside `SettingsRepository.commit_refresh`, between the commit and the refresh await (the committed row must never be observable alongside the stale cache) + durable `upstream_route` bump when either upstream field changed (the existing `settings` bump also clears peers, but it enqueues no retry on a failed write; the `upstream_route` bump carries the coalesced-retry fallback) |
| Account deletion | `DELETE /accounts/{id}` (service layer) | local clear + durable `upstream_route` bump — deletion cascades the binding row away, and account ids are deterministic, so delete-then-re-import regenerates the same cache key |
| Endpoint create | `POST /settings/upstream-proxy/endpoints` | none needed — a new endpoint is not a member of any pool yet, so no cached outcome can reference it |
| Pool create | `POST /settings/upstream-proxy/pools` | none needed — bindings and the default-pool setting validate pool existence, so no cached outcome can reference a pool that did not exist when it was cached |
| Out-of-band DB edits (e.g. manual `is_active` toggles) | — | TTL backstop (60 s default) |

The binding/member/deletion bumps use the awaited durable `bump()` (not the
coalesced `request_bump()`), matching the settings cache: route changes are
security-bearing, so the cross-replica signal is written before the mutating
response returns. Peers converge within one poll interval (~0.5 s). `bump()`
is non-raising; when it reports failure, `invalidate()` enqueues the coalesced
`request_bump()` fallback, which retries on every poll cycle until the write
lands — the TTL bounds peer staleness in the interim.

### Placement and test-transparency

The consult lives in `streaming/helpers._resolve_upstream_route_for_account`
(the single funnel for all proxy-service operations). The miss path still goes
through `_facade().SessionLocal()` / `_facade().resolve_upstream_route`, so
existing tests that monkeypatch the service facade keep working. The test
suite sets `CODEX_LB_UPSTREAM_ROUTE_CACHE_TTL_SECONDS=0` globally (cache
transparent); cache-specific tests opt in explicitly. Background callers
(usage refresh, model discovery, oauth, automations, auth dependencies) keep
their existing fresh reads — they are not hot paths.

### Staleness bounds

- Admin mutation, same replica: 0 (cleared before the response returns).
- Admin mutation, peer replica: ≤ 1 poll interval (~0.5 s); if the durable
  bump write fails, the coalesced retry converges peers on the first poll
  cycle after the database recovers, bounded by the TTL in the interim.
- Out-of-band DB edit: ≤ TTL (60 s default).

A `generation` counter guards repopulation: an outcome resolved concurrently
with an invalidation is dropped rather than stored (same pattern as the
API-key cache version guard).

### Credential residency

Cached routes hold decrypted proxy credentials in process memory for up to the
TTL (today they live in memory for the request duration). Types are frozen
dataclasses shared safely across requests; credentials never appear in logs.
