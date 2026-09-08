## Why

The backend hardening in `restrict-guest-sensitive-surfaces` answers API-key reads, upstream-proxy administration, the runtime connect address, sticky sessions, and OAuth status with `403 permission_required` for principals without the coarse `write` alias (the built-in guest), masks account emails, and returns no API-key filter options. The dashboard still requests every one of those reads for guests and renders their failures: the APIs page shows a raw `Dashboard permission 'api_keys:read' is required` card with a Retry button that polls the forbidden endpoint every 30 s, the Settings page fires the upstream-proxy and API-key queries on load and the sticky-session query when Advanced is expanded, and the Accounts page fires the upstream-proxy query and lets a guest open the Windows OAuth help that calls the connect-address endpoint. A control the backend will refuse must not be drawn (PLAN §4.8 rule 2, §4.11).

## What Changes

- **APIs page**: for principals without write access the API-key list, trends, and 7-day usage queries stay idle and the page renders a compact administrator-only notice ("API keys are managed by administrators / Sign in as an administrator to view and manage API keys.") in place of the key list. The nav item stays; the page explains.
- **Settings page**: the API key section and the sticky-session section are mounted only for principals with write access; the page-level upstream-proxy administration query is enabled only for them, so the Upstream Proxy card is absent and no error banner appears for guests. The existing read-only notice is unchanged.
- **Accounts page**: the upstream-proxy administration query is enabled only with write access; the "Need help?" Windows OAuth help toggle (which fetches the connect address) is hidden for read-only sessions. Masked account identities (redacted email, null ChatGPT account and workspace ids) render with the existing fallbacks.
- **Request-log filters**: the API key filter control is hidden for sessions without write access, and any `apiKeyId` carried by the URL is dropped from the request-log and filter-option requests (and removed from the address) so a hidden filter never silently restricts results.
- **Cache hygiene**: rendering of the upstream-proxy card and proxy-binding panel is gated on write access as well as on data (a disabled query still returns cached data), and a store subscriber evicts the API-key, upstream-proxy, and sticky-session query families when a session loses write access in-tab.
- The gate signal is the existing `canWrite` store flag; fine-grained `can(permission)` selectors are a later change.

No new nav items, routes, settings, or env vars. `CORE_NAV_ITEMS` is unchanged.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `frontend-architecture`: qualify the Settings page requirement by role (API key table, upstream-proxy administration query, and sticky-session administration are write-only surfaces) and add a requirement that the guest dashboard does not request restricted surfaces.

## Impact

- `frontend/src/features/apis/components/apis-page.tsx`, `frontend/src/features/apis/hooks/use-apis.ts`: `enabled` option, administrator-only state.
- `frontend/src/features/settings/components/settings-page.tsx`, `frontend/src/features/settings/hooks/use-settings.ts`: conditional sections, `enabled` option on `useUpstreamProxyAdmin`.
- `frontend/src/features/accounts/components/accounts-page.tsx`, `account-list.tsx`: idle upstream-proxy query, hidden help toggle, no cached proxy data for guests.
- `frontend/src/features/auth/access-cache-eviction.ts` (new) + `frontend/src/main.tsx`: evict write-only query families when write access is lost.
- `frontend/src/features/dashboard/components/filters/request-filters.tsx`, `dashboard-page.tsx`: optional API key filter.
- `frontend/src/i18n/locales/{en,ko,zh-CN}.json`: two new `apis.page.*` keys.
- Tests: page-level unit tests plus an MSW integration test asserting the guest UI issues none of the restricted requests and that writers still do.
- `docs/authentication.md`: one sentence in "Roles and permissions".
- Depends on the backend change `restrict-guest-sensitive-surfaces` (PR-0a-2) for the 403 contract; the UI change is safe against the previous backend as well (it simply stops requesting data a guest could not act on).
