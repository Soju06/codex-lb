## MODIFIED Requirements

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
- **THEN** the page obtains `Model` and `UserAgent` options from `GET /api/reports/options` scoped by dates, timezone, accounts and API keys
- **AND** the options query does not change when only model or User-Agent selection changes

#### Scenario: Reports page shows one shared relaxed-catalog error for report filter choices
- **WHEN** the `GET /api/reports/options` query fails
- **THEN** the page shows one page-owned error describing the combined `Model` and `UserAgent` option loading failure
- **AND** the page does not show separate duplicate relaxed-catalog errors for `Model` and `UserAgent`
