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

The dashboard SHALL expose daily non-warmup request counts for the latest six-month display period through a dashboard activity endpoint. The endpoint MUST combine hourly rollup rows with only the un-folded raw tail and MUST filter by a bounded time range.

#### Scenario: Return daily counts

- **WHEN** the request activity endpoint is queried
- **THEN** it SHALL return UTC calendar-day buckets and non-negative request counts
- **AND** days with no requests MAY be omitted from the response because the frontend fills them as zero

#### Scenario: Avoid an unindexed full table scan

- **WHEN** hourly rollups cover part of the requested period
- **THEN** folded data SHALL be read using the hourly rollup time-bucket key range
- **AND** only the raw tail after the rollup watermark SHALL be read from `request_logs` using its requested-at time bound
- **AND** the endpoint SHALL NOT scan all historical request-log rows

### Requirement: Request activity heatmap

The request activity view SHALL display the latest six months of daily request counts in a GitHub-style calendar heatmap.

#### Scenario: Render themed heatmap

- **WHEN** requestHeatmap mode is active
- **THEN** the view SHALL render six months of daily activity cells in seven weekday rows, with intensity increasing with request count
- **AND** the view SHALL NOT render month or weekday axis labels
- **AND** the view SHALL use the active light or dark theme colors

#### Scenario: Explain a day

- **WHEN** the user hovers or focuses an activity cell
- **THEN** a themed tooltip SHALL show the date and request count

#### Scenario: Responsive layout

- **WHEN** the dashboard is viewed on a narrow screen
- **THEN** the heatmap SHALL remain usable without overflowing its dashboard container
