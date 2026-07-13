## ADDED Requirements

### Requirement: Reports daily charts fill missing selected days with zero-value rows

The dashboard SHALL render `/reports` `Cost by Day` and `Tokens by Day` charts from a continuous daily series covering every selected day from the current `startDate` through `endDate`. When `GET /api/reports` omits a selected date, the page SHALL insert a zero-value daily row for that date before rendering both charts.

#### Scenario: Missing API dates render as zero-value chart points

- **WHEN** an authenticated operator views `/reports` for a selected date range and the `daily` response omits one or more selected dates
- **THEN** the `Cost by Day` chart includes a point for every selected day from `startDate` through `endDate`
- **AND** each omitted date renders with `costUsd = 0`
- **AND** the `Tokens by Day` chart includes a point for every selected day from `startDate` through `endDate`
- **AND** each omitted date renders with `inputTokens = 0`, `outputTokens = 0`, `cachedInputTokens = 0`, `requests = 0`, `activeAccounts = 0`, and `errorCount = 0`

### Requirement: Daily Breakdown supports explicit visible-column sorting

The dashboard SHALL render `/reports` `Daily Breakdown` with sortable visible columns for `Day`, `Reqs`, `Input Tokens`, `Output Tokens`, `Cost`, and `Accounts`. The default sort SHALL be `Day` descending.

#### Scenario: Daily Breakdown defaults to newest day first

- **WHEN** an authenticated operator opens `/reports`
- **THEN** the `Daily Breakdown` rows are ordered by `Day` descending by default

#### Scenario: Daily Breakdown toggles sorting for a visible column

- **WHEN** an authenticated operator activates any `Daily Breakdown` visible-column header
- **THEN** the table sorts by that column
- **AND** activating the same header again toggles the sort direction between ascending and descending

### Requirement: Reports Tokens summary subtitle shows cached totals

The dashboard SHALL render the `/reports` `Tokens` summary-card subtitle as `Input <value> · Cache <value> · Output <value>` using the current report summary totals for input, cached input, and output tokens.

#### Scenario: Tokens subtitle includes cached total

- **WHEN** an authenticated operator opens `/reports`
- **THEN** the `Tokens` summary card subtitle includes formatted `Input`, `Cache`, and `Output` token totals in that order

### Requirement: Daily Breakdown shows cached input tokens inline

The dashboard SHALL render `/reports` `Daily Breakdown` `Input Tokens` cells as the input-token total followed by the cached input-token total in parentheses using muted secondary text.

#### Scenario: Input Tokens cell shows cached input token count

- **WHEN** a `Daily Breakdown` row has non-zero `inputTokens` and `cachedInputTokens`
- **THEN** the `Input Tokens` cell renders `<formatted inputTokens> (<formatted cachedInputTokens>)`

#### Scenario: Input Tokens cell shows zero cached tokens explicitly

- **WHEN** a `Daily Breakdown` row has `cachedInputTokens = 0`
- **THEN** the `Input Tokens` cell renders the primary input-token value followed by `(0)`
- **AND** if `inputTokens = 0` the full rendered value is `0 (0)`

### Requirement: Daily Breakdown CSV export stays chronological

The dashboard SHALL export `/reports` `Daily Breakdown` CSV rows in ascending `Day` order regardless of the current visible table sort key or direction.

#### Scenario: CSV export ignores visible descending day sort

- **WHEN** an authenticated operator exports the `Daily Breakdown` CSV while the visible table is sorted newest-first
- **THEN** the CSV rows are written from the earliest day to the latest day

#### Scenario: CSV export ignores non-day visible sort

- **WHEN** an authenticated operator exports the `Daily Breakdown` CSV while the visible table is sorted by another column
- **THEN** the CSV rows are still written from the earliest day to the latest day

### Requirement: Daily Breakdown sortable headers show visible sort state

The dashboard SHALL render a visible sort icon on every sortable `/reports` `Daily Breakdown` header. Inactive sortable headers SHALL show a muted gray unsorted indicator, and the active sorted header SHALL show a bright directional indicator matching the current ascending or descending sort direction.

#### Scenario: Inactive sortable headers show unsorted indicator

- **WHEN** an authenticated operator views `/reports`
- **THEN** each sortable `Daily Breakdown` header shows a visible unsorted icon when that column is not the active sort column

#### Scenario: Active sortable header shows ascending indicator

- **WHEN** an authenticated operator activates a `Daily Breakdown` header and the table sort is ascending for that column
- **THEN** that header shows the active ascending sort icon instead of the muted unsorted icon

#### Scenario: Active sortable header shows descending indicator

- **WHEN** an authenticated operator activates the same `Daily Breakdown` header again and the table sort becomes descending
- **THEN** that header shows the active descending sort icon instead of the muted unsorted icon
