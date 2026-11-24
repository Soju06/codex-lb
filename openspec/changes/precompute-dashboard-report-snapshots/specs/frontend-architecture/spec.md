## ADDED Requirements

### Requirement: Prepared analytical views distinguish cache state from data coverage
Dashboard and Reports SHALL consume published background results and display
freshness without starting measurement on page refresh. Cold preparation,
refresh failure, actual insufficient observations and unsupported historical
coverage SHALL be distinct states. Cached data for another window, date range,
filter or timezone SHALL NOT be relabeled as the requested view.

#### Scenario: A report refresh is still running
- **GIVEN** a complete same-key report snapshot
- **WHEN** its scheduled refresh is running or has failed
- **THEN** the page SHALL retain that result and show its age or delayed-update state

#### Scenario: A fresh snapshot is being refreshed
- **WHEN** a published snapshot is refreshing but is not stale and has no error
- **THEN** its update status SHALL remain visible in either Dashboard layout and Reports
- **AND** Reports SHALL retain the published as-of timestamp

#### Scenario: A cold dashboard is waiting between warming polls
- **WHEN** an overview request reports the retryable snapshot-warming state without data
- **THEN** the page SHALL keep its loading state between polls rather than show a generic failure
