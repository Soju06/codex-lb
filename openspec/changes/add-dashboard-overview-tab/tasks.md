## 1. Implementation

- [x] 1.1 Move the view tabs to the dashboard ("Dashboard | Overview") and remove them from the Accounts page. Add `/dashboard/overview` as a lazy route in `App.tsx`; add the `/accounts/overview` redirect outside `RouteGuard` so sessions without `accounts:read` also land on `/dashboard/overview`.
- [x] 1.2 Add a pure helper `summarizeRequestLogs(rows)` (count, non-`ok` count and share, average and nearest-rank p95 latency, known token totals, cache share, "unknown" as `null`) and the per-account used-percent columns helper.
- [x] 1.3 Add the overview page: activity cards from `buildDashboardView(...).stats`, the activity chart through `lazy-recharts`, accounts tiles from `summarizeAccountsByPlan(...)` with a plan selector and a distribution form above 100 accounts (an empty pool renders the accounts empty state in place of the tiles, while activity and requests stay usable), and the request list through `RecentRequestsTable` fed by a page-local `useQuery` on `getRequestLogs` only (page size 25, fixed status sets, `refetchInterval` = dashboard refresh seconds or `false`). Plan selector, large-plan form, and full table added on user request 2026-10-26.
- [x] 1.4 Keep the plan summary helper (`features/accounts/overview.ts` + tests) from the unreleased plan-grouped overview, without its page, tabs, or strings; add en/ko/zh-CN strings for the new page.

## 2. Tests

- [x] 2.1 Helper unit tests: p95 and average over known latencies, error share, known-only token totals, zero/unknown cache denominator, column order and tenths rounding, missing remaining percent.
- [x] 2.2 Page tests (MSW): only `/api/dashboard/overview` and `/api/request-logs` are requested (never projections, options, conversations); timeframe switch writes `overviewTimeframe`; Errors/Success/All send the specified `status` params and return to offset 0; Next page asks for offset 25; plan selection narrows the tiles and writes `plan`; a 101-account plan shows the distribution form; auto-refresh off stops polling (fake timers); account labels follow privacy blur; `accounts: []` renders the accounts empty state with activity and requests still shown.
- [x] 2.3 Integration tests: Dashboard ↔ Overview tabs with `aria-current`; rewrite the List/Overview test in `accounts-flow.test.tsx` to assert the Accounts page has no view tabs and `/accounts/overview` lands on `/dashboard/overview` for an admin; change the dashboard-only case in `nav-permissions-flow.test.tsx` to expect `/dashboard/overview`.

## 3. Verification

- [x] 3.1 Run `npm test`, lint, typecheck, and build in `frontend/`; confirm the overview page is its own lazy chunk in the build output; run the simplicity budget check.
- [ ] 3.2 Run `openspec validate add-dashboard-overview-tab --strict` (the CLI is not installed locally; install it or rely on the CI OpenSpec check).
- [x] 3.3 Capture before/after screenshots at desktop, tablet, and mobile widths in light and dark themes from the running app.
- [ ] 3.4 After acceptance: `openspec verify`, sync the delta into `openspec/specs/frontend-architecture/spec.md`, and archive the change.
