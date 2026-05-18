## MODIFIED Requirements

### Requirement: Dashboard page

The Dashboard page SHALL display summary metric cards for a selectable overview timeframe, primary and secondary usage donut charts with legends, account status cards grid, and a recent requests table with filtering and pagination. The supported overview timeframe values MUST be `1d`, `7d`, and `30d`.

#### Scenario: Dashboard data load defaults to weekly overview

- **WHEN** the Dashboard page is rendered without a previously selected overview timeframe
- **THEN** the app fetches `GET /api/dashboard/overview?timeframe=7d` and `GET /api/request-logs` in parallel
- **AND** the overview renders weekly activity totals and trends while the request-log table preserves its own independent filters

#### Scenario: Overview timeframe changes refetch only overview data

- **WHEN** a user changes the dashboard overview timeframe from one supported value to another
- **THEN** the app refetches only `GET /api/dashboard/overview` with the selected `timeframe`
- **AND** the current request-log filters, pagination, and rows remain unchanged until a request-log-specific control changes them

#### Scenario: Request log filtering remains independent from overview timeframe

- **WHEN** a user applies filters to the request logs table, including the request-log timeframe filter
- **THEN** only the request-log queries refetch from `/api/request-logs` and `/api/request-logs/options`
- **AND** the dashboard overview is NOT refetched unless the overview timeframe itself changes

#### Scenario: Quota visuals remain tied to quota windows

- **WHEN** the dashboard overview timeframe changes
- **THEN** the primary and secondary donut charts continue to represent the real primary and secondary quota windows
- **AND** the depletion indicators continue to be computed from those quota windows rather than the selected overview timeframe

### Requirement: Backend API response optimization

The backend API response schemas SHALL be optimized to eliminate over-fetching and under-fetching. Dashboard overview responses MUST use window-neutral aggregate field names and MUST expose explicit overview timeframe metadata. This is a BREAKING change; legacy frontend compatibility is not required.

#### Scenario: Dashboard overview exposes explicit timeframe metadata

- **WHEN** the frontend fetches `GET /api/dashboard/overview?timeframe=1d`
- **THEN** the response includes `timeframe.key = "1d"`, `timeframe.windowMinutes = 1440`, `timeframe.bucketSeconds = 3600`, and `timeframe.bucketCount = 24`
- **AND** each `trends.*` series contains exactly 24 points

#### Scenario: Weekly overview uses clean aggregate field names

- **WHEN** the frontend fetches `GET /api/dashboard/overview?timeframe=7d`
- **THEN** the response contains `summary.cost.totalUsd`
- **AND** the response contains `summary.metrics.requests`, `summary.metrics.tokens`, `summary.metrics.cachedInputTokens`, `summary.metrics.errorRate`, `summary.metrics.errorCount`, and `summary.metrics.topError`
- **AND** the response does NOT contain `summary.cost.totalUsd7d`
- **AND** the response does NOT contain `summary.metrics.requests7d`, `summary.metrics.tokensSecondaryWindow`, `summary.metrics.cachedTokensSecondaryWindow`, or `summary.metrics.errorRate7d`

#### Scenario: Monthly overview uses stable daily trend density

- **WHEN** the frontend fetches `GET /api/dashboard/overview?timeframe=30d`
- **THEN** the response includes `timeframe.windowMinutes = 43200`, `timeframe.bucketSeconds = 86400`, and `timeframe.bucketCount = 30`
- **AND** each `trends.*` series contains exactly 30 points

#### Scenario: Dashboard overview still omits request logs

- **WHEN** the frontend fetches `GET /api/dashboard/overview`
- **THEN** the response contains `timeframe`, `accounts`, `summary`, `windows`, and `trends`
- **AND** the response does NOT contain `request_logs`
