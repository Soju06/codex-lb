# report-aggregation Specification

## Purpose
Governs how dashboard reports are assembled from permanent time-bucketed aggregates plus the not-yet-folded raw complement, so historical totals, filter dimensions, and distinct conversation counts survive raw request-log retention and stay correct for any requested timezone. Long report windows previously scanned raw rows until they timed out and every filter change re-ran the full report; this capability bounds that work by preferring folded history, keeping filter options and report caching lightweight, and limiting exact speed metrics to short windows with explicit disclosure.
## Requirements
### Requirement: Permanent report aggregates
The system SHALL preserve request counts, error and cancellation counts, token totals, cost, first activity, active accounts and distinct normalized conversations in permanent time buckets, including account, API key, model and User-Agent filter dimensions. Report days SHALL respect the requested timezone. Folded history and raw complement SHALL be read in one snapshot without overlap. Partial storage buckets SHALL use raw data; retained history at sub-hour boundaries has the same bounded edge limitation as existing hourly statistics.

#### Scenario: Historical and live data coexist
- **GIVEN** a fold watermark inside the report window
- **WHEN** a filtered report is requested
- **THEN** folded rows and raw rows contribute exactly once and totals match the unfurled raw history

#### Scenario: Retention and lifecycle changes
- **WHEN** retention prunes raw logs
- **THEN** it SHALL wait for report fold coverage and report totals SHALL remain available
- **AND** account soft delete, hard delete and consolidation SHALL mirror report aggregates under the shared fold lock

### Requirement: Lightweight filter options
`GET /api/reports/options` SHALL apply the report date, timezone, account and API-key scope and return distinct models and nonblank User-Agent groups without running speed or full-report aggregates. It SHALL use dashboard authentication and the same date-range validation as reports.

#### Scenario: Filter catalog loads
- **WHEN** the report page loads or its scope changes
- **THEN** it SHALL request options from the dedicated endpoint instead of a second full report

### Requirement: Bounded speed work
Exact speed medians SHALL only run for date windows of at most seven days. Longer windows SHALL retain zero-valued numeric speed fields for compatibility, return `speedMetricsAvailable=false` and the maximum supported speed-window size and SHALL NOT execute median SQL. The dashboard SHALL explain the omission and hide unavailable speed charts.

#### Scenario: Ninety-day report
- **WHEN** an operator requests a 90-day report
- **THEN** the report SHALL return aggregate totals without running raw median calculations
- **AND** the page SHALL state that speed metrics require a window of seven days or less

### Requirement: Bounded report caching
Successful reports and options SHALL be cached for at most 60 seconds per normalized filter key within an application process, with bounded entry count and coalesced concurrent requests. Failures and cancelled loads SHALL NOT be cached. Browser report queries SHALL have a five-minute freshness interval, no periodic polling and no automatic retry. An explicit refresh control SHALL remain available. Reports SHALL expose their server generation time and the page SHALL show that time so an open, unpolled report does not imply live data.

#### Scenario: Concurrent identical reports
- **WHEN** concurrent requests use the same report filters
- **THEN** at most one aggregate computation SHALL populate their shared cache result

#### Scenario: Cached result during an unrelated slow computation
- **GIVEN** an unexpired cached result and an unrelated report computation in progress
- **WHEN** the cached report is requested
- **THEN** it SHALL return without waiting for the unrelated computation
- **AND** uncached computations SHALL remain bounded across requests by a cache instance initialized before requests are accepted

#### Scenario: Failed computation
- **WHEN** a computation fails or is cancelled
- **THEN** the next request SHALL be able to compute a fresh result
