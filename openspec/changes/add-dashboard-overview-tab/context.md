# Context: Dashboard overview tab

## Purpose

A light, visual answer to "is the pool healthy and is traffic flowing": activity totals and a chart, account status and quota at a glance, and a live feed of the latest requests. The Dashboard page stays the workbench (filters, donuts, pace, account actions); the Overview is the screen left open on a monitor.

The design follows a three-section relay dashboard the operator shared (activity, accounts, log). It replaces the unreleased plan-grouped Accounts overview, which answered only the accounts part.

## Decisions

- **Dashboard sub-route, not a nav item.** The core navigation is at its budget (`.github/simplicity-budgets.toml`). `routePermission` matches `/dashboard/` by prefix, so `/dashboard/overview` inherits `dashboard:read` without new guard code.
- **Two endpoints.** `GET /api/dashboard/overview` already carries accounts, metrics, comparison, and trends; the request list adds `GET /api/request-logs`. The page skips projections, filter facets, and conversations, which is what makes it lighter than the Dashboard page.
- **Reuse.** Summary cards come from `buildDashboardView(...).stats`, so values, deltas, and copy match the Dashboard page. Pool figures (status counts, average remaining, windows present) come from the all-plans row of `summarizeAccountsByPlan`.
- **Used, not remaining.** The tiles show used percent like the reference design; colors still come from `quotaBarColor` on the remaining percent, so red still means "almost out".
- **User change 2026-10-26: plan selector and large-plan design.** Production runs 3,000+ free accounts, so the accounts tiles are per plan and, above 100 accounts, switch to a status-share bar and a ten-bucket used-percent histogram (the earlier plan overview's form), with the count under 10 percent left.
- **User change 2026-10-26: full request table.** The request list reuses the Dashboard's `RecentRequestsTable` (all columns, details dialog, page controls, shared column layout) instead of a reduced table; pages replace "Load more". The overview chunk now includes the table module.
- **Free quota is monthly.** The backend drops a free account's 5-hour and weekly fields when it has a monthly window, so free accounts land only in the monthly tile. Each tile counts "with data" against the accounts that report that window, not the whole plan, so all plans does not read "35/3,035" for 5-hour.
- **One column per account, sorted.** The earlier page avoided per-account marks because unsorted marks stop being readable past a few dozen accounts. Sorting columns by used percent turns them into a distribution curve that stays readable at hundreds of accounts.
- **Old link outside the guard.** `/accounts/overview` matches the Accounts guard by prefix, so its redirect sits outside `RouteGuard`; otherwise a dashboard-only session would land on `/dashboard` instead of the overview.
- **Own request-list query.** The Dashboard's `useRequestLogs` also loads filter facets and polls every 30 s regardless of preference, so the page runs its own query on `getRequestLogs`.
- **Fixed status sets.** The backend's visible statuses are `ok`, `error`, `rate_limit`, `quota`, and `cancelled`; Errors sends the last four rather than loading the facet endpoint.
- **Errors per bucket** are derived as requests × error rate, because the trend payload carries the rate, not the count.
- **Request statistics are over the current page**, not the whole timeframe: the summary cards already cover the timeframe totals.
- **Not built:** CSV export, search, model/effort/latency filters (the Dashboard page has them), a Vietnamese locale, and input/output token cards (the overview payload has total tokens only).

## Constraints and failure modes

- `GET /api/dashboard/overview` requires `accounts:read`, exactly as for the Dashboard page, which loads the same endpoint. A dashboard-only session reaches the Overview (and is redirected there from `/accounts/overview`) but sees the overview load error, as it does on `/dashboard`; changing the endpoint's permission is a backend change outside this frontend-only change.
- A failed poll keeps the last overview on screen with an error alert and Retry, so stale figures are never presented as current.
- Unknown account statuses count as active through `normalizeStatus`, as elsewhere in the dashboard.
- Unknown token counts are left out of the totals, not counted as zero; a figure with nothing known, or a cache share over zero input tokens, shows "–".
- A request without latency is left out of the latency figures; tokens per second needs both latency and time to first token and a positive generation time.

## Example

Logs with latencies 1, 2, 3, 4 s and one `error`: "4 rows · 1 error (25.0%) · avg 2.5 s · p95 4.0 s".
