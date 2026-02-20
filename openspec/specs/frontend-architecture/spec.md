# frontend-architecture Specification

## Purpose
TBD - created by archiving change frontend-react-migration. Update Purpose after archive.
## Requirements
### Requirement: Vite project structure

The frontend SHALL be a standalone Vite + React + TypeScript project located at `frontend/` in the repository root. The build output SHALL target `app/static/` so that FastAPI serves the built assets without configuration changes.

#### Scenario: Development server

- **WHEN** the developer runs `npm run dev` in `frontend/`
- **THEN** the Vite dev server starts with HMR and proxies `/api/*`, `/v1/*`, `/backend-api/*`, `/health` requests to the FastAPI backend

#### Scenario: Production build

- **WHEN** the developer runs `npm run build` in `frontend/`
- **THEN** Vite outputs optimized assets (JS, CSS, index.html) to `app/static/` with content-hashed filenames

### Requirement: SPA routing

The application SHALL use React Router v6 for client-side routing with three routes: `/dashboard`, `/accounts`, `/settings`. The root path `/` SHALL redirect to `/dashboard`. FastAPI SHALL serve `index.html` for all unmatched routes as a SPA fallback.

#### Scenario: Direct navigation to route

- **WHEN** a user navigates directly to `/accounts` in the browser
- **THEN** FastAPI serves `index.html` and React Router renders the Accounts page

#### Scenario: Client-side navigation

- **WHEN** a user clicks the "Settings" tab from the Dashboard page
- **THEN** the URL changes to `/settings` without a full page reload and the Settings page renders

### Requirement: Authentication gate

The application SHALL check the session state via `GET /api/dashboard-auth/session` on initial load. When `passwordRequired` is true and `authenticated` is false, the application MUST render only the login form. All other routes and UI elements MUST be hidden until authenticated.

#### Scenario: Unauthenticated load with password required

- **WHEN** the app loads and the session endpoint returns `{ "passwordRequired": true, "authenticated": false }`
- **THEN** only the login form is visible; navigation tabs and page content are not rendered

#### Scenario: Authenticated load

- **WHEN** the app loads and the session endpoint returns `{ "authenticated": true }`
- **THEN** the full application with navigation tabs and default page (Dashboard) is rendered

#### Scenario: TOTP verification pending

- **WHEN** the session returns `{ "passwordRequired": true, "authenticated": false, "totpRequiredOnLogin": true }` and the user has completed password login
- **THEN** a TOTP input dialog is shown; the full UI is not accessible until TOTP verification succeeds

### Requirement: Theme support

The application SHALL support light and dark themes using Tailwind CSS dark mode (class strategy). The theme preference MUST be persisted to localStorage and applied on load. A theme toggle button MUST be visible in the application header.

#### Scenario: Theme toggle

- **WHEN** a user clicks the theme toggle button
- **THEN** the theme switches between light and dark and the preference is saved to localStorage

#### Scenario: Theme persistence

- **WHEN** the app loads with a previously saved theme preference
- **THEN** the saved theme is applied immediately without flash

### Requirement: Dashboard page

The Dashboard page SHALL display: summary metric cards (requests 7d, tokens, cost, error rate), primary and secondary usage donut charts with legends, account status cards grid, and a recent requests table with filtering and pagination.

#### Scenario: Dashboard data load

- **WHEN** the Dashboard page is rendered
- **THEN** the app fetches `/api/dashboard/overview` (accounts, summary, windows) and `/api/request-logs` (recent requests) in parallel, rendering all dashboard sections with the combined data

#### Scenario: Auto-refresh

- **WHEN** the Dashboard page is active
- **THEN** the dashboard overview and request logs are independently refetched at a regular interval (30 seconds)

#### Scenario: Request log filtering

- **WHEN** a user applies filters (search, timeframe, account, model, status) to the request logs table
- **THEN** only the request logs query refetches from `/api/request-logs` with the applied filter parameters; the dashboard overview is NOT refetched

#### Scenario: Request log pagination

- **WHEN** a user changes the page size or navigates to the next page
- **THEN** the request logs query refetches with updated offset/limit parameters and the response includes `total` count and `has_more` flag for pagination state

### Requirement: Accounts page

The Accounts page SHALL display a two-column layout: left panel with searchable account list, import button, and add account button; right panel with selected account details including usage, token info, and actions (pause/resume/delete/re-authenticate).

#### Scenario: Account selection

- **WHEN** a user clicks an account in the list
- **THEN** the right panel shows the selected account's details

#### Scenario: Account import

- **WHEN** a user clicks the import button and uploads an auth.json file
- **THEN** the app calls `POST /api/accounts/import` and refreshes the account list on success

#### Scenario: Ambiguous duplicate identity import conflict

- **WHEN** `importWithoutOverwrite` was previously enabled and duplicate accounts with the same email exist
- **AND** overwrite mode is enabled again
- **AND** a new import matches multiple existing accounts by email without an exact ID match
- **THEN** `POST /api/accounts/import` returns `409` with `error.code=duplicate_identity_conflict`
- **AND** no existing account is modified

#### Scenario: OAuth add account

- **WHEN** a user clicks the add account button
- **THEN** an OAuth dialog opens with browser and device code flow options

#### Scenario: Account actions

- **WHEN** a user clicks pause/resume/delete on an account
- **THEN** the corresponding API is called and the account list is refreshed

### Requirement: Settings page

The Settings page SHALL include sections for: routing settings (sticky threads, reset priority), password management (setup/change/remove), TOTP management (setup/disable), API key auth toggle, and API key management (table, create, edit, delete, regenerate).

#### Scenario: Save routing settings

- **WHEN** a user toggles sticky threads or reset priority
- **THEN** the app calls `PUT /api/settings` with the updated values

#### Scenario: Password setup

- **WHEN** a user sets a password from the settings page
- **THEN** the app calls `POST /api/dashboard-auth/password/setup` and reflects the new auth state

#### Scenario: API key management

- **WHEN** a user creates an API key via the settings page
- **THEN** the app calls `POST /api/api-keys` and displays the plain key in a dialog with a copy button and a warning that it will not be shown again

### Requirement: API client type safety with Zod runtime validation

All API calls SHALL use a shared typed fetch wrapper (`lib/api-client.ts`). Each feature MUST define Zod schemas in `schemas.ts` and typed API functions in `api.ts`. API responses MUST be validated against Zod schemas at runtime. TypeScript types MUST be derived from Zod schemas via `z.infer<>` to maintain a single source of truth.

#### Scenario: Type-safe API call with runtime validation

- **WHEN** a feature component calls an API function
- **THEN** the response is parsed through a Zod schema, the return type is statically known at compile time, and any schema mismatch is detected at runtime

#### Scenario: API error handling

- **WHEN** an API call returns a non-2xx response
- **THEN** the error is parsed into a structured `ApiError` and displayed to the user

#### Scenario: 401 interceptor

- **WHEN** any API call returns 401
- **THEN** the auth store is updated to reflect unauthenticated state and the login form is shown

#### Scenario: Zod validation failure

- **WHEN** an API response does not match the expected Zod schema
- **THEN** in development mode, a detailed error is logged to console; in production, the response is passed through with a warning

### Requirement: Server state management

All server data (accounts, dashboard, request logs, settings, API keys) SHALL be managed via TanStack Query. Queries MUST use semantic query keys for cache invalidation. Mutations MUST invalidate related queries on success.

#### Scenario: Mutation cache invalidation

- **WHEN** an account is paused via a mutation
- **THEN** the accounts list query and dashboard overview query are both invalidated and refetched

#### Scenario: Optimistic updates not required

- **WHEN** a mutation is submitted
- **THEN** the UI shows a loading state until the mutation completes; no optimistic update is applied

### Requirement: Backend API response optimization

The backend API response schemas SHALL be optimized to eliminate over-fetching and under-fetching. This is a BREAKING change; legacy frontend compatibility is not required.

#### Scenario: Dashboard overview without request logs

- **WHEN** the frontend fetches `GET /api/dashboard/overview`
- **THEN** the response contains `accounts`, `summary`, and `windows` but does NOT contain `request_logs`

#### Scenario: AccountSummary field reduction

- **WHEN** the frontend fetches accounts (via dashboard overview or account list)
- **THEN** the `AccountSummary` does NOT include `capacity_credits_primary`, `remaining_credits_primary`, `capacity_credits_secondary`, `remaining_credits_secondary`, `last_refresh_at`, or `deactivation_reason`

#### Scenario: Request logs pagination metadata

- **WHEN** the frontend fetches `GET /api/request-logs` with offset/limit
- **THEN** the response includes `total` (total matching count) and `has_more` (boolean) alongside `requests`

#### Scenario: Filter options with statuses

- **WHEN** the frontend fetches `GET /api/request-logs/options`
- **THEN** the response includes `statuses` (list of available status values) alongside `account_ids` and `model_options`

### Requirement: Frontend test infrastructure

The frontend project SHALL include a Vitest-based test infrastructure with React Testing Library and MSW v2 for API mocking. Tests SHALL be colocated with source files as `.test.ts(x)`. Shared test utilities (render wrapper, MSW server, data factories) SHALL reside in `src/test/`.

#### Scenario: Test runner execution

- **WHEN** the developer runs `npm test` in `frontend/`
- **THEN** Vitest discovers and executes all `*.test.ts(x)` files using the Vite config (path aliases, plugins) without additional configuration

#### Scenario: Component test with API data

- **WHEN** a component test renders a component that fetches data via TanStack Query
- **THEN** MSW intercepts the API call at the network level, the component renders with mock data, and assertions verify the rendered output

#### Scenario: Zod schema contract test

- **WHEN** a Zod schema test runs `safeParse` with a valid API response fixture
- **THEN** the parse succeeds and the output matches the expected type; when run with an invalid fixture (missing field, wrong type), the parse fails with a descriptive error

#### Scenario: Test isolation

- **WHEN** multiple tests run in sequence
- **THEN** each test has a fresh QueryClient (no cache leakage), MSW handlers are reset between tests, and no localStorage/DOM state persists

### Requirement: Test coverage targets

Zod schema tests SHALL achieve 95%+ line coverage. Utility function tests SHALL achieve 90%+ line coverage. Component tests SHALL achieve 70-80% line coverage. Vitest coverage thresholds SHALL be configured to fail CI below the minimum floor (70% overall).

#### Scenario: Coverage gate

- **WHEN** the developer runs `npm run test:coverage` in `frontend/`
- **THEN** Vitest produces a coverage report and the build fails if overall line coverage drops below 70%

### Requirement: Existing feature parity

The React application MUST implement all features present in the current Alpine.js application. No existing functionality SHALL be removed or degraded during migration.

#### Scenario: Feature checklist

- **WHEN** the migration is complete
- **THEN** every feature documented in the current frontend (dashboard metrics, donut charts, account cards, request log table with all filters, OAuth flow with browser and device methods, account import, account details with token info, settings toggles, password management, TOTP setup/verify/disable, API key CRUD with model/limit/expiry settings, theme toggle, status bar) is functional in the React application

### Requirement: Old frontend removal

After migration is complete, the old frontend files SHALL be removed: `app/static/index.html`, `app/static/index.js`, `app/static/index.css`, and all files in `app/static/components/`.

#### Scenario: Clean removal

- **WHEN** the React build outputs to `app/static/`
- **THEN** no legacy Alpine.js files remain in the directory

