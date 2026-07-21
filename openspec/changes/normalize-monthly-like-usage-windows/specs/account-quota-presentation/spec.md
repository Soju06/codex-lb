## ADDED Requirements

### Requirement: Observed monthly-only quota is plan-agnostic

Account-facing quota surfaces SHALL classify an authoritative observed window
between 28 and 32 days as monthly-only regardless of stored account plan.
They MUST NOT render that window as a synthetic 5h or weekly quota. A monthly
row for a plan without configured monthly capacity MUST be ignored only when a
meaningfully newer non-monthly standard window proves that the account has
moved to a different quota shape.

#### Scenario: Team 30-day quota renders as monthly

- **GIVEN** a Team account reports a 43800-minute primary window
- **AND** the secondary slot is a zero-duration placeholder
- **WHEN** the account summary is built
- **THEN** the summary exposes only the monthly quota window
- **AND** the remaining percentage and reset belong to that observed window

#### Scenario: New paid-plan windows supersede stale monthly history

- **GIVEN** an account has an older monthly row from a previous quota shape
- **AND** a non-monthly primary or secondary row is newer by more than the
  sibling-fetch margin
- **WHEN** the account summary is built
- **THEN** the stale monthly row is not presented
