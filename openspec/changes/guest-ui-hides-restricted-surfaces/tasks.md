## 1. Query gating

- [x] 1.1 Add an `enabled` option to `useApiKeys`, `useApiKeyTrends`, and `useApiKeyUsage7Day` in `frontend/src/features/apis/hooks/use-apis.ts`.
- [x] 1.2 Add an `enabled` option to `useUpstreamProxyAdmin` in `frontend/src/features/settings/hooks/use-settings.ts`.

## 2. Pages

- [x] 2.1 APIs page: read `canWrite`, pass `enabled: canWrite` to the three queries, render the administrator-only notice for read-only sessions; add `apis.page.adminOnlyTitle` / `apis.page.adminOnlyDescription` to `en`, `ko`, and `zh-CN`.
- [x] 2.2 Settings page: mount `ApiKeysSection` and `StickySessionsSection` only when `canWrite`; call `useUpstreamProxyAdmin({ enabled: canWrite })`.
- [x] 2.3 Accounts page: call `useUpstreamProxyAdmin({ enabled: canWrite })`; hide the "Need help?" OAuth help toggle in `AccountList` when `readOnly`.
- [x] 2.4 Request-log filters: add `showApiKeyFilter` to `RequestFilters`; the dashboard hides the filter when the option list is empty and the session cannot write.

## 3. Verification

- [x] 3.1 Unit tests: APIs page guest/writer states and hook `enabled` arguments; Settings page sections and query flag per role; Accounts page query flag, hidden help, masked identity rendering; account list item masked summary; request filters hidden API key filter.
- [x] 3.2 MSW integration test (`frontend/src/__integration__/guest-restricted-surfaces.test.tsx`): guest sessions on `/apis`, `/settings?advanced=1`, and `/accounts` issue none of the restricted requests; writers still issue them.
- [x] 3.3 `bun run lint`, `bun run typecheck`, `bun run test`, `openspec validate guest-ui-hides-restricted-surfaces --strict`.
- [x] 3.4 Before/after guest screenshots of `/apis` and `/settings` captured with the Playwright screenshot harness (P5).

## 4. Documentation

- [x] 4.1 Extend the "Roles and permissions" paragraph in `docs/authentication.md` with the guest dashboard behaviour.
