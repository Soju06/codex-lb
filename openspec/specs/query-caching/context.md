## Overview

The query-caching capability is broader than cache TTLs. It also owns the database query shapes that sit on hot request and dashboard paths, especially when SQLite is the default backend.

## Decisions

- Keep the public request-log and usage APIs unchanged; optimize query shape and indexing underneath them.
- Preserve legacy `usage_history.window IS NULL` semantics as `"primary"` instead of forcing a data backfill in this change.
- Avoid related-table joins on request-log listing unless search actually needs `accounts.email` or `api_keys.name`.
- Avoid full window-ranking scans on hot selector and dashboard aggregate reads; prefer grouped latest-id or PostgreSQL `DISTINCT ON` shapes backed by matching indexes.
- Keep hot-path index migrations idempotent so manual production hotfix indexes do not break later schema upgrades.
- Compute unfiltered request-log filter-option facets with recursive-CTE loose index scans (one btree probe per distinct value) instead of full `DISTINCT` passes; PostgreSQL has no native skip scan, so an unfiltered facet is otherwise a whole-index pass per facet per panel load. The gate is "no user-supplied filters": filtered requests keep the legacy `DISTINCT` shape because selective residual filters can make per-value probes degenerate. NULL second-column pair placement follows each backend's ASC NULL ordering to preserve exact result parity.
- Proxy API-key auth caching is invalidation-driven: every key mutation bumps the `api_key` invalidation namespace and each instance's poller (0.5 s) clears the local cache, so the 60 s TTL is only a backstop for a broken poller. Sticky-session upserts persist and return the row in one `INSERT ... ON CONFLICT ... RETURNING` statement. Never run multiple statements concurrently on one `AsyncSession` (selection-input usage reads are awaited sequentially).
- Serve account request-usage summaries from `account_usage_rollups` plus a live tail (`requested_at > folded_through`) instead of aggregating all `request_logs` history per read. A background fold job (15-min cadence, 24-hour safety lag, ≤7-day slices per transaction, leader-gated and serialized on the migration-seeded `account_usage_rollup_state` row lock) advances the watermark. Duplicate rows share an exact `requested_at`, so a `requested_at` boundary never splits a dedupe group; the 24 h lag must exceed the maximum request duration because log rows are written at stream end but dated at request start. Reads fetch sums + watermark in one statement to stay snapshot-consistent under READ COMMITTED; identity-merge consolidation transfers duplicates' rollup sums to the canonical account. Per-API-key lifetime summaries fold the same way into `api_key_usage_rollups` (API-key semantics: no dedupe, soft-deleted rows included), governed by the same watermark; identity consolidation takes the fold-state row lock before reassigning logs so folds cannot interleave.

## Cross-Replica Cache Invalidation Bus

- The bus is the `cache_invalidation` table (`namespace` TEXT PK, `version` INTEGER) plus one
  `CacheInvalidationPoller` per process (`app/core/cache/invalidation.py`, default poll 0.5s).
  Mutations bump a namespace's version with a dialect-atomic upsert; every process compares
  versions each poll and runs registered callbacks on change.
- Registered namespaces and their callbacks (wired in `app/main.py`):
  - `api_key` -> `ApiKeyCache.clear` (fallback TTL 60s)
  - `firewall` -> `FirewallIPCache.invalidate_all` (fallback TTL `firewall_ip_cache_ttl_seconds`, default 30s)
  - `account_routing` -> `RoutingAvailabilityCache.refresh_from_db` (snapshot of `accounts.id -> status`; no TTL — the snapshot is authoritative once seeded, degraded local-set semantics when unseeded)
  - `account_selection` -> `AccountSelectionCache.invalidate(propagate=False)` (fallback TTL 5s)
  - `settings` -> `SettingsCache.invalidate(propagate=False)` (fallback TTL 5s)
- Two bump flavors: `await bump(namespace)` (durable before the mutation response; used by
  security-bearing endpoints: settings/dashboard-auth mutations, account pause/reactivate/delete,
  OAuth re-auth) and sync `request_bump(namespace)` (coalesced into a pending set flushed at the
  start of each poll cycle; used on hot/scheduler paths). Coalescing bounds writes to <=1 per
  namespace per poll interval; worst-case cross-replica convergence is flush (<=0.5s) + peer poll
  (<=0.5s) ~= 1s for coalesced bumps and one poll interval for awaited bumps.
- Failure semantics: `bump()` retries transient lock errors (3 attempts, 0.05s base backoff); a
  final failure logs ERROR and increments
  `codex_lb_cache_invalidation_bump_failures_total{namespace}` but never fails the mutation —
  peers then converge via the cache's fallback TTL. Failed coalesced flushes stay pending and
  retry next cycle. Poll failures escalate to WARNING after 3 and ERROR after 10 consecutive
  failures and increment `codex_lb_cache_invalidation_poll_failures_total`.
- Poller callbacks must be registered with non-propagating variants — a propagating callback
  would re-bump on every observed bump and loop.
- Routing-unavailable derivation: an account is routing-unavailable when the snapshot says
  PAUSED / REAUTH_REQUIRED / DEACTIVATED, or the id is absent (deleted), or a local mark overlay
  entry exists (covers the window before the accompanying status write commits). RATE_LIMITED and
  QUOTA_EXCEEDED deliberately do not map to unavailable, preserving cooldown-state bridge-session
  reuse. Bridge-session reuse checks stay pure in-memory: zero per-request DB reads.

## Operational Notes

- Primary-window usage reads should normalize on `coalesce(window, 'primary')`.
- Latest usage selection should be backed by a composite latest-row index, not by Python-side deduplication.
- Default request-log listing should sort by latest-first timestamp and tie-breaker ID.
- Do not hold the load-balancer runtime lock across network-bound usage refresh calls; only protect the in-memory selection and runtime-state mutation step.
- Stale usage refreshes should collapse into one in-flight refresh per account, with followers re-checking persisted primary-window data before calling the upstream usage API again.
- On 2026-06-29, production `10.0.0.113` saw Postgres backend OOM kills while dashboard/account-selection requests ran large `request_logs` and `additional_usage_history` window-ranking queries. The durable mitigation is to keep additional-quota latest lookups and account request usage summaries off `row_number()` hot paths, then restore any temporary production registry/timeout workarounds after deployment verification.
- Operator escape hatch for the usage rollup: delete all `account_usage_rollups` rows **and** the `account_usage_rollup_state` row in one transaction; the next fold pass re-bootstraps the state row at epoch and re-backfills from raw `request_logs`, and summaries stay correct throughout because reads fall back to the full live aggregate while no state row exists. Deleting only one of the two is unsafe: rollup rows alone loses folded totals (the watermark stays advanced), and the state row alone re-folds history on top of existing sums (double count).

## Example

These rows must both participate in a primary-window lookup:

```text
usage_history(window=NULL, account_id='acc_1', recorded_at='2026-03-08T10:00:00Z')
usage_history(window='primary', account_id='acc_1', recorded_at='2026-03-08T11:00:00Z')
```

`latest_by_account("primary")` should return only the later row while still treating both rows as part of the same logical primary window.
