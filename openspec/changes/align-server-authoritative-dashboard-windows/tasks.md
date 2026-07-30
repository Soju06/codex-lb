## 1. Backend shared window contract (Lane 1 — sequential, gates Lanes 2–5)

- [x] 1.1 Add a shared conversation timeframe resolver: expose `CONVERSATION_TIMEFRAME_KEYS = {"1d","7d","30d"}` and `resolve_conversation_timeframe(key) -> (window_minutes, effective_since)` (where `effective_since = utcnow() - timedelta(minutes=window_minutes)`) reusing the existing dashboard `_OVERVIEW_TIMEFRAME_CONFIGS` table. Preferred home: a new `app/modules/dashboard/timeframes.py` (or extend `builders.py` if a separate module is rejected as over-scoping). The `30d` entry MUST produce the same window as the existing `_CONVERSATION_MAX_LOOKBACK` bare default.
- [x] 1.2 Add a `timeframe` query parameter to `GET /api/conversations` in `app/modules/request_logs/api.py` (`DashboardOverviewTimeframeKey`-compatible enum: `1d|7d|30d`, optional). When supplied, compute `effective_since` via the shared resolver from 1.1. Reject `timeframe + since` supplied together with HTTP 422 (explicit validation in the handler, not silent precedence). Preserve the existing `since` path (normalization, 30-day clamp) and the bare-request default unchanged.
- [x] 1.3 Thread `effective_since` (and the chosen mode) into `context.service.list_conversations(...)` so the service/repository can both apply the window for membership and choose the correct cache identity. Do not change response schema.

## 2. Backend cache plumbing (Lane 2 — after Lane 1 interface is frozen; parallel with Lane 3)

- [x] 2.1 Replace the literal `(search, since)` cache key at `app/modules/request_logs/repository.py:263` with a namespaced semantic identity: `("conversation-count", normalizedSearch, mode_token)` where `mode_token = ("timeframe", timeframe)` in timeframe mode and `("since", effective_since)` in legacy `since` mode. `normalizedSearch` trims whitespace and maps empty to `None` exactly as the SQL path does.
- [x] 2.2 Exclude `limit`/`offset` from the key (total is page-independent). Keep the process-local 256-entry / 30-second-TTL cache and its eviction behavior unchanged.
- [x] 2.3 Confirm the membership query still uses the live `effective_since` (the cache stores only the count metadata; the page/facets still reflect the current rolling window).

## 3. Frontend hook + API client (Lane 3 — parallel with Lane 2 after Lane 1 contract is frozen)

- [x] 3.1 Add a `timeframe?: "1d" | "7d" | "30d"` field to `ConversationListFilters` in `frontend/src/features/dashboard/api.ts` and serialize it as `timeframe=<key>` in `getConversations`.
- [x] 3.2 In `frontend/src/features/dashboard/hooks/use-conversations.ts`, stop calling `timeframeToSinceIso` on the refetch path: remove the redundant recomputation in the `queryFn` (line 132) and the `since` field in `listFilters` (line 104); send `timeframe: filters.timeframe` instead. Leave the `conversationQueryKey` memo unchanged (it already keys on the symbolic timeframe).
- [x] 3.3 Keep `timeframeToSinceIso` only if any non-conversations caller still uses it; otherwise remove it. Verify `dashboard-page.tsx` needs no production change (it already passes only `enabled`).

## 4. Backend regression tests (Lane 4 — after Lanes 1–2)

- [x] 4.1 In `tests/integration/test_conversations_api.py` (or the equivalent conversations API test file): add fixed-server-clock parity test asserting `GET /api/conversations?timeframe=7d` and the dashboard overview activity aggregation for `timeframe=7d` agree row-for-row under a mocked `utcnow()`.
- [x] 4.2 Add tests for the new parameter: `timeframe=1d|7d|30d` accepted and mapped to the shared config; unknown `timeframe=24h` rejected with 422; `timeframe + since` together rejected with 422; bare request still defaults to 30 days.
- [x] 4.3 Add legacy-compat regression tests for standalone `since`: timezone-offset strings, naive timestamps, `>30d` clamp, future timestamps, trailing-slash routing.
- [x] 4.4 Add cache-identity tests in `tests/unit/test_request_logs_repository.py`: same `(search, timeframe)` reuses the cached total; different `search` or `timeframe` does not; legacy `since` mode and timeframe mode do not collide; the 256-entry bound evicts without affecting correctness.

## 5. Frontend tests (Lane 5 — after Lane 3)

- [x] 5.1 Update `frontend/src/features/dashboard/hooks/use-conversations.test.ts`: replace assertions that the request carries a browser-generated `since` with assertions that it carries `timeframe=<key>` and no `since`; add a refetch-stability test asserting repeated polls send identical parameters.
- [x] 5.2 Add a clock-skew test: mock `Date.now()` far from server time and assert the outgoing conversations request parameters are unaffected (only `timeframe`/`search`/`limit`/`offset` are sent).
- [x] 5.3 Cover the standalone `ConversationsView` mount path: it also sends `timeframe` and no browser-generated `since` (test in `conversations-view.test.tsx` or the dashboard-flow integration test).

## 6. Verification (final lane — sequential)

- [x] 6.1 Run focused backend tests: `uv run pytest tests/integration/test_conversations_api.py tests/unit/test_request_logs_repository.py -q` (and the dashboard overview test if parity test lives there).
- [x] 6.2 Run focused frontend tests + typecheck: `npm --prefix frontend test -- use-conversations conversations-view` and `npm --prefix frontend run typecheck`.
- [x] 6.3 Validate OpenSpec: `openspec validate align-server-authoritative-dashboard-windows --strict` and `openspec validate --specs`.
- [ ] 6.4 Confirm rollout ordering note is in the PR body: backend MUST deploy before frontend.
