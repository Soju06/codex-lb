# Design: extend-cache-invalidation-bus

## Decisions

### No new migration

The `cache_invalidation` table already exists (`namespace` TEXT PK, `version` INTEGER); new
namespaces are just rows created lazily by the first upsert. This change adds no Alembic revision.

Rejected: a new `account_routing_unavailable` table (DB-backed set) — it duplicates state already
durable in `accounts.status` and needs its own migration, TTL, and cleanup. The mark always
accompanies a committed status write, so status re-read is the authoritative source.

### Concurrency/atomicity per backend

Bumps reuse the existing dialect-specific atomic upsert: PostgreSQL
`INSERT ... ON CONFLICT (namespace) DO UPDATE SET version = version + 1` (row-level atomic, no
advisory lock needed), SQLite the equivalent `sqlite_insert(...).on_conflict_do_update(...)` atomic
under SQLite's single-writer lock, with SQLITE_BUSY absorbed by a bounded retry (3 attempts,
0.05s/0.2s backoff) and, failing that, by the coalescing pending-set which re-flushes every poll
cycle. No CAS is required: the version is a monotone counter where lost intermediate increments are
harmless (pollers compare `version != prev`, not `prev + 1`), and the routing snapshot is rebuilt
from the authoritative `accounts` table, so refreshes are idempotent.

Rejected: performing the bump inside the mutation's own transaction — bump uses a second session
today and the mutating call sites span ~10 modules with different session lifecycles; retry +
pending-set gives equivalent reliability without threading sessions through.

### Routing snapshot instead of per-request re-read

`_http_bridge_session_account_active` stays a pure in-memory check. The snapshot is refreshed only
on `account_routing` bump events plus one seed query at startup — the hot proxy path gains zero DB
round-trips. Unavailable := status in {PAUSED, REAUTH_REQUIRED, DEACTIVATED} or id absent from
`accounts` (deleted) — exactly the statuses written at today's mark sites. RATE_LIMITED /
QUOTA_EXCEEDED deliberately do NOT map to unavailable (no behavior change for cooldown-state
session reuse). When the snapshot is unseeded (unit tests, poller not running), the cache degrades
to today's local-set semantics — never worse than the status quo.

Local overlay semantics: `mark_*` adds the id to a local overlay set (immediate same-replica
enforcement even before the status write commits); `clear_*` removes it and stamps the snapshot
entry ACTIVE. A snapshot rebuild drops overlay entries whose committed DB status is ACTIVE — this
is what lets a remote reactivation clear a peer's marker. A mark racing an uncommitted status write
can be dropped early by a concurrent rebuild; it self-heals on the next bump/poll because the
mark's own enqueued bump flushes after the mutation commits.

Rejected: TTL on the local set — it bounds the re-auth wedge but leaves pause enforcement racing
the TTL and adds a second freshness mechanism.

### Coalescing `request_bump` vs awaited `bump`

Sync call sites (the ~25 selection-cache invalidations, marks inside request/scheduler paths) use
`request_bump`: a set-add flushed at the start of each poll cycle, giving <=1 DB write per
namespace per 0.5s regardless of burst size; worst-case cross-replica convergence is
flush (<=0.5s) + peer poll (<=0.5s) ~= 1s. The poller owns the flush — no orphan asyncio tasks.
Security-bearing, low-frequency mutations (settings/dashboard-auth mutations, account
pause/reactivate/delete endpoints, OAuth re-auth) `await bump()` directly so the write is durable
before the HTTP response returns.

Rejected: `asyncio.create_task(bump(...))` fire-and-forget (unowned tasks) and per-call awaited
bumps at all sync sites (write amplification on usage-refresh cycles).

### Feedback-loop prevention

Poller callbacks are registered with local-only variants (`invalidate(propagate=False)` / snapshot
rebuild), so a remote bump never re-bumps. A replica observing its own bump re-invalidates locally
once — idempotent and harmless.

### Failure semantics

A bump that exhausts retries does not fail the mutation (local invalidation already happened; peers
converge via the existing TTL fallback) but is now observable: ERROR log +
`codex_lb_cache_invalidation_bump_failures_total{namespace}`. Poll failures escalate from debug to
WARNING after 3 consecutive failures and ERROR after 10, with
`codex_lb_cache_invalidation_poll_failures_total`. The query-caching spec states TTL-as-fallback
normatively so the bound is a documented contract.

### Testability

`RoutingAvailabilityCache`, `AccountSelectionCache`, `SettingsCache`, and
`CacheInvalidationPoller` are all instantiable with injected dependencies, so two-replica tests
construct two instances of each sharing one DB and drive `_poll_once()` directly for determinism —
no sleeps. Tests construct `AccountSelectionCache(ttl_seconds=5)` explicitly to defeat the pytest
ttl=0 default that hides the stale window.

## Deviations from the original design

- Pending `request_bump` namespaces are flushed at the top of `_poll_once()` rather than in the
  `_run()` loop body, so tests that drive `_poll_once()` directly exercise the flush path and the
  production loop behaves identically (it calls `_poll_once()` every cycle).
- The awaited durable bump at API-endpoint mark/clear sites is layered *on top of* the sync
  mark/clear (which also enqueues a coalesced bump). The extra version increment is harmless and
  the retained pending entry guarantees a post-commit bump even when the awaited bump raced an
  uncommitted status write (`settings/api.py` clears before its commit; the durable bump there is
  issued after the commit).
- The account-import path in `accounts/service.py` (`import_account`) keeps the sync
  (coalesced-bump) mark/clear rather than an awaited bump: it is not one of the security-bearing
  endpoints named by the design (pause/reactivate/delete/re-auth) and converges within ~1s.
- `clear_all_account_routing_unavailable()` (test-reset helper) now also resets the snapshot to
  unseeded, preserving test isolation semantics.
- `clear_unavailable()` stamps the snapshot entry ACTIVE even for ids the snapshot has never seen:
  a freshly imported/re-authenticated account must be immediately routable on the replica that
  created it, before the bump-triggered rebuild lands. Overlay marks are dropped on rebuild only
  when the committed status is routable (ACTIVE / RATE_LIMITED / QUOTA_EXCEEDED), not solely
  ACTIVE, so a reactivate-then-rate-limit sequence on a peer still clears the marker.
- `_poll_once()` now creates its session inside the failure-tracked block: a session-factory
  failure previously escaped `_record_poll_failure()` entirely (swallowed at debug in `_run()`
  with no counter), which the bump-resilience test exposed.
- The lifespan shutdown clears the process-global poller reference (when it still points at the
  poller being stopped) so a stopped poller cannot keep receiving propagation requests; the
  startup-time `SettingsCache.invalidate()` in `lifespan` uses `propagate=False` because it is a
  local reset, not a mutation.
