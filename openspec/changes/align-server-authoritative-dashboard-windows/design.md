## Context

The dashboard renders two views over `request_logs` that are supposed to describe the same window:

- `GET /api/dashboard/overview?timeframe=7d` — the server resolves `since = utcnow() - timedelta(minutes=window_minutes)` at `app/modules/dashboard/service.py:64,94` (naive-UTC). The activity aggregation at `service.py:115-116` uses this raw `bucket_since` for membership.
- `GET /api/conversations?since=<ISO>` — the frontend resolves `since = new Date(Date.now() - offsetMs).toISOString()` at `frontend/src/features/dashboard/hooks/use-conversations.ts:76-84`, recomputed on every 30-second poll (line 132).

Two coupled defects fall out:

1. **Clock skew → membership disagreement.** When the browser clock differs from the server clock, the conversations table and the dashboard metrics describe different windows. This violates the membership invariant at `openspec/specs/conversations-api/spec.md:56-70`. Even with synchronized clocks, the two queries poll independently at 30 s and race at the live boundary.
2. **Unstable window → cache never hits.** `RequestLogsRepository.list_conversations()` (`app/modules/request_logs/repository.py:208-314`) caches the expensive grouped-count query in a process-local bounded dict (256 entries, 30 s TTL) keyed by the exact `(search, since)` tuple (line 263). Because the frontend recomputes `since` from `Date.now()` on every poll, every 30 s request sends a different ISO string → cache miss → cache fills with one-off entries → the count query re-runs every cycle.

The two defects share a single root cause — there is no server-authoritative, stable window identity that both views share. Fixing the membership defect by routing the conversations endpoint through the same server-derived window also stabilizes the cache identity, resolving both findings together.

The dashboard page (`frontend/src/features/dashboard/components/dashboard-page.tsx:49`) calls `useDashboard(dashboardTimeframe)` and `useConversations({ enabled })` from the same component. `useConversations` already reads the symbolic `conversationTimeframe` from URL search params. A second call site at `conversations-view.tsx:25` mounts `useConversations` standalone (no overview context); that path also reads its timeframe from the URL, so a server-authoritative `timeframe` query parameter fixes it for free.

## Goals / Non-Goals

**Goals:**
- The conversations list window and the dashboard overview window SHALL be derived from the same server clock and the same timeframe configuration, so a row counted by dashboard trends for a timeframe is always listed by the conversations endpoint for the same timeframe (and vice versa), under a fixed server clock.
- The conversations grouped-count cache SHALL hit on the normal 30-second polling path for an unchanged `(search, timeframe)` instead of recomputing on every refetch.
- The conversations endpoint SHALL continue to accept the existing `since` parameter unchanged as a compatibility escape hatch.
- The standalone `ConversationsView` SHALL get the same server-authoritative behavior without depending on the overview query.

**Non-Goals:**
- Transactional snapshot equality across the two independent HTTP requests. A request that straddles a live rolling-window boundary can still observe one row of difference between the two responses; that is normal rolling-dashboard behavior and is not solved by this change.
- Migrating `use-request-logs.ts`, `/api/request-logs`, or `/api/request-locks/options`. Those have the same latent pattern but different ranges (`all`, `1h`, `24h`, `7d`) and a separate facet query; they are deferred to a follow-up change.
- Changing any response schema. This change is request-only and additive.
- Aligning the rolling window to trend buckets. `align_bucket_window_start()` is used only for trend bucketing; membership uses the raw rolling `bucket_since`. Bucket alignment would shift "last 7 days" semantics by up to 6 hours — excessive blast radius.

## Decisions

### Decision 1: Server-authoritative `timeframe` parameter on `/api/conversations` (Approach A)

Add an optional `timeframe=1d|7d|30d` query parameter to `GET /api/conversations`. When supplied, the server computes `since = utcnow() - timedelta(minutes=window_minutes)` using the same `_OVERVIEW_TIMEFRAME_CONFIGS` table that the dashboard overview uses (`app/modules/dashboard/builders.py:31-50`). The frontend sends `timeframe` (which it already reads from URL params) instead of synthesizing a `since`.

**Why over alternatives:**
- *Approach B (overview exposes `conversationsSince`, frontend threads it through):* couples the conversations query to the overview query, introduces a request waterfall, and leaves the standalone `ConversationsView` unfixed. Rejected.
- *Approach C (bucket-align the rolling window in both views):* changes the semantics of "last 7 days" by up to 6 hours (and 1 day for the 30-day window) for every dashboard aggregation. Excessive blast radius for a P2. Rejected.

### Decision 2: Centralize the timeframe resolver

Today the conversations endpoint (`app/modules/request_logs/api.py:33,141`) hardcodes `_CONVERSATION_MAX_LOOKBACK = timedelta(days=30)`, while the dashboard overview uses `resolve_overview_timeframe()` from `app/modules/dashboard/builders.py`. To guarantee the two views compute the same window for the same timeframe, both paths SHALL resolve through a single shared resolver. Concretely: extract a `timeframes.py` (or extend `builders.py`) that exposes `resolve_conversation_timeframe(key) -> (window_minutes, effective_since)` and is reused by both modules. This is the single point of truth that makes the membership invariant mechanically enforceable.

### Decision 3: `timeframe` and `since` are mutually exclusive

Supplying both parameters is rejected with an explicit error rather than silently choosing precedence. Silent precedence is a foot-gun: a client migrating from `since` to `timeframe` that forgets to drop the old param would get surprising windows. The bare request (neither param) preserves the current 30-day default.

### Decision 4: Namespaced semantic cache key

Replace the literal `(search, since)` cache key at `repository.py:263` with a namespaced semantic identity:

- Timeframe mode: `("conversation-count", normalizedSearch, ("timeframe", timeframe))`
- Legacy `since` mode: `("conversation-count", normalizedSearch, ("since", normalizedEffectiveSince))`

`normalizedSearch` trims whitespace and maps empty to `None` exactly as the SQL path already does. `normalizedEffectiveSince` is the existing `effective_since` value the service already computes (post-clamp). `limit`/`offset` are excluded (total is page-independent). The leading `"conversation-count"` namespace prevents accidental collisions if the same dict is ever shared with request-log count entries. Keep the process-local 256-entry / 30-second-TTL cache unchanged.

**Why this fixes Finding 2:** in timeframe mode the cache key is `(search, timeframe)` — stable across 30 s polls, so polling reuses the cached total until TTL expiry. The actual membership query still uses the current server-derived `since` (not a cached timestamp), so the page reflects the current rolling boundary; only the count metadata is allowed to lag by up to TTL.

**Staleness bound:** count metadata (`total`, `has_more`) can lag up to 30 s. Membership is governed by the live `HAVING requested_at >= effective_since` query. The rolling window edge can move by up to ~30 s between polls; this is the same staleness budget the cache already accepts.

**Honesty note:** this does not guarantee that *every* scheduled poll hits the cache — poll and TTL boundaries can coincide. The unstable per-refetch timestamp is removed; that is the correctness claim. Raising TTL above the poll interval is a separate tuning decision.

### Decision 5: Frontend sends `timeframe`; removes `timeframeToSinceIso` from the refetch path

`getConversations` (`frontend/src/features/dashboard/api.ts:107-130`) gains a `timeframe` field. `useConversations` passes `filters.timeframe` through and stops calling `timeframeToSinceIso` in both the `listFilters` memo (line 104) and the queryFn (line 132). The query key (`conversationQueryKey`, lines 109-117) already uses the symbolic timeframe, so it does not need to change — its stability is what made the cache miss obvious. `dashboard-page.tsx` needs no production change because it already passes `enabled` only. The standalone `conversations-view.tsx:25` path also fixes itself because `useConversations` reads the URL timeframe internally.

## Risks / Trade-offs

- **[Rollout ordering]** An old backend ignores the new `timeframe` param and falls back to the 30-day default, producing an inconsistent window while the frontend expects timeframe mode. → Mitigation: ship backend before frontend. Document the ordering in the change tasks and PR description. No client-side fallback is added (the conversations endpoint will return 422 for `timeframe + since` once live, so misordered deploys fail loudly rather than silently).
- **[No snapshot guarantee]** Two independent HTTP requests can still differ at a live rolling boundary. → Mitigation: accepted; documented as a non-goal. If exact screen-level snapshot equality becomes a hard requirement later, add a server-issued window anchor as a separate change.
- **[Cache TTL vs poll interval]** 30 s TTL on a 30 s poll does not mathematically guarantee every poll hits. → Mitigation: accepted; the unstable per-refetch timestamp (the actual bug) is removed. Tuning TTL > poll interval is deferred.
- **[Replica clock skew]** Server-authoritative does not protect against badly skewed DB/app hosts. → Mitigation: out of scope; deployment environments already require clock sync.
- **[Legacy `since` regression]** Existing `since` callers must keep working. → Mitigation: `since` path is preserved verbatim with the same normalization, clamp, and 30-day cap. Regression tests cover timezone offsets, naive timestamps, >30-day clamping, and future timestamps.
- **[Mutual-exclusion confusion]** Clients that send both params get 422. → Mitigation: the frontend never sends both; documented in the API description; integration test asserts the rejection.

## Migration Plan

1. **Backend first.** Ship the new `timeframe` param, the shared resolver, the mutual-exclusion rule, and the namespaced cache key. Existing `since` callers (including the current frontend) keep working unchanged because `since` is still accepted.
2. **Frontend second.** After the backend is live, switch `useConversations` to send `timeframe` and remove the client-side `since` computation. Old frontends behind a CDN/cache continue to work against the new backend (they just send `since`).
3. **Rollback.** Revert the frontend PR to restore client-side `since`. The backend `timeframe` param and cache-key change are additive and safe to leave in place; reverting them is also safe if needed.

## Open Questions

None blocking. The follow-up `use-request-logs.ts` migration is tracked as a separate future change.
