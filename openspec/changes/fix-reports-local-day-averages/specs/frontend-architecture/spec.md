## ADDED Requirements

### Requirement: Reports per-day averages use the inclusive local calendar window

`GET /api/reports` MUST calculate `summary.avgCostPerDay` and
`summary.avgRequestsPerDay` by dividing the current report totals by exactly
`(end_date - start_date).days + 1`. The divisor MUST represent the selected
inclusive local calendar-date window and MUST NOT be derived from the
UTC-converted filter boundaries.

#### Scenario: Offset-to-zero transition keeps a two-day divisor

- **WHEN** an operator requests `2026-02-15` through `2026-02-16` in
  `Africa/Casablanca` and the report totals are 60 cost units and 30 requests
- **THEN** `avgCostPerDay` is `30`
- **AND** `avgRequestsPerDay` is `15`

#### Scenario: Offset-from-zero transition keeps a two-day divisor

- **WHEN** an operator requests `2026-03-22` through `2026-03-23` in
  `Africa/Casablanca` and the report totals are 60 cost units and 30 requests
- **THEN** `avgCostPerDay` is `30`
- **AND** `avgRequestsPerDay` is `15`
