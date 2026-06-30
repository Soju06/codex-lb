## ADDED Requirements

### Requirement: Quota planner decisions persist naive UTC instants

The quota phase planner SHALL normalize timezone-aware datetimes to naive UTC
before persisting them to the timezone-naive `QuotaPlannerDecision.scheduled_at`
and `executed_at` columns. When a planned or executed instant is timezone-aware,
the persisted column value MUST equal that instant converted to UTC with its
`tzinfo` removed, preserving the absolute instant. Naive datetimes MUST be
persisted unchanged. JSON audit snapshots MAY continue to record the same
instants as ISO-8601 strings that include a timezone offset.

#### Scenario: Aware planned instant is stored as naive UTC

- **GIVEN** the scheduler logs a decision with a timezone-aware UTC
  `scheduled_at`
- **WHEN** the repository persists the decision row
- **THEN** the stored `scheduled_at` is timezone-naive
- **AND** it equals the original instant expressed in UTC

#### Scenario: Aware executed instant is stored as naive UTC on update

- **GIVEN** a decision is updated with a timezone-aware UTC `executed_at`
- **WHEN** the repository writes the status update
- **THEN** the stored `executed_at` is timezone-naive
- **AND** it equals the original instant expressed in UTC

#### Scenario: Naive instants persist unchanged

- **GIVEN** a decision is logged or updated with a timezone-naive datetime
- **WHEN** the repository persists the value
- **THEN** the stored value is unchanged and remains timezone-naive
