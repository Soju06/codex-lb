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
