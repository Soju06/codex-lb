## Context

The React dashboard has no 403 handling: `request()` in `frontend/src/lib/api-client.ts` treats any non-2xx as an `ApiError`, react-query retries once, and polling queries keep re-firing. The only role signal components read today is `useAuthStore().canWrite` (true only when the session `permissions` include the `write` alias). Settings, Accounts, and Dashboard already gate their mutation controls on it; the APIs page, `ApiKeysSection`, `StickySessionsSection`, and `useUpstreamProxyAdmin` do not read it at all (Phase 0 fact reports `guest-frontend-consumption.md`, `api-keys-guest-surface.md`).

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

`useApiKeys`, `useApiKeyTrends`, `useApiKeyUsage7Day`, and `useUpstreamProxyAdmin` gain an `{ enabled?: boolean }` option (default `true`, matching the existing `useTelemetryConsent` style). Hooks keep a stable call order; the mutations they return are still constructed (they are never invoked for guests because the controls are not rendered). The APIs page renders the administrator-only state purely from `canWrite`, so a cached admin key list (e.g. after a session downgrade) is never shown to a guest.

### Sections are unmounted, not disabled

`ApiKeysSection` and `StickySessionsSection` self-fetch on mount. Rendering them `disabled` (today's behaviour) still fires the reads. Not mounting them is the only way to keep the sections silent, and it also removes the empty table that a guest could not act on. The Upstream Proxy card already renders only when the admin query has data, so disabling the query removes it without a second condition. The Advanced group's scroll-timing wait uses `isFetching`, so a disabled query does not block deep-link scrolling.

### OAuth help hidden for read-only sessions

The Windows OAuth help only matters to someone who can start an OAuth flow, and its connect-address read is now write-only. The toggle is hidden when `readOnly` (the same prop that already disables "Add account"); the help panel is guarded by the same flag so a stale open state cannot render it.

### Request-log API key filter

`GET /api/request-logs/options` still succeeds for guests with `apiKeys: []`. An empty multi-select is harmless but pointless, so `RequestFilters` takes `showApiKeyFilter` and the dashboard passes `canWrite || apiKeyOptions.length > 0`. Writers with zero keys keep the filter (it doubles as discoverability); guests never see it.

## Risks / Trade-offs

- [Risk] Store defaults are still admin until the first session refresh (H4), so a hard reload as a guest can fire one admin-shaped request before the session arrives. → Out of scope here (PR-0b `harden-dashboard-mutation-origin` fixes the defaults); the page still switches to the guest state as soon as the session is applied, and cached data is not shown.
- [Risk] A future role that holds `api_keys:read` without the `write` alias would see the administrator-only notice. → Acceptable for Phase 0; the gate becomes `can("api_keys:read")` when that selector exists.
- [Trade-off] The guest APIs page is now a notice rather than an error card with Retry. Retrying a permission error can never succeed, so nothing is lost.
