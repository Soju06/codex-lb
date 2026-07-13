## ADDED Requirements

### Requirement: Reports page exposes a visible user-agent filter

The dashboard SHALL render `/reports` with a visible `UserAgent` filter beside the existing `Model` filter. The `UserAgent` filter SHALL be single-select, SHALL use normalized `request_logs.useragent_group` values for its choices, and SHALL filter the reports payload by sending `useragent_group` on `GET /api/reports` requests.

#### Scenario: Reports page shows the user-agent filter
- **WHEN** an authenticated operator opens `/reports`
- **THEN** the page exposes a visible `UserAgent` filter beside `Model`

#### Scenario: Reports page requests filtered data by normalized user-agent group
- **WHEN** an authenticated operator selects a `UserAgent` value on `/reports`
- **THEN** the page refetches `GET /api/reports` with `useragent_group` set to the selected normalized `request_logs.useragent_group` value

#### Scenario: Reports page reuses the relaxed reports query for user-agent filter choices
- **WHEN** `/reports` loads or refreshes filter choices
- **THEN** the page obtains `UserAgent` filter options from the same relaxed `GET /api/reports` query flow used for report filter-option discovery
- **AND** the page does not require a separate endpoint to load `UserAgent` choices

#### Scenario: Reports page shows one shared relaxed-catalog error for report filter choices
- **WHEN** the relaxed `GET /api/reports` query for report filter-option discovery fails
- **THEN** the page shows one page-owned error describing the combined `Model` and `UserAgent` option loading failure
- **AND** the page does not show separate duplicate relaxed-catalog errors for `Model` and `UserAgent`

### Requirement: Reports page renders a user-agent distribution card

The dashboard SHALL render `/reports` with a `Distribution by UserAgent` card placed below `Distribution by Model`, using aggregated `request_logs.useragent_group` values from `GET /api/reports`.

#### Scenario: Reports page shows user-agent distribution data
- **WHEN** an authenticated operator opens `/reports`
- **THEN** the page renders `Distribution by UserAgent` below `Distribution by Model`

### Requirement: Reports distribution cards toggle between cost and requests

The dashboard SHALL render both `/reports` distribution cards with an upper-right `cost` / `req` toggle that defaults to `cost` and changes the donut slices, percentages, and legend values to match the selected metric. The `Distribution by Model` and `Distribution by UserAgent` donuts SHALL NOT render hover tooltips.

#### Scenario: Distribution cards default to cost mode
- **WHEN** an authenticated operator opens `/reports`
- **THEN** both distribution cards render in `cost` mode by default

#### Scenario: Distribution cards can switch to request mode
- **WHEN** an authenticated operator activates `req` on either distribution card
- **THEN** that card renders request-count slices, request-count values, and request-based percentages
