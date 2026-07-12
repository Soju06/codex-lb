## ADDED Requirements

### Requirement: Reports page sends browser-local timezone context

The `/reports` page SHALL detect the browser's current IANA timezone, cache the latest detected valid value locally for convenience, and include a valid timezone in `GET /api/reports` requests whenever one is available. The page SHALL prefer the browser's current valid timezone over any cached value, SHALL reuse the cached valid timezone when live detection is unavailable or invalid, and SHALL omit the `timezone` query parameter only when neither the live nor cached value is valid.

#### Scenario: Reports page includes browser timezone on requests

- **WHEN** an authenticated operator opens `/reports` or changes a report filter
- **THEN** the request to `GET /api/reports` includes the browser's current IANA timezone in the `timezone` query parameter when detection succeeds

#### Scenario: Reports page reuses cached timezone when live detection fails

- **WHEN** the browser cannot provide a valid IANA timezone name
- **AND** the page has a cached valid timezone from an earlier successful detection
- **THEN** the reports page still requests `GET /api/reports`
- **AND** the request uses the cached valid timezone in the `timezone` query parameter

#### Scenario: Reports page omits timezone only when no valid timezone is available

- **WHEN** the browser cannot provide a valid IANA timezone name
- **AND** the page does not have a cached valid timezone
- **THEN** the reports page still requests `GET /api/reports`
- **AND** the request omits the `timezone` query parameter

### Requirement: Reports endpoint applies timezone-aware ranges and daily bucketing

`GET /api/reports` SHALL interpret `start_date` and `end_date` as calendar dates in the supplied IANA timezone, convert those local-midnight boundaries to UTC for filtering, and group `daily` rows by calendar day in that same timezone. When the timezone is missing or invalid, the endpoint MUST fall back to UTC.

#### Scenario: Reports endpoint uses local-day buckets before UTC midnight

- **WHEN** `/api/reports` receives `start_date`, `end_date`, and `timezone=America/Los_Angeles`
- **AND** a request log row falls on `2026-06-02T01:30:00Z`
- **THEN** the row is included in the `2026-06-01` daily bucket for that response

#### Scenario: Reports endpoint falls back to UTC for invalid timezone

- **WHEN** `/api/reports` receives an invalid `timezone` value
- **THEN** the endpoint still returns a successful response
- **AND** it interprets the report range and daily buckets in UTC

### Requirement: Reports summary cards show previous-window deltas conservatively

`GET /api/reports` SHALL expose a `comparison` block for the `Total Cost`, `Tokens`, and `Requests` summary cards that includes `canCompare` plus the previous-window totals for cost, tokens, and requests. The current window and previous window SHALL use equal calendar-window lengths derived from the selected report date range. The endpoint SHALL set `canCompare` to `true` only when eligible report history fully covers the immediately preceding window. When `canCompare` is `false`, the `/reports` summary cards SHALL hide the previous-window percentage indicators. Even when `canCompare` is `true`, an individual summary card SHALL hide its own percentage indicator when that card's previous-window total is zero.

#### Scenario: Reports summary cards show previous-window increase

- **WHEN** `GET /api/reports` returns current summary totals plus `comparison.canCompare: true`
- **AND** a previous-window total for `Total Cost`, `Tokens`, or `Requests` is lower than the current total for that same card
- **THEN** the matching summary card renders a visible percentage-change increase indicator

#### Scenario: Incomplete previous window suppresses comparison

- **WHEN** the earliest eligible report activity is later than the start of the immediately preceding report window
- **THEN** `GET /api/reports` returns `comparison.canCompare: false`
- **AND** the `/reports` summary cards do not render previous-window percentage indicators

#### Scenario: Zero previous total suppresses the matching card indicator

- **WHEN** `GET /api/reports` returns `comparison.canCompare: true`
- **AND** the previous-window total for one of `Total Cost`, `Tokens`, or `Requests` is `0`
- **THEN** that summary card does not render a previous-window percentage indicator
- **AND** the other summary cards may still render percentage indicators when their own previous-window totals are greater than `0`

### Requirement: Reports daily breakdown renders a continuous calendar window

The `/reports` daily breakdown table SHALL render one row per calendar day in the selected date range. Each row SHALL display its date as an ISO `yyyy-mm-dd` calendar date string. If the reports API omits one or more days inside that range, the table SHALL synthesize zero-valued rows for those days using the same row styling as API-backed rows. The table SHALL keep the header visible while only the data rows scroll, with a default visible body height of seven row heights.

#### Scenario: Daily breakdown fills missing days with zero-valued rows

- **WHEN** the selected reports window spans `2026-06-05` through `2026-06-12`
- **AND** the reports API returns daily rows for every day except `2026-06-06`
- **THEN** the daily breakdown renders a row for `2026-06-06`
- **AND** that row shows zero requests, zero input tokens, zero output tokens, zero cost, and zero accounts
- **AND** that row uses the same row styling as neighboring rows

#### Scenario: Daily breakdown header stays visible while rows scroll

- **WHEN** the daily breakdown contains more than seven rows
- **THEN** the table header remains visible
- **AND** only the table body scrolls vertically through the remaining rows

#### Scenario: Daily breakdown preserves ISO bucket dates

- **WHEN** the reports API returns a daily bucket row with `date` set to `2026-06-01`
- **THEN** the daily breakdown table renders that row label as `2026-06-01`

### Requirement: Reports daily charts use symmetric horizontal padding

The `/reports` `Cost by Day` and `Tokens by Day` charts SHALL use equal left and right horizontal plot padding within their chart cards.

#### Scenario: Daily charts render with balanced left and right inset

- **WHEN** an authenticated operator opens `/reports`
- **THEN** the `Cost by Day` and `Tokens by Day` charts render with equal left and right horizontal padding around the plotted area

## MODIFIED Requirements

### Requirement: Reports page exposes visible filter controls

The `/reports` page SHALL expose visible filter controls for `7d`, `30d`, and `90d` quick presets, start date, end date, account, and model. When an authenticated operator clicks one of the quick presets, the page SHALL visibly highlight that preset. When the operator manually edits the start date or end date afterward, the page SHALL clear the quick-preset highlight until another quick preset is clicked. The start and end date inputs SHALL disallow selecting dates later than the browser's current local calendar date.

#### Scenario: Reports page shows report filter controls

- **WHEN** an authenticated operator opens `/reports`
- **THEN** the page exposes visible filter controls for `7d`, `30d`, and `90d` quick presets, start date, end date, account, and model

#### Scenario: Quick preset highlight follows the selected preset

- **WHEN** an authenticated operator clicks the `30d` quick preset on `/reports`
- **THEN** the page visibly highlights the `30d` preset
- **AND** the page updates the start and end dates to the `30d` preset range

#### Scenario: Quick preset highlight clears after manual date edits

- **WHEN** an authenticated operator clicks a quick preset on `/reports`
- **AND** then manually edits the start date or end date
- **THEN** the page clears the quick-preset highlight
- **AND** the page keeps the edited date range values

#### Scenario: Report date inputs disallow future dates

- **WHEN** an authenticated operator opens `/reports`
- **THEN** the start date and end date inputs prevent selecting a date later than the browser's current local calendar date
