## Context

The React dashboard has no 403 handling: `request()` in `frontend/src/lib/api-client.ts` treats any non-2xx as an `ApiError`, react-query retries once, and polling queries keep re-firing. The only role signal components read today is `useAuthStore().canWrite` (true only when the session `permissions` include the `write` alias). Settings, Accounts, and Dashboard already gate their mutation controls on it; the APIs page, `ApiKeysSection`, `StickySessionsSection`, and `useUpstreamProxyAdmin` do not read it at all.

## Goals / Non-Goals

**Goals**

- A guest never issues `GET /api/api-keys*`, `GET /api/settings/upstream-proxy`, `GET /api/settings/runtime/connect-address`, or `GET /api/sticky-sessions` from any page, and never sees a control that depends on their result.
- The APIs page explains the restriction in plain words instead of surfacing the backend error string.
- Writers see exactly what they see today (regression-tested).
- Nav budget untouched (`CORE_NAV_ITEMS` keeps five entries).

**Non-Goals**

- Fine-grained `can(permission)` selectors, `requires` on nav items, route guards, least-privilege store defaults (H4), or a global 403 handler — later Phase 0/1 changes.
- Hiding the APIs nav item (PLAN §4.8 keeps nav constant; the page explains).
- Any backend change.

## Decisions

### Gate on `canWrite`, not on a 403 response

Alternative rejected: let the request fail and switch to an empty state on `ApiError.status === 403`. That still issues the forbidden request (and re-issues it on every poll), still flashes the skeleton, and leaks the permission name into the UI. The store already knows whether the principal can write before the page mounts, so the queries are disabled with react-query's `enabled` and the empty state is chosen from the store. When a later change adds `can(perm)`, only the gate expression changes.

### Idle queries via an `enabled` option, not conditional hook calls

`useApiKeys`, `useApiKeyTrends`, `useApiKeyUsage7Day`, and `useUpstreamProxyAdmin` gain an `{ enabled?: boolean }` option (default `true`, matching the existing `useTelemetryConsent` style). Hooks keep a stable call order; the mutations they return are still constructed (they are never invoked for guests because the controls are not rendered). `enabled: false` only stops fetching: TanStack still returns whatever is cached, and an in-tab admin→guest switch (logout into a passwordless guest session, or a guest login within `gcTime`) leaves the admin's responses in the cache. Rendering is therefore gated on `canWrite` as well — the APIs page renders the administrator-only state purely from `canWrite`, the Settings page mounts the Upstream Proxy card only on `canWrite && data`, and the Accounts page passes `upstreamProxyAdmin` only when `canWrite` — and a small store subscriber (`features/auth/access-cache-eviction.ts`, installed once from `main.tsx`) removes the `["settings","upstream-proxy"]`, `["api-keys"]`, and `["sticky-sessions"]` query families the moment `canWrite` flips from true to false, so the stale data does not linger for a later re-enable either. The auth store itself is untouched.

### Sections are unmounted, not disabled

`ApiKeysSection` and `StickySessionsSection` self-fetch on mount. Rendering them `disabled` (today's behaviour) still fires the reads. Not mounting them is the only way to keep the sections silent, and it also removes the empty table that a guest could not act on. The Upstream Proxy card already renders only when the admin query has data, so disabling the query removes it without a second condition. The Advanced group's scroll-timing wait uses `isFetching`, so a disabled query does not block deep-link scrolling.

### OAuth help hidden for read-only sessions

The Windows OAuth help only matters to someone who can start an OAuth flow, and its connect-address read is now write-only. The toggle is hidden when `readOnly` (the same prop that already disables "Add account"); the help panel is guarded by the same flag so a stale open state cannot render it.

### Request-log API key filter

`GET /api/request-logs/options` still succeeds for guests with `apiKeys: []`, so a guest can never pick a key; `RequestFilters` takes `showApiKeyFilter` and the dashboard passes `canWrite`. Hiding the control is not enough on its own: the filter state lives in the URL, so `/dashboard?apiKeyId=key_1` (a bookmark, or an admin's selection retained across logout) would still be sent to `/api/request-logs` and `/api/request-logs/options`, and the backend honours it for guests, silently narrowing the results with no visible chip to clear. `useRequestLogs` therefore takes `allowApiKeyFilters`; when false, `apiKeyIds` is forced to `[]` in the effective filters (requests, `filtersApplied`, conversation summary) and the stale `apiKeyId` parameters are rewritten out of the address with `replace: true` so they cannot be re-applied. Other parameters are untouched. Writers keep the previous behaviour, including the control with zero keys.

## Risks / Trade-offs

- [Risk] Store defaults are still admin until the first session refresh (H4), so a hard reload as a guest can fire one admin-shaped request before the session arrives. → Out of scope here (PR-0b `harden-dashboard-mutation-origin` fixes the defaults); the page still switches to the guest state as soon as the session is applied, and cached data is not shown.
- [Risk] A future role that holds `api_keys:read` without the `write` alias would see the administrator-only notice. → Acceptable for Phase 0; the gate becomes `can("api_keys:read")` when that selector exists.
- [Trade-off] The guest APIs page is now a notice rather than an error card with Retry. Retrying a permission error can never succeed, so nothing is lost.
