## ADDED Requirements

### Requirement: Request metadata has bounded retention

The system SHALL hard-delete request-log records older than a configured positive
retention period. Cleanup MUST run automatically, MUST retain newer records, and
MUST be safe to run repeatedly. The Onda production configuration SHALL use 30
days and SHALL keep request/response payload and conversation archival disabled.

#### Scenario: Scheduled cleanup deletes only expired request logs

- **WHEN** cleanup runs with a 30-day retention period
- **THEN** request logs older than the cutoff are hard-deleted
- **AND** request logs at or newer than the cutoff remain
- **AND** a repeated cleanup remains successful

#### Scenario: Readiness allows cleanup interval grace

- **GIVEN** retention cleanup is enabled and its last successful run is still fresh
- **WHEN** the oldest retained request log is older than the nominal retention cutoff only because the next cleanup interval has not elapsed
- **THEN** readiness remains healthy
- **AND** readiness still fails when cleanup itself is unhealthy or retained rows exceed the cleanup grace window

#### Scenario: Non-leader readiness ignores stale local cleanup state

- **GIVEN** leader election is enabled and this replica is not currently responsible for cleanup
- **WHEN** its local last-success or error state was left by an earlier leadership term
- **THEN** readiness does not fail on that stale local cleanup state
- **AND** the current cleanup leader remains responsible for retention health
