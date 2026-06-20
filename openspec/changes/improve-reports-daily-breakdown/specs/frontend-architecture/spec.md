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

### Requirement: Daily Breakdown shows cached input tokens inline

The dashboard SHALL render `/reports` `Daily Breakdown` `Input Tokens` cells as the input-token total followed by the cached input-token total in parentheses using muted secondary text.

#### Scenario: Input Tokens cell shows cached input token count

- **WHEN** a `Daily Breakdown` row has non-zero `inputTokens` and `cachedInputTokens`
- **THEN** the `Input Tokens` cell renders `<formatted inputTokens> (<formatted cachedInputTokens>)`

#### Scenario: Input Tokens cell shows zero cached tokens explicitly

- **WHEN** a `Daily Breakdown` row has `cachedInputTokens = 0`
- **THEN** the `Input Tokens` cell renders the primary input-token value followed by `(0)`
- **AND** if `inputTokens = 0` the full rendered value is `0 (0)`
