## ADDED Requirements

### Requirement: Main usage ingestion normalizes semantic window duration

The background poller and live usage ingestor MUST apply the same semantic
normalization before persisting standard quota windows. A primary window from
28 through 32 days MUST be persisted in the monthly slot. A zero-duration,
zero-usage window without a reset deadline MUST be treated as a placeholder
and MUST NOT be persisted as a real standard quota window.

#### Scenario: Team monthly-like event includes a placeholder

- **GIVEN** an upstream usage snapshot contains a 43800-minute primary window
- **AND** a zero-duration, zero-usage secondary window without a reset
- **WHEN** either standard ingestion path processes the snapshot
- **THEN** it persists the primary measurement as monthly usage
- **AND** it does not persist the secondary placeholder
