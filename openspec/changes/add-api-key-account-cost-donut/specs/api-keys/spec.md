## ADDED Requirements

### Requirement: API key account cost breakdown

The dashboard SHALL expose a selected API key's last-7-day cost grouped by upstream account for APIs tab visualization.

#### Scenario: Account cost breakdown is sorted for donut rendering

- **WHEN** an admin opens an API key in the APIs tab
- **THEN** the APIs tab fetches the selected key's last-7-day account cost breakdown
- **AND** the trend chart renders in 75% of the chart area on large screens with a title and description
- **AND** a donut chart renders in 25% of the chart area
- **AND** known account slices are ordered by descending 7-day cost
- **AND** the donut legend renders below the circle with no more than three account rows
- **AND** the dashboard account-info privacy toggle applies to email-like account labels in the donut legend

#### Scenario: Missing account association is shown last

- **WHEN** the selected API key has 7-day request-log cost with no matching account
- **THEN** the donut chart groups that cost under `Unknown Account`
- **AND** the `Unknown Account` slice is ordered after known account slices
