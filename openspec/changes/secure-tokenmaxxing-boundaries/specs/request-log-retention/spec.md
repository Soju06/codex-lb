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
