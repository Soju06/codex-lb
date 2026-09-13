MUST distinguish first-run empty states from filter-empty states on dashboard operator lists.

## ADDED Requirements

### Requirement: First-run empty lists describe setup, not filter mismatch

Accounts, APIs, and dashboard request-log empty states SHALL distinguish a
first-run empty source list from a filtered-empty result. When the source
list has no items and no narrowing filter is applied, the empty copy SHALL
describe setup (no accounts yet, no API keys yet, no requests yet) and SHALL
NOT tell the operator to adjust filters. When the source list has items, or
request-log filters differ from their defaults, and the visible result is
empty, the empty copy SHALL describe a filter mismatch.

#### Scenario: Accounts first-run empty copy

- **GIVEN** the Accounts page has no accounts
- **WHEN** the account list renders
- **THEN** the empty title describes that there are no accounts yet
- **AND** the empty description does not tell the operator to adjust filters

#### Scenario: Accounts filtered empty copy

- **GIVEN** the Accounts page has at least one account
- **AND** the current search or status filter matches none of them
- **WHEN** the account list renders
- **THEN** the empty title describes that no accounts match
- **AND** the empty description tells the operator to adjust filters

#### Scenario: APIs first-run empty copy

- **GIVEN** the APIs page has no API keys
- **WHEN** the API key list renders
- **THEN** the empty title describes that there are no API keys yet
- **AND** the empty description does not tell the operator to adjust filters

#### Scenario: Request logs first-run empty copy

- **GIVEN** the request-log listing has no rows
- **AND** request-log filters are at their defaults
- **WHEN** the recent-requests table renders
- **THEN** the empty title is `No requests yet`
- **AND** the empty description does not say that request logs match the current filters

#### Scenario: Request logs filtered empty copy

- **GIVEN** the request-log listing has no rows
- **AND** at least one request-log filter differs from its default
- **WHEN** the recent-requests table renders
- **THEN** the empty copy describes that no request logs match the current filters

### Requirement: Dashboard empty accounts include a CTA to Accounts

The dashboard empty-account cards and list SHALL include a control that
navigates to `/accounts` when the overview has no accounts.

#### Scenario: Empty account cards link to Accounts

- **GIVEN** the dashboard overview has no accounts
- **WHEN** the account cards empty state renders
- **THEN** the empty state includes a link to `/accounts`

#### Scenario: Empty account list links to Accounts

- **GIVEN** the dashboard overview has no accounts
- **WHEN** the account list empty state renders
- **THEN** the empty state includes a link to `/accounts`

### Requirement: Reports line charts show no-data when daily rows are absent

When a Reports line chart receives an empty daily-row array, it SHALL render
a no-data empty state and SHALL NOT render a continuous zero-filled series
for the selected date range. When the daily-row array has at least one row,
the chart MAY still fill missing days with zeros.

#### Scenario: Empty daily payload hides the zero-line chart

- **GIVEN** `GET /api/reports` returns no daily rows
- **WHEN** a visible Reports line chart renders
- **THEN** the chart card shows a no-data empty state
- **AND** it does not render an area or line series of zero values

#### Scenario: Partial daily payload still fills missing days

- **GIVEN** a Reports line chart receives daily rows for some days in the selected range
- **WHEN** the chart renders
- **THEN** missing days in that range are still filled with zero values

### Requirement: Legacy firewall route expands Advanced and targets the firewall section

The `/firewall` route SHALL redirect to `/settings?advanced=1#firewall`.
Opening Settings with `advanced=1` or hash `#firewall` SHALL expand the
Advanced settings group on first render so the firewall section mounts.
The firewall section SHALL expose `id="firewall"`. Opening `/settings`
without that query or hash SHALL keep Advanced collapsed by default.

#### Scenario: Legacy /firewall deeplink shows the firewall section

- **WHEN** an operator opens `/firewall`
- **THEN** the SPA navigates to `/settings?advanced=1#firewall`
- **AND** the Advanced settings group is expanded
- **AND** the firewall section heading is visible without a further expand click

#### Scenario: Plain Settings stays collapsed

- **WHEN** an operator opens `/settings` without `advanced=1` and without `#firewall`
- **THEN** the Advanced settings group remains collapsed
- **AND** the firewall section is not mounted
