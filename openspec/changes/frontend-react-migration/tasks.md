## 0. Backend API Optimization

- [x] 0.1 Remove `request_logs` field from `DashboardOverviewResponse` schema and `DashboardService.get_overview()` — request logs are fetched exclusively via `/api/request-logs`
- [x] 0.2 Remove `requestLimit`/`requestOffset` query params from `GET /api/dashboard/overview` endpoint
- [x] 0.3 Remove unused fields from `AccountSummary` schema: `capacity_credits_primary`, `remaining_credits_primary`, `capacity_credits_secondary`, `remaining_credits_secondary`, `last_refresh_at`, `deactivation_reason`
- [x] 0.4 Remove `email` field from `UsageHistoryItem` schema (duplicates accounts array)
- [x] 0.5 Remove `by_model` field from `UsageCost` schema (UI 미사용)
- [x] 0.6 Add `total: int` and `has_more: bool` fields to `RequestLogsResponse` — compute from `COUNT(*)` and `offset + limit < total`
- [x] 0.7 Add `statuses: list[str]` field to `RequestLogFilterOptionsResponse` — return distinct status values from filtered request logs
- [x] 0.8 Update `DashboardService` and `AccountsService` mappers to reflect removed fields
- [x] 0.9 Update existing backend tests to match changed response schemas

## 1. Project Scaffolding

- [x] 1.1 Initialize Vite + React + TypeScript project in `frontend/` (`npm create vite@latest frontend -- --template react-ts`)
- [x] 1.2 Configure `vite.config.ts` — set `build.outDir` to `../app/static`, `build.emptyOutDir` to true, add `@tailwindcss/vite` plugin, add dev proxy for `/api/*`, `/v1/*`, `/backend-api/*`, `/health`, configure `@` path alias
- [x] 1.3 Install Tailwind CSS v4: `npm install tailwindcss @tailwindcss/vite`, replace `src/index.css` with `@import "tailwindcss";` (no tailwind.config.js needed)
- [x] 1.4 Configure TypeScript path aliases in `tsconfig.json` and `tsconfig.app.json` — `"@/*": ["./src/*"]`, install `@types/node`
- [x] 1.5 Initialize shadcn/ui — run `npx shadcn@latest init` (Neutral base color), auto-generates CSS variables and theme config
- [x] 1.6 Install core dependencies — `react-router-dom`, `@tanstack/react-query`, `zustand`, `zod`, `lucide-react`
- [x] 1.7 Install test dependencies — `vitest`, `jsdom`, `@testing-library/react`, `@testing-library/jest-dom`, `@testing-library/user-event`, `msw`
- [x] 1.8 Configure TypeScript strict mode in `tsconfig.app.json`
- [x] 1.9 Configure Vitest in `vite.config.ts` — `test.globals: true`, `test.environment: 'jsdom'`, `test.setupFiles`, coverage with v8 provider and 70% threshold

## 2. Shared Infrastructure

- [x] 2.1 Create `lib/api-client.ts` — fetch wrapper with Zod runtime validation: `get<T>(url, schema: ZodType<T>): Promise<T>`, `post<T>`, `patch<T>`, `del` functions, structured `ApiError` class, 401 interceptor, dev-mode Zod parse error logging
- [x] 2.2 Create `lib/query-client.ts` — TanStack QueryClient with default `staleTime`, `retry`, `refetchOnWindowFocus` settings
- [x] 2.3 Create `lib/utils.ts` — `cn()` helper (clsx + tailwind-merge)
- [x] 2.4 Create `schemas/api.ts` — shared Zod schemas for API error responses, `ApiError` type derived via `z.infer<>`
- [x] 2.5 Create `utils/formatters.ts` — migrate all number/date/token/currency/percent formatters from current `index.js`
- [x] 2.6 Create `utils/constants.ts` — migrate STATUS_LABELS, ERROR_LABELS, ROUTING_LABELS, KNOWN_PLAN_TYPES, DONUT_COLORS, MESSAGE_TONE_META
- [x] 2.7 Create `utils/colors.ts` — migrate `adjustHexColor`, `buildDonutPalette`, `buildDonutGradient`
- [x] 2.8 Create `test/setup.ts` — import `@testing-library/jest-dom/vitest`, MSW server lifecycle (`beforeAll/afterEach/afterAll`), RTL `cleanup`
- [x] 2.9 Create `test/utils.tsx` — `renderWithProviders()` wrapper: creates fresh `QueryClient` (retry: false, gcTime: 0) + `QueryClientProvider` + `BrowserRouter`
- [x] 2.10 Create `test/mocks/server.ts` — MSW `setupServer` with `onUnhandledRequest: 'error'`
- [x] 2.11 Create `test/mocks/handlers.ts` — default MSW handlers for all API endpoints (happy path responses matching Zod schemas)
- [x] 2.12 Create `test/mocks/factories.ts` — Zod 스키마 기반 테스트 데이터 팩토리 (valid fixtures for each API response type)

## 3. shadcn/ui Components

- [x] 3.1 Add shadcn components: `button`, `input`, `label`, `dialog`, `alert-dialog`, `select`, `dropdown-menu`, `checkbox`, `switch`, `table`, `badge`, `progress`, `tabs`, `card`, `tooltip`, `separator`, `skeleton`, `sonner` (toast)
- [x] 3.2 Create `components/layout/app-header.tsx` — logo, tab navigation (React Router NavLink), theme toggle, logout button
- [x] 3.3 Create `components/layout/status-bar.tsx` — sync time, routing strategy, backend path display
- [x] 3.4 Create `components/layout/loading-overlay.tsx` — full-screen spinner with backdrop
- [x] 3.5 Create `components/status-badge.tsx` — status badge using shadcn `Badge` with variant mapping (active/paused/limited/exceeded/deactivated)
- [x] 3.6 Create `components/donut-chart.tsx` — conic-gradient donut with center label and legend (pure CSS, no chart library)
- [x] 3.7 Create `components/confirm-dialog.tsx` — reusable alert/confirm dialog wrapping shadcn `AlertDialog`
- [x] 3.8 Create `components/copy-button.tsx` — clipboard copy with feedback toast

## 4. Stores & Providers

- [x] 4.1 Create `hooks/use-theme.ts` — zustand store for dark/light theme, localStorage persistence, `<html>` class sync
- [x] 4.2 Create `features/auth/hooks/use-auth.ts` — zustand store for auth session state (`passwordRequired`, `authenticated`, `totpRequiredOnLogin`), `refreshSession()`, `login()`, `logout()`, `verifyTotp()`
- [x] 4.3 Create `main.tsx` — wrap App in `QueryClientProvider`, `BrowserRouter`, initialize theme
- [x] 4.4 Create `app.tsx` — `AuthGate` → `Layout(Header + Outlet + StatusBar)` → `Routes`

## 5. Auth Feature

- [x] 5.1 Create `features/auth/schemas.ts` — Zod schemas for `AuthSession`, `LoginRequest`, `PasswordSetupRequest`, `PasswordChangeRequest`; export derived types via `z.infer<>`
- [x] 5.2 Create `features/auth/api.ts` — typed functions for all `/api/dashboard-auth/*` endpoints, each passing Zod schema to api-client for runtime validation
- [x] 5.3 Create `features/auth/components/login-form.tsx` — password input, submit, error display, loading state
- [x] 5.4 Create `features/auth/components/totp-dialog.tsx` — shadcn Dialog with 6-digit input, auto-focus, error display
- [x] 5.5 Create `features/auth/components/auth-gate.tsx` — wraps children, shows login form or TOTP dialog based on auth state

## 6. Dashboard Feature

- [x] 6.1 Create `features/dashboard/schemas.ts` — Zod schemas for `DashboardOverview` (without request_logs), `DashboardMetrics`, `UsageWindow`, `RequestLog`, `RequestLogsResponse` (with `total`, `has_more`), `RequestLogFilterOptions` (with `statuses`), `FilterState`; export types via `z.infer<>`
- [x] 6.2 Create `features/dashboard/api.ts` — `getDashboardOverview()`, `getRequestLogs(params)`, `getRequestLogOptions()` with Zod validation; overview and request-logs are separate queries
- [x] 6.3 Create `features/dashboard/utils.ts` — `buildDashboardView`, `buildRemainingItems`, `buildDonutGradient` wrappers, `avgPerHour`
- [x] 6.4 Create `features/dashboard/hooks/use-dashboard.ts` — TanStack Query hook with 30s refetchInterval
- [x] 6.5 Create `features/dashboard/hooks/use-request-logs.ts` — query hook with filter/pagination state via URL searchParams
- [x] 6.6 Create `features/dashboard/components/dashboard-page.tsx` — page layout with all dashboard sections
- [x] 6.7 Create `features/dashboard/components/stats-grid.tsx` — 4 metric cards (requests, tokens, cost, error rate)
- [x] 6.8 Create `features/dashboard/components/usage-donuts.tsx` — primary + secondary donut chart panels with legends
- [x] 6.9 Create `features/dashboard/components/account-cards.tsx` — grid of account status cards
- [x] 6.10 Create `features/dashboard/components/account-card.tsx` — individual card with status badge, progress bar, reset time, actions
- [x] 6.11 Create `features/dashboard/components/recent-requests-table.tsx` — table with column headers, row rendering, expandable error cells
- [x] 6.12 Create `features/dashboard/components/filters/request-filters.tsx` — filter toolbar container
- [x] 6.13 Create `features/dashboard/components/filters/timeframe-select.tsx` — timeframe dropdown (All, 1h, 24h, 7d)
- [x] 6.14 Create `features/dashboard/components/filters/multi-select-filter.tsx` — reusable multi-select with checkboxes (account, model, status)
- [x] 6.15 Create `features/dashboard/components/filters/pagination-controls.tsx` — page size select + prev/next

## 7. Accounts Feature

- [x] 7.1 Create `features/accounts/schemas.ts` — Zod schemas for `AccountSummary` (경량화된 스키마: capacity/remaining credits, last_refresh_at, deactivation_reason 없음), `OAuthState`, `ImportState`; export types via `z.infer<>`
- [x] 7.2 Create `features/accounts/api.ts` — accounts CRUD + OAuth + import endpoints with Zod validation
- [x] 7.3 Create `features/accounts/hooks/use-accounts.ts` — query + mutations for list/pause/resume/delete/reactivate
- [x] 7.4 Create `features/accounts/hooks/use-oauth.ts` — OAuth flow state machine (start, poll, complete, countdown)
- [x] 7.5 Create `features/accounts/components/accounts-page.tsx` — two-column layout (list + detail)
- [x] 7.6 Create `features/accounts/components/account-list.tsx` — searchable list with filter, import button, add button
- [x] 7.7 Create `features/accounts/components/account-list-item.tsx` — list item with email, status badge, plan label
- [x] 7.8 Create `features/accounts/components/account-detail.tsx` — selected account details container
- [x] 7.9 Create `features/accounts/components/account-usage-panel.tsx` — primary + secondary progress bars with labels
- [x] 7.10 Create `features/accounts/components/account-token-info.tsx` — access/refresh/id token status display
- [x] 7.11 Create `features/accounts/components/account-actions.tsx` — action buttons (pause/resume/delete/reauth)
- [x] 7.12 Create `features/accounts/components/import-dialog.tsx` — file upload dialog for auth.json
- [x] 7.13 Create `features/accounts/components/oauth-dialog.tsx` — OAuth flow dialog (intro → browser/device → success/error)

## 8. Settings Feature

- [x] 8.1 Create `features/settings/schemas.ts` — Zod schemas for `DashboardSettings`, `SettingsUpdateRequest`; export types via `z.infer<>`
- [x] 8.2 Create `features/settings/api.ts` — `getSettings()`, `updateSettings()` with Zod validation
- [x] 8.3 Create `features/settings/hooks/use-settings.ts` — query + mutation with cache invalidation
- [x] 8.4 Create `features/settings/components/settings-page.tsx` — page layout with all settings sections
- [x] 8.5 Create `features/settings/components/routing-settings.tsx` — sticky threads + reset priority switches
- [x] 8.6 Create `features/settings/components/password-settings.tsx` — setup/change/remove password sections
- [x] 8.7 Create `features/settings/components/totp-settings.tsx` — TOTP toggle, setup panel with QR code, disable with code input

## 9. API Keys Feature

- [x] 9.1 Create `features/api-keys/schemas.ts` — Zod schemas for `ApiKey`, `ApiKeyCreateRequest`, `ApiKeyCreateResponse`, `ApiKeyUpdateRequest`; export types via `z.infer<>`
- [x] 9.2 Create `features/api-keys/api.ts` — CRUD + regenerate endpoints with Zod validation
- [x] 9.3 Create `features/api-keys/hooks/use-api-keys.ts` — query + mutations (create, update, delete, regenerate)
- [x] 9.4 Create `features/api-keys/components/api-keys-section.tsx` — section container with auth toggle and key table
- [x] 9.5 Create `features/api-keys/components/api-key-table.tsx` — table with name, prefix, models, usage, expiry, status, actions
- [x] 9.6 Create `features/api-keys/components/api-key-create-dialog.tsx` — create form (name, model multi-select, weekly limit, expiry)
- [x] 9.7 Create `features/api-keys/components/api-key-created-dialog.tsx` — plain key display with copy button and warning
- [x] 9.8 Create `features/api-keys/components/api-key-edit-dialog.tsx` — edit form (name, models, limit, expiry, active toggle)
- [x] 9.9 Create `features/api-keys/components/api-key-auth-toggle.tsx` — apiKeyAuthEnabled switch with description

## 10. FastAPI SPA Routing Update

- [x] 10.1 Update `app/main.py` — replace individual route handlers + StaticFiles mount with unified SPA catch-all serving `app/static/index.html` for all non-API, non-static routes

## 11. Legacy Cleanup

- [x] 11.1 Remove `app/static/index.html` (old Alpine.js SPA)
- [x] 11.2 Remove `app/static/index.js` (old monolithic JS)
- [x] 11.3 Remove `app/static/index.css` (old main stylesheet)
- [x] 11.4 Remove `app/static/components/` directory (23 old CSS files)
- [x] 11.5 Add `frontend/node_modules/` and `frontend/dist/` to `.gitignore`
- [x] 11.6 Update `Dockerfile` to include Node.js build stage (multi-stage: node build → python runtime)

## 12. Unit Tests — Zod Schemas & Utilities

- [x] 12.1 Create `schemas/api.test.ts` — shared API error schema validation (valid/invalid inputs, required fields, type coercion)
- [x] 12.2 Create `features/auth/schemas.test.ts` — AuthSession, LoginRequest schema tests (safeParse valid/invalid, default values)
- [x] 12.3 Create `features/dashboard/schemas.test.ts` — DashboardOverview schema without request_logs, RequestLogsResponse with total/has_more, UsageWindow, AccountSummary (경량화 검증: removed fields rejected)
- [x] 12.4 Create `features/accounts/schemas.test.ts` — AccountSummary light schema, OAuthState, ImportState
- [x] 12.5 Create `features/settings/schemas.test.ts` — DashboardSettings, SettingsUpdateRequest
- [x] 12.6 Create `features/api-keys/schemas.test.ts` — ApiKey, ApiKeyCreateResponse (includes key field), ApiKeyUpdateRequest
- [x] 12.7 Create `utils/formatters.test.ts` — all formatter functions (numbers, dates, tokens, currency, percent)
- [x] 12.8 Create `utils/colors.test.ts` — adjustHexColor, buildDonutPalette, buildDonutGradient
- [x] 12.9 Create `utils/constants.test.ts` — STATUS_LABELS, ERROR_LABELS mapping completeness

## 13. Component & Hook Tests

- [x] 13.1 Create `features/auth/components/login-form.test.tsx` — render, submit, error display, loading state
- [x] 13.2 Create `features/auth/components/auth-gate.test.tsx` — unauthenticated → login form, authenticated → children, TOTP pending → dialog
- [x] 13.3 Create `features/auth/hooks/use-auth.test.ts` — refreshSession, login, logout, verifyTotp state transitions
- [x] 13.4 Create `features/dashboard/hooks/use-dashboard.test.ts` — query with MSW, 30s refetchInterval, loading/error states
- [x] 13.5 Create `features/dashboard/hooks/use-request-logs.test.ts` — filter params → query key mapping, pagination with total/has_more
- [x] 13.6 Create `features/dashboard/components/stats-grid.test.tsx` — renders 4 metric cards with formatted values
- [x] 13.7 Create `features/dashboard/components/usage-donuts.test.tsx` — renders donut charts with legend, handles empty data
- [x] 13.8 Create `features/dashboard/components/recent-requests-table.test.tsx` — renders rows, status badges, expandable error cells
- [x] 13.9 Create `features/accounts/hooks/use-accounts.test.ts` — list, pause/resume/delete mutations with cache invalidation
- [x] 13.10 Create `features/accounts/components/account-list.test.tsx` — search filter, renders items, empty state
- [x] 13.11 Create `features/settings/hooks/use-settings.test.ts` — query, mutation, cache invalidation
- [x] 13.12 Create `features/api-keys/hooks/use-api-keys.test.ts` — CRUD mutations, create returns plain key
- [x] 13.13 Create `hooks/use-theme.test.ts` — toggle, localStorage persistence, html class sync
- [x] 13.14 Create `components/donut-chart.test.tsx` — renders conic-gradient style, center label, legend items
- [x] 13.15 Create `components/copy-button.test.tsx` — clipboard write, feedback toast

## 14. Integration Tests

- [x] 14.1 Create `__integration__/auth-flow.test.tsx` — full auth flow: unauthenticated → login → TOTP → dashboard visible
- [x] 14.2 Create `__integration__/dashboard-flow.test.tsx` — dashboard load (overview + request-logs parallel), filter change → only request-logs refetch, pagination
- [x] 14.3 Create `__integration__/accounts-flow.test.tsx` — account list → select → detail panel, pause/resume actions
- [x] 14.4 Create `__integration__/api-keys-flow.test.tsx` — create key → plain key dialog → edit → delete

## 15. Verification

- [x] 15.1 Verify all Dashboard features — metrics, donuts, account cards, request log table with all filter types, pagination
- [x] 15.2 Verify all Accounts features — search, import, OAuth (browser + device), account details, pause/resume/delete/reauth
- [x] 15.3 Verify all Settings features — routing toggles, password management, TOTP setup/disable, API key auth toggle
- [x] 15.4 Verify all API Key features — create (with plain key dialog), edit, delete, regenerate, model restriction display
- [x] 15.5 Verify auth flow — login gate, password login, TOTP dialog, logout, session persistence
- [x] 15.6 Verify theme — toggle, persistence, no flash on load
- [x] 15.7 Verify production build — `npm run build` outputs to `app/static/`, FastAPI serves correctly
- [x] 15.8 Verify test suite — `npm test` passes all unit/component/integration tests, `npm run test:coverage` meets 70% threshold
