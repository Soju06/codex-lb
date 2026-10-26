## ADDED Requirements

### Requirement: Dashboard overview route and tabs

The dashboard SHALL serve a read-only Overview at `/dashboard/overview` under the same `dashboard:read` guard as `/dashboard`, without adding a core navigation item. Both routes SHALL show "Dashboard" and "Overview" tabs that link to each other, and only the tab for the current route SHALL carry `aria-current="page"`. The Overview page component SHALL load lazily like every other route page. `/accounts/overview` SHALL redirect to `/dashboard/overview` for every signed-in session, including one without `accounts:read`, and the Accounts page SHALL NOT show view tabs.

#### Scenario: Operator opens the overview
- **WHEN** an operator selects the Overview tab on the Dashboard page
- **THEN** the URL becomes `/dashboard/overview` and only the Overview tab is current

#### Scenario: Old accounts overview link
- **WHEN** an operator with or without `accounts:read` opens `/accounts/overview`
- **THEN** the dashboard replaces the URL with `/dashboard/overview`

### Requirement: Dashboard overview loads two endpoints

The Overview SHALL load its figures from `GET /api/dashboard/overview` for the selected timeframe and from `GET /api/request-logs`. It MUST NOT request `/api/dashboard/projections`, `/api/request-logs/options`, or `/api/conversations`.

#### Scenario: Opening the overview
- **WHEN** the Overview renders
- **THEN** the only dashboard data requests are the overview and the request-log list

### Requirement: Dashboard overview activity

The Overview SHALL offer a 24h / 7d / 30d timeframe kept in the `overviewTimeframe` URL parameter (default `7d`, as on the Dashboard page). It SHALL show the same summary cards as the Dashboard page with the account burn projection left out, each with its value, meta line, previous-window delta where the Dashboard shows one, and a sparkline of its trend. It SHALL show an activity chart over the timeframe's buckets with a Requests / Tokens / Error rate switch; the Requests view SHALL also draw errors per bucket as the bucket's requests multiplied by its error rate, rounded.

#### Scenario: Requests view with errors
- **WHEN** a bucket has 200 requests and an error rate of 0.05
- **THEN** the Requests view shows 200 requests and 10 errors for that bucket

#### Scenario: Timeframe switch
- **WHEN** the operator selects 30d
- **THEN** the URL carries `overviewTimeframe=30d` and the cards and chart use the 30-day overview

### Requirement: Dashboard overview accounts tiles

When the pool has no accounts, the Overview SHALL show the accounts empty state in place of the tiles and still show activity and requests. Otherwise it SHALL offer a plan selector listing "All plans" and each raw plan type with its account count, kept in the `plan` URL parameter (absent for all plans; an unknown plan falls back to all plans), and SHALL compute the tiles over the selected plan's accounts. It SHALL show a status tile with the active accounts over the total and a count per status present, and one quota tile per window (5-hour, weekly, monthly) that any selected account reports, with the average used percent per account over accounts with a known remaining percent and how many of the accounts reporting that window have data. Free accounts report a monthly window only, so they SHALL count toward the monthly tile and never toward the 5-hour or weekly tiles. Accounts without a known percent SHALL be excluded from the average and the marks.

For a selection of at most 100 accounts, the status tile SHALL draw one mark per account colored by its status, and each quota tile SHALL draw one column per account with data, ordered by used percent descending, filled in tenths rounded up. For a larger selection, the status tile SHALL draw one bar split by status share, and each quota tile SHALL draw a ten-bucket histogram of used percent with an accessible list of bucket counts and the count of accounts under 10 percent remaining.

#### Scenario: Used percent and missing data
- **WHEN** three accounts report 5-hour remaining 100, 40, and no value
- **THEN** the 5-hour tile shows 30 percent used on average, two columns (60 percent used first, filled 6 of 10; then 0 percent, filled 0), and 2 of 3 accounts with data

#### Scenario: Monthly-only free plan
- **WHEN** the pool has one Plus account with 5-hour and weekly quota and one Free account with only monthly quota at 30 percent remaining
- **THEN** all plans shows 5-hour, weekly, and monthly tiles with the Free account counted only in monthly, and selecting Free shows only a monthly tile at 70 percent used

#### Scenario: Plan selection
- **WHEN** the pool has two Plus accounts and one Pro account and the operator selects "Pro · 1"
- **THEN** the URL carries `plan=pro` and the status tile shows 1 total

#### Scenario: Large plan
- **WHEN** the selected plan has 3,000 accounts
- **THEN** the status tile shows a status-share bar and each quota tile shows a ten-bucket histogram instead of per-account marks

### Requirement: Dashboard overview request list

The Overview SHALL list request logs with the Dashboard's request-log table (same columns, column layout preferences, request details, conversation link to the Dashboard, and page controls), starting at offset 0 with a page size of 25. Changing the status filter SHALL return to the first page. An auto-refresh switch, on by default, SHALL poll the current page at the dashboard refresh interval; when off the list SHALL NOT poll. An All / Success / Errors filter SHALL request no `status`, `status=ok`, or `status=error`, `status=rate_limit`, `status=quota`, and `status=cancelled` respectively. A summary row SHALL show, over the rows of the current page, the row count, the non-`ok` count and share, the average and nearest-rank p95 of known latencies, input and output token totals over known values, and known cached input tokens as a share of known input tokens. A figure with no known values, or a cache share whose input total is zero, SHALL show "–". A request-log failure SHALL show an error with Retry and SHALL NOT be presented as an empty list.

#### Scenario: Auto-refresh off
- **WHEN** the operator turns auto-refresh off
- **THEN** the request list stops polling until it is turned back on

#### Scenario: Errors filter
- **WHEN** the operator selects Errors on the second page
- **THEN** the request-log query carries `status=error`, `status=rate_limit`, `status=quota`, and `status=cancelled` and offset 0

#### Scenario: Unknown tokens
- **WHEN** no loaded row reports input tokens
- **THEN** the input total and the cache share show "–"

#### Scenario: Summary statistics
- **WHEN** the loaded rows have latencies 1, 2, 3, and 4 seconds and one row has status `error`
- **THEN** the summary shows 4 rows, 1 error (25.0%), 2.5 s average, and 4.0 s p95
