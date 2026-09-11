## 1. Query gating

- [x] 1.1 Add an `enabled` option to `useApiKeys`, `useApiKeyTrends`, and `useApiKeyUsage7Day` in `frontend/src/features/apis/hooks/use-apis.ts`.
- [x] 1.2 Add an `enabled` option to `useUpstreamProxyAdmin` in `frontend/src/features/settings/hooks/use-settings.ts`.

## 2. Pages

- [x] 2.1 APIs page: read `canWrite`, pass `enabled: canWrite` to the three queries, render the administrator-only notice for read-only sessions; add `apis.page.adminOnlyTitle` / `apis.page.adminOnlyDescription` to `en`, `ko`, and `zh-CN`.
- [x] 2.2 Settings page: mount `ApiKeysSection` and `StickySessionsSection` only when `canWrite`; call `useUpstreamProxyAdmin({ enabled: canWrite })`; render the Upstream Proxy card only on `canWrite && data`.
- [x] 2.3 Accounts page: call `useUpstreamProxyAdmin({ enabled: canWrite })`; pass `upstreamProxyAdmin` only when `canWrite`; hide the "Need help?" OAuth help toggle in `AccountList` when `readOnly`.
- [x] 2.5 Add `features/auth/access-cache-eviction.ts` (`installAccessCacheEviction`) and install it from `main.tsx`; evict `["settings","upstream-proxy"]`, `["api-keys"]`, `["sticky-sessions"]` when `canWrite` flips true→false.
- [x] 2.4 Request-log filters: add `showApiKeyFilter` to `RequestFilters` and `allowApiKeyFilters` to `useRequestLogs`; for sessions without write access the dashboard hides the control and drops URL-carried `apiKeyId` values from the effective filters and the address.

## 3. Verification

- [x] 3.1 Unit tests: APIs page guest/writer states and hook `enabled` arguments; Settings page sections and query flag per role (including cached upstream-proxy data not rendered for guests); Accounts page query flag, hidden help, masked identity rendering, cached proxy data not rendered; account list item masked summary; request filters hidden API key filter; access-cache eviction subscriber.
- [x] 3.2 MSW integration test (`frontend/src/__integration__/guest-restricted-surfaces.test.tsx`): guest sessions on `/apis`, `/settings?advanced=1`, and `/accounts` issue none of the restricted requests; `/dashboard?apiKeyId=…` sends no `apiKeyId` for guests and does for writers; writers still issue the restricted reads.
- [x] 3.3 `bun run lint`, `bun run typecheck`, `bun run test`, `openspec validate guest-ui-hides-restricted-surfaces --strict`.
- [x] 3.4 Before/after guest screenshots of `/apis` and `/settings` captured with the Playwright screenshot harness (P5).

## 4. Documentation

- [x] 4.1 Extend the "Roles and permissions" paragraph in `docs/authentication.md` with the guest dashboard behaviour.
