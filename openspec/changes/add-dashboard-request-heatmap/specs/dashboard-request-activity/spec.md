## ADDED Requirements

### Requirement: Dashboard activity display mode

The dashboard SHALL provide a local-only display mode setting with `weeklyPace` and `requestHeatmap` values, defaulting to `weeklyPace` when no valid stored value exists.

#### Scenario: Weekly pace remains the default

- **WHEN** no display mode has been stored
- **THEN** the dashboard SHALL render the existing Weekly credits pace card when valid pace data is available
- **AND** the adjacent Weekly Credits usage view SHALL retain its existing behavior

#### Scenario: Select request heatmap

- **WHEN** the user selects Request activity heatmap in Appearance settings
- **THEN** the dashboard SHALL replace the Weekly credits pace card with the request heatmap
- **AND** the setting SHALL be persisted in browser local storage only
- **AND** no server settings or database row SHALL be changed

#### Scenario: Select weekly pace

- **WHEN** the user selects Weekly credits pace in Appearance settings
- **THEN** the dashboard SHALL render the original Weekly credits pace card and SHALL NOT render the request heatmap

### Requirement: Request activity data

The dashboard SHALL expose sparse daily non-warmup request counts for the latest six calendar months, including the current calendar month, through a dashboard activity endpoint. The endpoint SHALL accept an optional IANA timezone identifier. An absent or invalid timezone value MUST use UTC.

#### Scenario: Return daily counts

- **WHEN** the request activity endpoint is queried
- **THEN** it SHALL capture the current instant once and use the effective timezone to select the local date range beginning on the first day of the calendar month five months before the current local month and ending at that captured instant on the current local date
- **AND** it SHALL return local calendar-day labels and non-negative request counts
- **AND** it SHALL omit days with no requests from the response because the frontend fills them as zero

#### Scenario: Cap the current local day at the captured instant

- **WHEN** the endpoint aggregates the current local date
- **THEN** it SHALL use that date's independently converted local-midnight UTC start and the captured current instant as the exclusive UTC end
- **AND** it SHALL NOT use the following local midnight or any future time as the current-day end

#### Scenario: Resolve the request timezone

- **WHEN** the endpoint receives no timezone or an invalid timezone identifier
- **THEN** it SHALL use UTC for local-day labels, bounds, and aggregation
- **WHEN** the endpoint receives a valid IANA timezone identifier
- **THEN** it SHALL use that timezone for the complete request

#### Scenario: Convert local days across daylight-saving transitions

- **WHEN** the endpoint builds a bound for a completed labeled local calendar day
- **THEN** it SHALL convert that day's local midnight and the following local midnight independently to UTC
- **AND** it SHALL not assume that every local calendar day is a fixed 24-hour UTC interval

#### Scenario: Avoid an unindexed full table scan

- **WHEN** the endpoint loads activity data
- **THEN** the folded rollup aggregation and fold watermark SHALL come from one watermark-consistent `AccountUsageRollupState LEFT JOIN` rollup SQL statement
- **AND** that statement SHALL construct bounded, labeled local-day ranges and sum folded hourly `request_count` values in SQL by those labels
- **AND** separate bounded raw SQL reads SHALL cover exactly the requested UTC intervals not represented by whole folded UTC-hour buckets, including bounded pre-watermark partial edge windows when a local-day boundary is not UTC-hour-aligned, excluding warmup traffic and grouping by the same local-day labels
- **AND** application code SHALL merge only the small per-day folded and raw totals, not materialize full-grain hourly or raw rows
- **AND** the endpoint SHALL NOT scan all historical request-log rows or issue a separate watermark query

#### Scenario: Preserve the hourly-retention boundary limitation

- **WHEN** a local midnight falls between UTC hour boundaries, such as in a non-hour-offset timezone, and raw detail for the relevant pre-watermark partial edge window has been pruned
- **THEN** the endpoint MAY omit or undercount the unreconstructable partial contribution
- **AND** it SHALL NOT attribute the whole folded UTC-hour bucket solely because the partial edge detail is unavailable
- **AND** the bounded SQL path SHALL remain in use rather than reading unbounded historical raw logs

### Requirement: Request activity heatmap

The request activity view SHALL display the latest six calendar months, including the current calendar month, of daily request counts in a GitHub-style calendar heatmap using the requested browser IANA timezone.

#### Scenario: Render themed heatmap

- **WHEN** requestHeatmap mode is active
- **THEN** the view SHALL render six months of daily activity cells in seven weekday rows, with intensity increasing with request count
- **AND** the view SHALL NOT render month or weekday axis labels
- **AND** the view SHALL use the active light or dark theme colors

#### Scenario: Use browser-local calendar components

- **WHEN** the frontend constructs the heatmap's date labels, bounds, or cells
- **THEN** it SHALL use browser-local calendar date components rather than UTC date components
- **AND** it SHALL include the browser's IANA timezone in the activity request and client query/cache key

#### Scenario: Refresh at a bounded interval

- **WHEN** requestHeatmap mode is active
- **THEN** the activity query SHALL refresh on a fixed interval of at least 60 seconds

#### Scenario: Explain a day

- **WHEN** the user hovers or focuses an activity cell
- **THEN** a themed tooltip SHALL show the date and request count

#### Scenario: Responsive layout

- **WHEN** the dashboard is viewed on a narrow screen
- **THEN** the heatmap SHALL remain usable without overflowing its dashboard container
