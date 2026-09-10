## ADDED Requirements

### Requirement: Dashboard refresh cadence is operator-selectable

The dashboard MUST let the operator select a 5, 15, 30, or 60 second refresh
cadence. The selection MUST persist locally, default to 15 seconds, and apply to
both overview and projection queries without enabling background-tab polling.
Failed refreshes MUST retain the last successful query data.

#### Scenario: Refresh cadence updates live dashboard queries

- **GIVEN** the operator selects a 5-second dashboard refresh cadence
- **WHEN** the dashboard overview and projection queries are active
- **THEN** both queries poll every 5 seconds
- **AND** the preference survives a page reload
- **AND** a failed poll does not clear the last successful data
